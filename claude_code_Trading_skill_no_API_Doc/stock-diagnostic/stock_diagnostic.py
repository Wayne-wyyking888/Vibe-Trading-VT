#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单只A股「持仓诊断」引擎 — stock-diagnostic skill 的 Agent① 量化底座。

给定 股票代码 + 持仓成本价，直连东方财富/腾讯/新浪（免费、0 API key），拉取：
  · 个股快照（价/PE/PB/市值/行业/板块/换手/量比）
  · 160日前复权K线 → 趋势/动量/量能/技术(MACD/RSI/BOLL)/位置/ATR/支撑压力
  · 个股主力资金流（近N日主力净流入额/占比，超大单+大单）
  · 所属行业板块的同行对比（peer K线算20日相对强弱）
  · 大盘环境（上证/创业板/沪深300 涨跌 + 60日位 + 多空regime）
  · 成本相关测算（盈亏%/解套涨幅/补仓摊薄/关键价位地图/情景分析）
并用 IC 回测校准的 qlib 风格因子权重（≤24h 自动重跑，保证最新最predictive）给量化分。

这是纯数据、确定性引擎。Claude Code 在此之上扮演 5 个 agent：
  ① 技术·量价   ② 基本面·财务   ③ 消息·催化剂(中英文海内外)
  ④ 资金·板块·市场联动   ⑤ 风险挑战者·首席裁决官（综合裁定 + 操作建议）

复用同 repo 的 weekly-ashare-rank 引擎（数据客户端/因子/回测/交易日历），不重复造轮子。

用法:
  python stock_diagnostic.py --code 600519 --cost 1500
  python stock_diagnostic.py --code 600519 --cost 1500 --hold-days 20 --out C:/.../diag.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 复用周度引擎
HERE = pathlib.Path(__file__).resolve().parent


def _load_engine():
    """加载同 repo 的 weekly-ashare-rank 引擎（数据客户端/因子/回测/日历）。"""
    candidates = [
        HERE.parent / "weekly-ashare-rank" / "ashare_weekly_rank.py",
        HERE / "ashare_weekly_rank.py",
        pathlib.Path.home() / ".claude" / "skills" / "weekly-ashare-rank" / "ashare_weekly_rank.py",
    ]
    for p in candidates:
        if p.exists():
            spec = importlib.util.spec_from_file_location("ashare_eng", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError(
        "找不到 weekly-ashare-rank/ashare_weekly_rank.py（本引擎依赖它做数据客户端）。\n"
        "请确保 stock-diagnostic 与 weekly-ashare-rank 同在 claude_code_Trading_skill_no_API_Doc/ 下。")


eng = _load_engine()


def log(msg: str) -> None:
    sys.stderr.write(f"[diag] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 数据新鲜度守卫
def _session_now() -> str:
    """当前处于 A 股的哪个时段（中国时间）：盘前/盘中/午休/盘后/休市日。"""
    now = eng._cn_now()
    cal = eng._trade_calendar()
    today = now.date().isoformat()
    if cal and today not in cal:
        return "休市日"
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 15:
        return "盘前"
    if hm < 9 * 60 + 30:
        return "集合竞价"
    if hm < 11 * 60 + 30:
        return "盘中"
    if hm < 13 * 60:
        return "午休"
    if hm < 15 * 60:
        return "盘中"
    return "盘后"


def _expected_last_kline_date() -> str | None:
    """行情源此刻应有的最新K线日期：盘中=今天(未完成bar)，收盘后=今天，盘前=上一交易日。"""
    cal = eng._trade_calendar()
    if not cal:
        return None
    now = eng._cn_now()
    today = now.date().isoformat()
    past = [d for d in cal if d <= today]
    if not past:
        return None
    if past[-1] == today and now.hour * 60 + now.minute < 9 * 60 + 31:
        return past[-2] if len(past) >= 2 else None  # 还没开盘，最新完整bar=上一交易日
    return past[-1]


def _fresh_kline(code: str, bars: int = 260) -> pd.DataFrame | None:
    """get_kline + 新鲜度守卫：缓存K线落后于行情源应有日期时强制重拉（修复60h缓存
    导致盘中诊断用前一天均线/RSI 的口径错配）。"""
    h = eng.get_kline(code, bars=bars)
    exp = _expected_last_kline_date()
    if h is not None and exp and str(h["日期"].iloc[-1])[:10] < exp:
        log(f"  K线缓存滞后({h['日期'].iloc[-1]} < 应有{exp})，强制重拉 {code}")
        old = eng._REFRESH
        eng._REFRESH = True
        try:
            h2 = eng.get_kline(code, bars=bars)
        finally:
            eng._REFRESH = old
        if h2 is not None and len(h2) >= 25:
            h = h2
    return h


# ---------------------------------------------------------------- 个股快照（扩展字段）
# stock/get 字段（已 fltt=2，直接小数）：
_Q_FIELDS = ("f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,"
             "f127,f128,f162,f167,f168,f169,f170,f171")
_Q_MAP = {
    "f43": "price", "f44": "high", "f45": "low", "f46": "open", "f47": "vol_hand",
    "f48": "amount", "f50": "vol_ratio", "f57": "code", "f58": "name", "f60": "prevclose",
    "f116": "total_cap", "f117": "float_cap", "f127": "industry", "f128": "board",
    "f162": "pe", "f167": "pb", "f168": "turnover", "f169": "chg_amt",
    "f170": "chg_pct", "f171": "amplitude",
}


def _num(x):
    if x in (None, "-", "", "—"):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_quote(code: str) -> dict:
    """个股扩展快照。东财 stock/get → 腾讯兜底（名称/价/流通市值）。"""
    out = {"code": code}
    try:
        data = eng._get(eng._PUSH_HOSTS, "/api/qt/stock/get",
                        {"secid": eng._secid(code), "fields": _Q_FIELDS,
                         "fltt": 2, "invt": 2}, retries=3).get("data")
    except Exception as e:  # noqa: BLE001
        log(f"  东财个股快照失败: {str(e)[:60]}")
        data = None
    if data:
        for k, name in _Q_MAP.items():
            v = data.get(k)
            out[name] = v if name in ("code", "name", "industry", "board") else _num(v)
    # 腾讯兜底名称/价/流通市值（盘前实时归零或东财限流时）
    if not out.get("name") or not out.get("price"):
        q = eng._tx_quote([code]).get(code, {})
        out.setdefault("name", q.get("name", code))
        if not out.get("price"):
            out["price"] = q.get("price")
        if not out.get("float_cap"):
            out["float_cap"] = q.get("float_cap")
        if not out.get("total_cap"):
            out["total_cap"] = q.get("total_cap")
    out["name"] = out.get("name") or code
    return out


# ---------------------------------------------------------------- 主力资金流
# 列序(均): 日期, 主力净, 小单, 中单, 大单, 超大单, [主力占比%,..,收盘,涨跌幅%]
# 校验过: 大单+超大单 = 主力；小单+中单 = -主力。
_FFLOW_F2 = ("f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")
_FFLOW_HIS_HOSTS = [
    "https://push2his.eastmoney.com", "https://1.push2his.eastmoney.com",
    "https://13.push2his.eastmoney.com", "https://7.push2his.eastmoney.com",
    "https://82.push2his.eastmoney.com",
]


def _summarize_flow(rows: list[dict], source: str, partial: bool = False,
                    caliber_warn: str | None = None) -> dict:
    main_5 = round(sum(r["main_yi"] for r in rows[-5:] if r["main_yi"] is not None), 2)
    main_all = round(sum(r["main_yi"] for r in rows if r["main_yi"] is not None), 2)
    ratios = [r["main_ratio"] for r in rows[-5:] if r.get("main_ratio") is not None]
    n5 = len([r for r in rows[-5:] if r["main_yi"] is not None])
    pos_days = sum(1 for r in rows[-5:] if (r["main_yi"] or 0) > 0)
    if partial:
        trend = ("净流入" if main_5 > 0 else "净流出") + "(仅最新日·历史源限流)"
    else:
        trend = ("净流入" if main_5 > 0 else "净流出") + f"(近{n5}日{pos_days}/{n5}天为正)"
    if caliber_warn:
        trend += f" ⚠{caliber_warn}"
    return {"available": True, "source": source, "partial": partial,
            "caliber_warn": caliber_warn,
            "main_net_5d_yi": main_5, "main_net_all_yi": main_all,
            "main_ratio_avg5": round(float(np.mean(ratios)), 2) if ratios else None,
            "pos_days_5": pos_days, "trend": trend, "days": rows}


def _sina_fund_flow(code: str, days: int = 10) -> list[dict] | None:
    """新浪历史资金流(独立于东财, host=vip.stock.finance.sina.com.cn)。
    ⚠ 新浪「主力净」的tick分类口径与东财不同：实测同票同日数值差均值~1.7亿、
    个别日净流入/流出符号相反，**不可与东财数值或上次东财口径诊断直接比较**，
    仅当东财历史源限流时用其『自洽5日序列』看资金趋势方向。"""
    daima = ("sh" if code.startswith(("60", "68", "9", "5")) else "sz") + code
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={max(days, 6)}&sort=opendate&asc=0&daima={daima}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for r in (data or []):
        nv = _num(r.get("netamount"))
        ra = _num(r.get("ratioamount"))
        ch = _num(r.get("changeratio"))
        rows.append({"date": r.get("opendate"),
                     "main_yi": round(nv / 1e8, 3) if nv is not None else None,
                     "main_ratio": round(ra * 100, 2) if ra is not None else None,
                     "chg": round(ch * 100, 2) if ch is not None else None})
    rows = [r for r in rows if r["date"]][::-1]  # 新浪降序→转升序(与东财一致:最新在末)
    return rows[-days:] or None


def fetch_fund_flow(code: str, days: int = 10) -> dict:
    """个股主力资金流。优先级：当日缓存 → 东财历史日线(含占比,push2his)
    → 新浪历史(独立源,⚠口径异于东财,仅趋势参考) → 东财实时单日(仅当日)。
    缓存当日东财历史结果(TTL 12h)以降低对 push2his 的重复打点、减少被软封概率。"""
    secid = eng._secid(code)
    ckey = f"{code}_{days}"
    cached = eng._cache_get("fundflow", ckey, max_age_h=12)
    if cached is not None and cached.get("source") == "东财历史日线":
        log("  资金流命中当日缓存(东财历史)")
        return cached
    # 1) 历史日线（push2his）：完整 N 日 + 占比（主源，与上次诊断口径一致）
    # 批量场景优化(2026-07-07)：走引擎全局熔断器——东财一旦限流，后续标的直接跳到新浪，
    # 不再每只死磕整套指数退避(旧版限流时每只白等~25秒)。fast=True 短超时短退避。
    _brk_open = getattr(eng, "_breaker_open", None)
    if _brk_open and _brk_open("em_fflow"):
        log("  资金流东财源熔断中，直接走新浪独立源")
    else:
        try:
            data = eng._get(_FFLOW_HIS_HOSTS, "/api/qt/stock/fflow/daykline/get",
                            {"lmt": 90, "klt": 101, "secid": secid,
                             "fields1": "f1,f2,f3,f7", "fields2": _FFLOW_F2},
                            retries=3, fast=True).get("data")
            kl = (data or {}).get("klines") or []
            rows = []
            for ln in kl[-days:]:
                a = ln.split(",")
                if len(a) < 13:
                    continue
                mv = _num(a[1])
                rows.append({"date": a[0], "main_yi": round(mv / 1e8, 3) if mv is not None else None,
                             "main_ratio": _num(a[6]), "chg": _num(a[12])})
            if rows:
                res = _summarize_flow(rows, source="东财历史日线")
                eng._cache_put("fundflow", ckey, res)
                return res
        except Exception as e:  # noqa: BLE001
            if getattr(eng, "_breaker_trip", None):
                eng._breaker_trip("em_fflow")
            log(f"  资金流东财历史源限流({str(e)[:40]})，尝试新浪独立源")
    # 2) 新浪历史（独立 host，完整 N 日 + 占比，但口径≠东财，强标注）
    srows = _sina_fund_flow(code, days)
    if srows:
        log("  资金流回退新浪历史(口径异于东财,仅趋势参考)")
        return _summarize_flow(srows, source="新浪历史(独立源)",
                               caliber_warn="新浪口径≠东财,仅看5日趋势方向,勿与东财数值/上次诊断比较")
    # 3) 实时单日（东财 push2，不同主机，仅当日）
    try:
        data = eng._get(eng._PUSH_HOSTS, "/api/qt/stock/fflow/kline/get",
                        {"lmt": 1, "klt": 101, "secid": secid,
                         "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56"},
                        retries=4).get("data")
        kl = (data or {}).get("klines") or []
        if kl:
            a = kl[-1].split(",")
            mv = _num(a[1])
            if mv is not None:
                rows = [{"date": a[0], "main_yi": round(mv / 1e8, 3),
                         "main_ratio": None, "chg": None}]
                return _summarize_flow(rows, source="东财实时单日", partial=True)
    except Exception as e:  # noqa: BLE001
        log(f"  资金流实时源也失败: {str(e)[:50]}")
    return {"available": False, "days": []}


# ---------------------------------------------------------------- F10 事件面（解禁/质押/业绩预告/股东户数/两融）
_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DC_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _dc_get(report: str, filt: str, sort: str = "", ps: int = 20) -> list[dict]:
    """东财数据中心 datacenter-web 通用查询（免费、无 key）。失败返回 []。"""
    p = {"reportName": report, "columns": "ALL", "filter": filt,
         "pageSize": str(ps), "pageNumber": "1", "source": "WEB", "client": "WEB"}
    if sort:
        p["sortColumns"], p["sortTypes"] = sort.split(":")
    req = urllib.request.Request(_DC_URL + "?" + urllib.parse.urlencode(p), headers=_DC_UA)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            obj = json.load(resp)
        return ((obj.get("result") or {}).get("data")) or []
    except Exception as e:  # noqa: BLE001
        log(f"  datacenter {report} 失败: {str(e)[:50]}")
        return []


def fetch_f10_extras(code: str, hold_days: int, float_cap_yi: float | None) -> dict:
    """客观事件面数据（替代 WebSearch 记忆）：未来解禁、质押比例、最近业绩预告、
    股东户数变化、两融余额。每项独立容错；并产出 事件风险加点(risk_bump) + 理由。"""
    out: dict = {"available": True}
    today = eng._cn_now().date()   # 北京日期(本机时区不可信,P12)
    risks: list[str] = []
    notes: list[str] = []
    bump = 0
    flt = f'(SECURITY_CODE="{code}")'

    # 1) 未来解禁（按解禁日升序取未来批次）
    lift_rows = _dc_get("RPT_LIFT_STAGE", flt, sort="FREE_DATE:-1", ps=40)
    fut = []
    for r in lift_rows:
        d = str(r.get("FREE_DATE", ""))[:10]
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        if dd >= today:
            ratio = r.get("TOTAL_RATIO") or r.get("FREE_RATIO")
            fut.append({"date": d, "days_to": (dd - today).days,
                        "ratio_pct": round(float(ratio), 2) if ratio is not None else None,
                        "type": r.get("FREE_SHARES_TYPE"),
                        "cap_yi": round(float(r["LIFT_MARKET_CAP"]) / 1e8, 2)
                        if r.get("LIFT_MARKET_CAP") else None})
    fut.sort(key=lambda x: x["days_to"])
    out["lift"] = fut[:3]
    if fut:
        nx = fut[0]
        rt = nx.get("ratio_pct") or 0
        if nx["days_to"] <= max(30, hold_days * 2) and rt >= 1:
            add = 12 if rt >= 5 else 8
            bump += add
            risks.append(f"{nx['date']}解禁{rt}%({nx['type']})")
        elif nx["days_to"] <= 90:
            notes.append(f"{nx['date']}有解禁({_d(rt,'%')})")

    # 2) 质押比例（中证登周频）
    pl = _dc_get("RPT_CSDC_LIST", flt, sort="TRADE_DATE:-1", ps=1)
    out["pledge"] = None
    if pl:
        ratio = pl[0].get("PLEDGE_RATIO")
        out["pledge"] = {"ratio_pct": round(float(ratio), 2) if ratio is not None else None,
                         "date": str(pl[0].get("TRADE_DATE", ""))[:10]}
        if ratio is not None:
            if float(ratio) >= 50:
                bump += 12; risks.append(f"质押比例{round(float(ratio),1)}%极高")
            elif float(ratio) >= 30:
                bump += 6; risks.append(f"质押比例{round(float(ratio),1)}%偏高")

    # 3) 最近业绩预告（仅12个月内的才计入风险）
    pf = _dc_get("RPT_PUBLIC_OP_NEWPREDICT", flt, sort="NOTICE_DATE:-1", ps=1)
    out["forecast"] = None
    if pf:
        r0 = pf[0]
        nd = str(r0.get("NOTICE_DATE", ""))[:10]
        out["forecast"] = {"notice_date": nd, "report_date": str(r0.get("REPORT_DATE", ""))[:10],
                           "type": r0.get("PREDICT_TYPE"), "content": r0.get("PREDICT_CONTENT"),
                           "chg_lo": r0.get("ADD_AMP_LOWER"), "chg_hi": r0.get("ADD_AMP_UPPER")}
        try:
            recent = (today - dt.date.fromisoformat(nd)).days <= 370
        except ValueError:
            recent = False
        tp = str(r0.get("PREDICT_TYPE") or "")
        if recent and any(k in tp for k in ("预亏", "首亏", "续亏")):
            bump += 15; risks.append(f"业绩预告{tp}({nd})")
        elif recent and any(k in tp for k in ("预减", "略减")):
            bump += 8; risks.append(f"业绩预告{tp}({nd})")

    # 4) 股东户数变化（筹码集中/分散）
    hn = _dc_get("RPT_HOLDERNUM_DET", flt, sort="END_DATE:-1", ps=1)
    out["holders"] = None
    if hn:
        r0 = hn[0]
        chg = r0.get("HOLDER_NUM_RATIO")
        out["holders"] = {"end_date": str(r0.get("END_DATE", ""))[:10],
                          "num": r0.get("HOLDER_NUM"),
                          "chg_pct": round(float(chg), 2) if chg is not None else None}
        if chg is not None and float(chg) >= 10:
            notes.append(f"股东户数+{round(float(chg),1)}%(筹码分散)")
        elif chg is not None and float(chg) <= -8:
            notes.append(f"股东户数{round(float(chg),1)}%(筹码集中)")

    # 5) 两融（融资余额与5/10日净买入）
    mg = _dc_get("RPTA_WEB_RZRQ_GGMX", f'(scode="{code}")', sort="date:-1", ps=1)
    out["margin"] = None
    if mg:
        r0 = mg[0]
        def _yi(k):
            v = r0.get(k)
            return round(float(v) / 1e8, 2) if v is not None else None
        out["margin"] = {"date": str(r0.get("DATE", ""))[:10], "rzye_yi": _yi("RZYE"),
                         "rz_net5d_yi": _yi("RZJME5D"), "rz_net10d_yi": _yi("RZJME10D"),
                         "rzye_ratio_pct": round(float(r0["RZYEZB"]), 2) if r0.get("RZYEZB") is not None else None}
        n5 = out["margin"]["rz_net5d_yi"]
        if n5 is not None:
            notes.append(f"融资5日净{'买入' if n5 >= 0 else '偿还'}{abs(n5)}亿")

    got = any((out.get("lift"), out.get("pledge"), out.get("forecast"),
               out.get("holders"), out.get("margin")))
    out["available"] = bool(got)
    out["event_risks"] = risks
    out["event_notes"] = notes
    out["risk_bump"] = min(30, bump)
    return out


# ---------------------------------------------------------------- 扩展技术面
def extra_technicals(h: pd.DataFrame) -> dict:
    """在 hist_factors 之外补：RSI14 / BOLL%b / 多周期位置 / 支撑压力 / 60·120·250日位。"""
    close, high, low = h["收盘"], h["最高"], h["最低"]
    last = float(close.iloc[-1])

    # RSI14（Wilder 近似用简单均值）
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if rs.iloc[-1] == rs.iloc[-1] else None

    # BOLL(20,2) 的 %b
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    up, lo = mid + 2 * std, mid - 2 * std
    width = float((up.iloc[-1] - lo.iloc[-1]))
    pctb = round((last - float(lo.iloc[-1])) / width, 2) if width > 0 else None
    boll_width_pct = round(width / float(mid.iloc[-1]) * 100, 1) if mid.iloc[-1] else None

    # 多周期位置
    def pos(win):
        seg_l = float(low.iloc[-win:].min()) if len(low) >= 5 else float(low.min())
        seg_h = float(high.iloc[-win:].max()) if len(high) >= 5 else float(high.max())
        return round((last - seg_l) / (seg_h - seg_l) * 100, 1) if seg_h > seg_l else 50.0

    pos60, pos120, pos250 = pos(60), pos(min(120, len(close))), pos(min(250, len(close)))
    ret60 = round(last / float(close.iloc[-61]) * 100 - 100, 2) if len(close) > 61 else None
    hi250 = float(high.iloc[-min(250, len(high)):].max())
    lo250 = float(low.iloc[-min(250, len(low)):].min())

    # 支撑/压力：MA + 近20/60日高低 + 整数关；取价上方最近压力、价下方最近支撑
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
    lo20 = float(low.iloc[-20:].min())
    hi20 = float(high.iloc[-20:].max())
    hi60 = float(high.iloc[-60:].max()) if len(high) >= 60 else hi20

    sup_cands = [x for x in (ma10, ma20, ma60, lo20, lo250) if x and x < last * 0.999]
    res_cands = [x for x in (ma5, ma10, hi20, hi60, hi250) if x and x > last * 1.001]
    near_support = round(max(sup_cands), 2) if sup_cands else round(last * 0.95, 2)
    near_resist = round(min(res_cands), 2) if res_cands else round(last * 1.05, 2)

    return {"rsi14": round(rsi, 1) if rsi is not None else None,
            "boll_pctb": pctb, "boll_width_pct": boll_width_pct,
            "pos60": pos60, "pos120": pos120, "pos250": pos250, "ret60": ret60,
            "hi250": round(hi250, 2), "lo250": round(lo250, 2),
            "ma60": round(ma60, 2),
            "near_support": near_support, "near_resist": near_resist,
            "off_hi250_pct": round(last / hi250 * 100 - 100, 1) if hi250 else None}


# ---------------------------------------------------------------- 大盘环境
_INDEX = {"上证指数": "1.000001", "深证成指": "0.399001",
          "创业板指": "0.399006", "沪深300": "1.000300"}


def fetch_index_context() -> dict:
    """大盘：各指数今日涨跌 + 是否站上MA20(多空regime) + 60日位。"""
    out = {}
    risk_on = 0
    for name, secid in _INDEX.items():
        try:
            d = eng._get(eng._PUSH_HOSTS, "/api/qt/stock/get",
                         {"secid": secid, "fields": "f43,f58,f60,f170", "fltt": 2, "invt": 2},
                         retries=2).get("data") or {}
            chg = _num(d.get("f170"))
            code = secid.split(".")[1]
            h = _fresh_kline(code, bars=160)
            above_ma20, pos60 = None, None
            if h is not None and len(h) >= 20:
                c = h["收盘"]
                ma20 = float(c.rolling(20).mean().iloc[-1])
                last = float(c.iloc[-1])
                above_ma20 = bool(last > ma20)
                win = min(60, len(c))
                lo = float(h["最低"].iloc[-win:].min())
                hi = float(h["最高"].iloc[-win:].max())
                pos60 = round((last - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0
                if above_ma20:
                    risk_on += 1
            out[name] = {"chg": chg, "above_ma20": above_ma20, "pos60": pos60}
            time.sleep(0.1)
        except Exception as e:  # noqa: BLE001
            log(f"  指数 {name} 失败: {str(e)[:40]}")
            out[name] = {"chg": None, "above_ma20": None, "pos60": None}
    regime = "偏多(多数指数站上MA20)" if risk_on >= 3 else (
        "中性" if risk_on == 2 else "偏空(多数指数破MA20)")
    return {"indices": out, "regime": regime, "risk_on_count": risk_on}


# ---------------------------------------------------------------- 同行对比
_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"


def _clean_industry(s: str) -> str:
    """清洗行业名：去掉申万式罗马数字后缀(白酒Ⅱ→白酒)、空白。"""
    if not s:
        return ""
    return "".join(ch for ch in s if ch not in _ROMAN).strip()


def _match_board(*names: str) -> tuple[str, str] | None:
    """在东财行业/概念板块里匹配名称，返回 (板块名, BK代码)。优先精确含子串。"""
    bmap = eng._board_name_to_code()
    if not bmap:
        return None
    cands = [_clean_industry(n) for n in names if n]
    for c in cands:
        if not c:
            continue
        # 精确等于优先
        for name, bk in bmap.items():
            if name == c:
                return name, bk
        # 双向子串
        for name, bk in bmap.items():
            if c and (c in name or name in c):
                return name, bk
    return None


def fetch_peers(industry: str, board: str, target_code: str, target_chg: float | None = None,
                max_peers: int = 8) -> dict:
    """所属行业/板块同行：匹配东财板块→直接拉成分股→取成交额前 max_peers→算20日相对强弱。
    匹配不到板块就标 available=False（不回退全市场，避免把全市场龙头误当同行）。"""
    m = _match_board(industry, board)
    if not m:
        log(f"  未匹配到『{industry}/{board}』对应东财板块，同行对比交给 Agent④ WebSearch")
        return {"available": False, "reason": "板块未匹配", "peers": []}
    sector_name, bk = m
    try:
        rows = eng._clist_top(f"b:{bk}", eng._SPOT_FIELDS, fid="f6", pz=1000)
    except Exception as e:  # noqa: BLE001
        log(f"  板块成分拉取失败 {sector_name}({bk}): {str(e)[:50]}")
        return {"available": False, "reason": "成分拉取失败", "peers": []}
    if not rows:
        return {"available": False, "reason": "成分为空", "peers": []}
    spot = pd.DataFrame(rows).rename(columns=eng._COL_MAP).drop_duplicates("代码")
    spot["成交额"] = pd.to_numeric(spot["成交额"], errors="coerce")
    spot = spot.sort_values("成交额", ascending=False)
    codes = [str(c) for c in spot["代码"].tolist() if str(c) != target_code]
    # 本股必须保住头名位置，不能被成交额排序裁掉（否则强弱排名缺本股）
    codes = [target_code] + codes[:max_peers]
    peers = []
    name_map = dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    chg_map = dict(zip(spot["代码"].astype(str),
                       pd.to_numeric(spot["涨跌幅"], errors="coerce")))
    for c in codes:
        h = eng.get_kline(c)
        time.sleep(0.12)
        if h is None or len(h) < 21:
            continue
        cl = h["收盘"]
        last = float(cl.iloc[-1])
        ret20 = round(last / float(cl.iloc[-21]) * 100 - 100, 2)
        ret5 = round(last / float(cl.iloc[-6]) * 100 - 100, 2) if len(cl) > 6 else None
        cv = chg_map.get(c)
        chg = round(float(cv), 2) if cv is not None and cv == cv else (
            round(float(target_chg), 2) if c == target_code and target_chg is not None else None)
        peers.append({"code": c, "name": name_map.get(c, c), "price": round(last, 2),
                      "chg": chg, "ret5": ret5, "ret20": ret20,
                      "is_target": c == target_code})
    if not peers:
        return {"available": False, "reason": "K线不足", "peers": []}
    r20s = [p["ret20"] for p in peers if p["ret20"] is not None]
    sect_avg20 = round(float(np.mean(r20s)), 2) if r20s else None
    # 目标在同行中的20日强弱排名
    ranked = sorted([p for p in peers if p["ret20"] is not None],
                    key=lambda x: -x["ret20"])
    rank = next((i for i, p in enumerate(ranked, 1) if p["is_target"]), None)
    return {"available": True, "sector_name": sector_name, "sect_avg_ret20": sect_avg20,
            "target_rank": rank, "n": len(ranked), "peers": peers}


# ---------------------------------------------------------------- 权重校准（≤24h）
def _sign_adjust_weights(weights: dict | None, ic_rows: list[dict]) -> tuple[dict | None, str]:
    """修正 |IC| 配桶的方向盲区：某桶因子的**带符号**平均 IC ≤ 0（负向预测，
    如尾盘强→未来反而跌的均值回归期），该桶权重不应被 |IC| 抬高 → 压到 ≤0.8。
    返回 (修正后权重, 说明)。"""
    if not weights or not ic_rows:
        return weights, ""
    try:
        icm = {r["factor"]: r.get("IC") for r in ic_rows if r.get("IC") is not None}
        buckets = getattr(eng, "FACTOR_BUCKETS", {})
        out = dict(weights)
        demoted = []
        for b, cols in buckets.items():
            vals = [icm[c] for c in cols if c in icm]
            if vals and float(np.mean(vals)) < 0 and out.get(b, 0) > 0.8:
                out[b] = 0.8
                demoted.append(b)
        note = f"；负IC桶降权:{'/'.join(demoted)}" if demoted else ""
        return out, note
    except Exception:  # noqa: BLE001
        return weights, ""


def ensure_weights(sector: str | None, fwd: int, sample: int = 50,
                   max_age_h: float = 24.0) -> tuple[dict | None, dict]:
    """qlib 风格因子 IC 权重：≤24h 自动重跑 + 符号修正。返回 (weights, meta)。"""
    wp = HERE / "weights.json"
    meta = {"calibrated": False, "age_h": None, "note": ""}
    if wp.exists():
        try:
            obj = json.loads(wp.read_text(encoding="utf-8"))
            ts = dt.datetime.fromisoformat(obj.get("generated_at", "2000-01-01"))
            if ts.tzinfo is None:             # 旧权重文件是裸本机时钟写的：补齐本机时区再比较
                ts = ts.astimezone()
            age_h = (eng._cn_now() - ts).total_seconds() / 3600
            meta["age_h"] = round(age_h, 1)
            if obj.get("fwd_days") == fwd and age_h <= max_age_h:
                w, sn = _sign_adjust_weights(obj.get("weights"), obj.get("ic") or [])
                meta["note"] = f"复用{round(age_h,1)}h前权重(fwd={fwd}){sn}"
                log(meta["note"])
                return w, meta
        except Exception:  # noqa: BLE001
            pass
    # 重跑回测刷新（≤24h 纪律）
    log(f"校准 qlib 因子权重（IC回测, fwd={fwd}, 样本≤{sample}, 因超24h或不匹配）...")
    try:
        bt = eng.backtest(sector if sector else None, sample, fwd=fwd)
        wp.write_text(json.dumps(bt, ensure_ascii=False, indent=2), encoding="utf-8")
        w, sn = _sign_adjust_weights(bt.get("weights"), bt.get("ic") or [])
        meta.update(calibrated=True, age_h=0.0,
                    note=f"已重跑IC回测刷新权重(fwd={fwd}, 样本{bt.get('n_stocks')}){sn}")
        log(meta["note"])
        return w, meta
    except Exception as e:  # noqa: BLE001
        log(f"  权重校准失败({str(e)[:50]})，用持有天数自适应默认权重")
        meta["note"] = "校准失败，用默认权重"
        return None, meta


# ---------------------------------------------------------------- 成本相关 + 裁决底座
def cost_analysis(price: float, cost: float | None, atr: float,
                  near_support: float, near_resist: float, ma20: float,
                  hold_days: int, trend: str = "中性") -> dict:
    """成本相关测算 + 关键价位地图 + 情景分析。trend ∈ {上行,中性,下行}（影响基准情景漂移）。"""
    out = {}
    k = max(2.0, hold_days * 0.4)
    bull = round(max(price + k * atr, near_resist), 2)
    bear = round(min(price - k * atr, near_support), 2)
    # 基准情景漂移随趋势定方向（旧版恒为正漂移，下跌趋势里会误导）
    drift = {"上行": 0.15, "中性": 0.0, "下行": -0.12}.get(trend, 0.0)
    base = round(price + drift * k * atr, 2)
    out["scenario"] = {
        "bull": {"price": bull, "from_now": round(bull / price * 100 - 100, 1)},
        "base": {"price": base, "from_now": round(base / price * 100 - 100, 1)},
        "bear": {"price": bear, "from_now": round(bear / price * 100 - 100, 1)},
    }
    # 止损：ATR 倍数随持有视野缩放（短视野没必要扛 1.8×ATR 的回撤）；
    # 与结构支撑取较紧者；最少留 1.2×ATR 防噪（否则日内波动就打掉）。
    atr_mult = 1.2 if hold_days <= 5 else (1.5 if hold_days <= 10 else 1.8)
    atr_stop = price - atr_mult * atr
    stop = round(min(near_support * 0.99, atr_stop) if near_support < price else atr_stop, 2)
    stop = min(stop, round(price * 0.97, 2))
    stop = max(stop, round(price - 3.0 * atr, 2))  # 不给离谱的超宽止损
    add_zone = [round(min(near_support, ma20) * 0.995, 2), round(min(price, ma20), 2)]
    # 自检：止损必须低于加仓区下沿（在加仓区里被止损=逻辑矛盾）
    map_warnings = []
    if stop >= add_zone[0]:
        stop = round(add_zone[0] * 0.985, 2)
        map_warnings.append("止损已下调至加仓区下沿之下(原ATR止损落在加仓区内)")
    if round(ma20, 2) > near_resist:
        map_warnings.append("MA20在压力位上方(价格被均线下压)，地图非单调，谨慎做多")
    if add_zone[1] >= price * 0.995:
        map_warnings.append("加仓区上沿≈现价：等于现价买入，不是回踩低吸")
    # R:R（按看多目标/止损），<1.5 不值得在现价加仓
    rr = round((bull - price) / max(price - stop, 1e-9), 2)
    out["price_map"] = {
        "key_support": near_support, "key_resist": near_resist,
        "stop": stop, "stop_atr_mult": atr_mult, "ma20": round(ma20, 2),
        "add_zone": add_zone,
        "take_profit": [near_resist, bull],
        "rr": rr,
        "rr_note": ("现价R:R≥1.5,可持有/择机加" if rr >= 1.5 else "现价R:R<1.5,不宜现价加仓"),
    }
    out["map_warnings"] = map_warnings
    if cost is None:
        out["cost"] = None
        return out
    pnl = (price / cost - 1) * 100
    status = ("浮盈" if pnl > 0.5 else ("浮亏" if pnl < -0.5 else "持平"))
    if pnl <= -15:
        status = "深套"
    breakeven_rally = round((cost / price - 1) * 100, 1) if price < cost else 0.0
    # 等量补仓后新成本与解套涨幅
    new_cost = round((cost + price) / 2, 2)
    new_breakeven_rally = round((new_cost / price - 1) * 100, 1) if price < new_cost else 0.0
    out["cost"] = {
        "cost": cost, "pnl_pct": round(pnl, 2), "status": status,
        "breakeven_price": cost, "breakeven_rally_pct": breakeven_rally,
        "cost_vs_price": ("成本在现价上方(套牢)" if cost > price else "成本在现价下方(浮盈)"),
        "cost_vs_ma20": ("成本在MA20上方" if cost > ma20 else "成本在MA20下方"),
        "avg_down_equal": {"new_cost": new_cost, "new_breakeven_rally_pct": new_breakeven_rally},
        "to_bull_from_cost": round(bull / cost * 100 - 100, 1),
        "to_bear_from_cost": round(bear / cost * 100 - 100, 1),
    }
    return out


def engine_verdict(f: dict, q: dict, tech: dict, flow: dict, cost: dict | None,
                   index_ctx: dict, peers: dict, f10: dict | None = None) -> dict:
    """引擎基线裁决：量价stance(0-100, 仅技术/资金/联动，不含基本面消息) + 持仓健康度
    + 建议动作（结合成本）。这是 Agent⑤ 的量化底座，可被基本面/消息面改写。"""
    stance = 50.0
    notes = []
    # 趋势
    if f.get("bull"):
        stance += 12; notes.append("MA多头排列")
    price = f["close"]
    if price > f.get("ma20", price):
        stance += 6
    else:
        stance -= 8; notes.append("跌破MA20")
    if price > tech.get("ma60", price):
        stance += 4
    else:
        stance -= 5
    # 动量
    ret20 = f.get("ret20") or 0
    stance += max(-12, min(12, ret20 * 0.6))
    if f.get("macd_gold"):
        stance += 5
    else:
        stance -= 3
    # RSI：70 以上渐进降温（旧版 78 才扣分，70-78 的过热区一分不扣 → stance 虚高）
    rsi = tech.get("rsi14")
    if rsi is not None:
        if rsi > 78:
            stance -= 6 + (rsi - 78) * 0.4; notes.append(f"RSI{rsi:.0f}超买")
        elif rsi > 68:
            stance -= (rsi - 68) * 0.6
        elif rsi < 30:
            stance += 6; notes.append(f"RSI{rsi:.0f}超卖(反弹)")
    # 位置：85 以上渐进降温
    p60 = tech.get("pos60", 50)
    if p60 > 92:
        stance -= 8 + (p60 - 92) * 0.3; notes.append("60日高位")
    elif p60 > 85:
        stance -= (p60 - 85) * 0.5
    elif p60 < 18:
        stance += 4; notes.append("60日低位")
    # 资金流（新浪兜底口径≠东财 或 仅当日partial → 降权为"方向参考"，幅度减半封顶±6）
    if flow.get("available"):
        m5 = flow.get("main_net_5d_yi") or 0
        soft = bool(flow.get("caliber_warn")) or bool(flow.get("partial"))
        cap, base = (6, 2) if soft else (12, 4)
        tag = ("(新浪口径·方向参考)" if flow.get("caliber_warn")
               else "(仅当日)" if flow.get("partial") else "")
        if m5 > 0:
            stance += min(cap, base + m5 * 0.8); notes.append(f"主力5日净流入{m5}亿{tag}")
        else:
            stance += max(-cap, -base + m5 * 0.8); notes.append(f"主力5日净流出{m5}亿{tag}")
    # 同行相对强弱
    if peers.get("available") and peers.get("target_rank") and peers.get("n"):
        pr = peers["target_rank"] / peers["n"]
        if pr <= 0.34:
            stance += 4; notes.append("同行强于多数")
        elif pr >= 0.75:
            stance -= 4; notes.append("同行偏弱")
    # 大盘环境
    if index_ctx.get("risk_on_count", 0) >= 3:
        stance += 3
    elif index_ctx.get("risk_on_count", 0) <= 1:
        stance -= 4; notes.append("大盘偏空")
    # 风险分折抵
    stance -= (f.get("risk_score", 0)) * 0.08
    stance = max(0.0, min(100.0, stance))

    # 持仓健康度 ≠ stance：stance 是量价多空，健康度还要吃风险分（技术+事件）
    event_bump = (f10 or {}).get("risk_bump", 0)
    total_risk = min(100, f.get("risk_score", 0) + event_bump)
    health = 0.7 * stance + 0.3 * (100 - total_risk)
    if event_bump:
        notes.append(f"事件风险+{event_bump}({'/'.join((f10 or {}).get('event_risks', [])[:2])})")
    overheat = (p60 > 92) or (rsi is not None and rsi > 80)
    downtrend = (not f.get("bull")) and (price < f.get("ma20", price))
    pnl = cost["pnl_pct"] if cost else None

    # 动作映射（结合成本）
    if stance >= 68:
        if overheat and pnl is not None and pnl > 15:
            action = "持有偏减(逢高止盈一部分)"
        else:
            action = "持有/逢低加仓"
    elif stance >= 55:
        action = "持有观察"
    elif stance >= 42:
        if pnl is not None and pnl > 5:
            action = "减仓锁利"
        elif downtrend:
            action = "逢反弹减仓/控制仓位"
        else:
            action = "持有观察(偏谨慎)"
    else:
        action = "止损/清仓为主" if downtrend else "减仓为主"

    # 深套保护：下跌趋势+资金流出 → 不补仓；趋势转好才可分批摊薄
    avg_down_ok = False
    if pnl is not None and pnl <= -15:
        m5 = flow.get("main_net_5d_yi") or 0 if flow.get("available") else 0
        if (not downtrend) and m5 > 0 and f.get("bull"):
            avg_down_ok = True
            action = "可分批补仓摊薄(趋势已转好)"
        else:
            health -= 8
            action = "深套+弱势：反弹减仓/止损，不宜补仓"

    return {"stance": round(stance, 1), "health_score": round(max(0, min(100, health)), 1),
            "action": action, "avg_down_ok": avg_down_ok,
            "overheat": overheat, "downtrend": bool(downtrend),
            "total_risk": total_risk, "event_risk_bump": event_bump,
            "drivers": notes[:8]}


def build_operation_plan(price: float, ca: dict | None, pm: dict, sc: dict,
                         verdict: dict, hold_days: int, eval_by: str, eval_dow: str) -> list[dict]:
    """生成分情景、可执行的操作方案（含 T+N 到期处理）。每行: 触发/情景, 动作, 仓位, 说明。
    这是引擎基线，Agent⑤ 可按基本面/消息改写（JSON 顶层 operation_plan 覆盖）。"""
    stop = pm["stop"]
    sup = pm["key_support"]
    az = pm["add_zone"]
    res = pm["key_resist"]
    tp = pm["take_profit"]
    bull = sc["bull"]["price"]
    action = verdict["action"]
    downtrend = verdict["downtrend"]
    avg_down_ok = verdict["avg_down_ok"]
    pnl = ca["pnl_pct"] if ca else None

    rows: list[dict] = []

    # 1) 现在该怎么做
    if "清仓" in action or "止损" in action:
        cur_pos = "主动降到 0–30%"
    elif "减仓" in action:
        cur_pos = "主动降到 50–70%"
    elif "加仓" in action:
        cur_pos = "维持，回踩分批加"
    else:
        cur_pos = "维持现有仓位"
    rows.append({"trigger": "现在（基线判断）", "action": action, "position": cur_pos,
                 "note": " / ".join(verdict.get("drivers", [])[:4])})

    # 2) 回踩到加仓/补仓区
    if avg_down_ok:
        add_note = "趋势已转好(站上MA20+主力转流入)，可分批补仓摊薄成本"
        add_pos = "+20–30%"
    elif not downtrend and (verdict.get("health_score", 0) >= 55):
        add_note = "趋势健康时回踩低吸；若同时跌破MA20/资金转流出则放弃"
        add_pos = "+10–20%"
    else:
        add_note = "趋势偏弱/下跌中，回踩≠机会，**不补仓**，只观察是否企稳"
        add_pos = "不加（0）"
    rows.append({"trigger": f"回踩到 {az[0]}–{az[1]}（加仓区）", "action": "低吸/补仓 或 放弃",
                 "position": add_pos, "note": add_note})

    # 3) 反弹到压力/止盈区
    profit_word = "锁利" if (pnl is not None and pnl > 0) else "减亏/解套离场"
    rows.append({"trigger": f"反弹到 {res}–{tp[1]}（压力/止盈区）", "action": f"分批减仓{profit_word}",
                 "position": "减 30–50%",
                 "note": ("浮盈优先兑现一部分；" if (pnl is not None and pnl > 0) else "套牢盘借反弹减压、降低风险；")
                         + "弱势股冲高更要走"})

    # 4) 冲到看多目标
    rows.append({"trigger": f"冲到看多目标 {bull}", "action": "兑现了结/大幅减仓",
                 "position": "减到 0–30%",
                 "note": f"已达 T+{hold_days} 看多情景目标，别贪，落袋为安"})

    # 5) 跌破止损
    rows.append({"trigger": f"跌破止损 {stop}", "action": "清仓止损（不补仓）", "position": "清空(0)",
                 "note": "破位即离场，不和趋势对抗；纪律优先"})

    # 6) T+N 到期处理（直接回答"到期该不该清仓"）
    if pnl is None:
        exp_act, exp_pos, exp_note = ("复盘后决定", "视情况",
            "达目标→了结；未达且转弱→减仓换股；趋势仍强→上移止损续持并重新诊断")
    elif pnl > 0:
        exp_act, exp_pos, exp_note = ("止盈优先 / 强势可续持", "减到 30–50% 或上移止损",
            f"浮盈状态：到 T+{hold_days}({eval_by} {eval_dow}) 若已近目标或趋势转弱→止盈了结；"
            "趋势仍强→上移止损继续持有，并重新跑一次诊断给新周期")
    elif pnl > -8:
        exp_act, exp_pos, exp_note = ("不死扛 / 趋势优先", "未走强则减 50%+",
            f"轻套：到 T+{hold_days} 仍未解套且趋势没走强→**减仓换强势股**，别无限期等；"
            "若期间已站上MA20+资金转流入→可再给一个周期")
    else:
        exp_act, exp_pos, exp_note = ("反弹减压 / 严守止损", "逢高减，不加",
            f"深套：到 T+{hold_days} 反弹则逢高减仓降风险，趋势未修复不补仓；"
            "基本面恶化(业绩雷/退市风险)→坚决清仓，不抱幻想")
    rows.append({"trigger": f"⏰ 持有到期 T+{hold_days}（{eval_by} {eval_dow}）",
                 "action": exp_act, "position": exp_pos, "note": exp_note})

    return rows


# ---------------------------------------------------------------- 主流程
def diagnose(code: str, cost: float | None, hold_days: int = 20,
             weights_mode: str = "auto", out_path: str | None = None) -> dict:
    code = str(code).strip().zfill(6)
    log(f"诊断 {code}  成本={cost}  持有视野={hold_days}日")

    q = fetch_quote(code)
    name = q.get("name", code)
    industry = (q.get("industry") or "").strip()
    log(f"  {name} | 行业={industry} | 板块={q.get('board')}")

    session = _session_now()
    data_warnings: list[str] = []
    h = _fresh_kline(code, bars=260)
    if h is None or len(h) < 30:
        raise RuntimeError(f"取不到 {code} 足够K线（行情源不可用或代码无效）")
    exp_date = _expected_last_kline_date()
    kline_last = str(h["日期"].iloc[-1])[:10]
    if exp_date and kline_last < exp_date:
        data_warnings.append(f"K线最新仅到{kline_last}(应有{exp_date})，均线/RSI等指标滞后，结论需打折")
    if session in ("盘前", "集合竞价"):
        data_warnings.append("盘前生成：当日涨跌/量比/大盘涨跌为竞价或昨日数据")
    elif session == "盘中":
        data_warnings.append("盘中生成：最新K线为当日未完成bar，收盘后指标可能变化")
    # 跨源价格校验（东财 vs 腾讯）
    try:
        txp = (eng._tx_quote([code]).get(code) or {}).get("price")
        emp = q.get("price")
        if txp and emp and abs(txp / emp - 1) > 0.015:
            data_warnings.append(f"跨源价格不一致：东财{emp} vs 腾讯{txp}(差{abs(txp/emp-1)*100:.1f}%)")
    except Exception:  # noqa: BLE001
        pass

    f = eng.hist_factors(code, name)
    if f is None:
        raise RuntimeError(f"{code} 因子计算失败")

    # 权重校准（≤24h）后给量化分。IC 回测用全市场样本(更稳、样本足)。
    weights, wmeta = (None, {"note": "未启用回测权重(--weights none)"})
    if weights_mode != "none":
        weights, wmeta = ensure_weights(None, hold_days, sample=50)
    f = eng.composite(f, weights)

    # 把快照基本面塞进 f，供 risk_assess 用
    f["amount_yi"] = round(q["amount"] / 1e8, 2) if q.get("amount") else f.get("amt_yi_kline")
    f["float_cap_yi"] = round(q["float_cap"] / 1e8, 1) if q.get("float_cap") else None
    f = eng.risk_assess(f)

    tech = extra_technicals(h)
    price = q.get("price") or f["close"]
    flow = fetch_fund_flow(code, days=10)
    f10 = fetch_f10_extras(code, hold_days, None)
    index_ctx = fetch_index_context()
    peers = fetch_peers(industry, q.get("board") or "", code, target_chg=q.get("chg_pct"))
    # 目标若非匹配板块成分，补回真实名称
    for p in peers.get("peers", []):
        if p.get("is_target"):
            p["name"] = name

    trend = ("上行" if (f.get("bull") or price > f.get("ma20", price)) else
             ("下行" if price < f.get("ma20", price) else "中性"))
    ca = cost_analysis(price, cost, f.get("atr14") or price * 0.05,
                       tech["near_support"], tech["near_resist"],
                       f.get("ma20", price), hold_days, trend=trend)
    verdict = engine_verdict(f, q, tech, flow, ca.get("cost"), index_ctx, peers, f10=f10)

    # 交易日历窗口（T → 评估日 T+N）
    win = eng._trade_window(f.get("last_date", eng._cn_now().date().isoformat()), hold_days)
    op_plan = build_operation_plan(price, ca.get("cost"), ca["price_map"], ca["scenario"],
                                   verdict, hold_days, win.get("sell_by", "?"),
                                   win.get("sell_dow", ""))

    result = {
        "schema": "stock-diagnostic/v1",
        "generated_at": eng._cn_now().isoformat(timespec="seconds"),
        "cn_time": eng._cn_now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code, "name": name, "industry": industry, "board": q.get("board"),
        "hold_days": hold_days,
        "as_of": win.get("as_of"), "as_of_dow": win.get("as_of_dow"),
        "eval_by": win.get("sell_by"), "eval_dow": win.get("sell_dow"),
        "calendar_authoritative": win.get("authoritative"), "note": win.get("note", ""),
        "quote": {"price": price, "prevclose": q.get("prevclose"), "chg_pct": q.get("chg_pct"),
                  "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
                  "amount_yi": f["amount_yi"], "turnover": q.get("turnover"),
                  "vol_ratio": q.get("vol_ratio"),
                  "total_cap_yi": round(q["total_cap"] / 1e8, 1) if q.get("total_cap") else None,
                  "float_cap_yi": f["float_cap_yi"], "pe": q.get("pe"), "pb": q.get("pb")},
        "weights": weights or "默认自适应", "weights_meta": wmeta,
        "quant_score": f["score"],
        "factor_breakdown": {"动量": f["mom"], "量能": f["vol_score"], "技术": f["tech"],
                             "盘口": f.get("tape_score"), "回调": f.get("pull_score"),
                             "惩罚": f.get("penalty")},
        "risk_score": f["risk_score"], "risk_level": f["risk_level"],
        "risk_reasons": f.get("risk_reasons", []),
        "technical": {
            "close": f["close"], "ma5": f.get("ma5"), "ma10": f.get("ma10"),
            "ma20": f.get("ma20"), "ma60": tech["ma60"], "bull": f["bull"],
            "macd_gold": f["macd_gold"], "rsi14": tech["rsi14"],
            "boll_pctb": tech["boll_pctb"], "boll_width_pct": tech["boll_width_pct"],
            "dist_ma10": f["dist_ma10"], "atr_pct": f["atr_pct"], "atr14": f.get("atr14"),
            "ret5": f["ret5"], "ret20": f["ret20"], "ret60": tech["ret60"],
            "pos60": tech["pos60"], "pos120": tech["pos120"], "pos250": tech["pos250"],
            "off_hi250_pct": tech["off_hi250_pct"], "hi250": tech["hi250"], "lo250": tech["lo250"],
            "near_support": tech["near_support"], "near_resist": tech["near_resist"],
            "vol_today": f.get("vol_today"), "tail_strength": f.get("tail_strength"),
            "limit_streak": f.get("limit_streak"), "signals": eng._signal_tag(f),
        },
        "fund_flow": flow,
        "f10": f10,
        "index_context": index_ctx,
        "sector_peers": peers,
        "cost_analysis": ca.get("cost"),
        "scenario": ca["scenario"],
        "price_map": ca["price_map"],
        "map_warnings": ca.get("map_warnings", []),
        "session": session,
        "data_warnings": data_warnings,
        "engine_verdict": verdict,
        "operation_plan": op_plan,
    }
    result["prev_diag"] = _load_prev_diag(code, result.get("cn_time", ""))
    if out_path:
        pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        log(f"已写出 JSON: {out_path}")
    _save_sidecar(result)
    return result


# ---------------------------------------------------------------- 历史诊断留痕与对比
def _sidecar_path(code: str, ts: str) -> pathlib.Path:
    return HERE / "reports" / f"diag_{code}_cn_{ts}.json"


def _save_sidecar(r: dict) -> None:
    """每次诊断在 reports/ 留一份 JSON（与 HTML 同名），供下次诊断对比与盘前复核。
    make_diag_report 回填后会用富集版覆盖同名文件。"""
    try:
        p = _sidecar_path(r["code"], _report_ts(r))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"  历史JSON留痕失败: {str(e)[:50]}")


def _load_prev_diag(code: str, cur_cn_time: str) -> dict | None:
    """找同一代码最近一次历史诊断 JSON，给「与上次对比」：当时价/分数/动作/止损。"""
    try:
        cur_ts = cur_cn_time.replace(" ", "_").replace(":", "-")
        cands = sorted(p for p in (HERE / "reports").glob(f"diag_{code}_cn_*.json")
                       if cur_ts not in p.name)
        if not cands:
            return None
        obj = json.loads(cands[-1].read_text(encoding="utf-8"))
        return {"cn_time": obj.get("cn_time"), "price": (obj.get("quote") or {}).get("price"),
                "final_score": obj.get("final_score"),
                "final_action": obj.get("final_action") or (obj.get("engine_verdict") or {}).get("action"),
                "stance": (obj.get("engine_verdict") or {}).get("stance"),
                "stop": (obj.get("price_map") or {}).get("stop"),
                "agent_scores": obj.get("agent_scores")}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 终端打印
def print_report(r: dict) -> None:
    q = r["quote"]
    t = r["technical"]
    ca = r.get("cost_analysis")
    v = r["engine_verdict"]
    auth = "" if r.get("calendar_authoritative") else " [日历未取到,仅跳周末]"
    print(f"\n## 个股诊断（Agent① 量化底座） — {r['name']}({r['code']})  行业:{r.get('industry') or '—'}")
    print(f"T(数据截止)={r.get('as_of')} {r.get('as_of_dow','')} → 评估视野 T+{r['hold_days']}="
          f"{r.get('eval_by')} {r.get('eval_dow','')}{auth}  (生成 {r['cn_time']} CST · {r.get('session','')})")
    for w in r.get("data_warnings", []):
        print(f"⚠ 数据提示: {w}")
    print(f"现价={q['price']}  今涨={q.get('chg_pct')}%  PE={q.get('pe')}  PB={q.get('pb')}  "
          f"流通市值={q.get('float_cap_yi')}亿  量比={q.get('vol_ratio')}  换手={q.get('turnover')}%")
    if ca:
        underwater = ca["pnl_pct"] < 0
        tail = (f"  解套需涨={ca['breakeven_rally_pct']}%  "
                f"等量补仓后新成本={ca['avg_down_equal']['new_cost']}(解套需涨{ca['avg_down_equal']['new_breakeven_rally_pct']}%)"
                if underwater else "")
        print(f"\n【成本】成本={ca['cost']}  盈亏={ca['pnl_pct']}%({ca['status']})  {ca['cost_vs_price']}{tail}")

    erb = v.get("event_risk_bump", 0)
    risk_s = (f"技术{r['risk_score']}+事件{erb}={v.get('total_risk', r['risk_score'])}"
              if erb else f"{r['risk_score']}")
    print(f"\n【量化】短线量化买入分={r['quant_score']}/100(↑,动量型,长线浮盈股低分正常)  "
          f"风险={r['risk_level']}({risk_s}/100,↓越低越好)  "
          f"持仓健康度={v['health_score']}/100(↑,=0.7×量价stance+0.3×(100−风险))  "
          f"量价stance={v['stance']}/100(仅技术/资金,不含基本面消息)")
    fb = r["factor_breakdown"]
    print(f"  因子: 动量{fb['动量']} 量能{fb['量能']} 技术{fb['技术']} 盘口{fb['盘口']} 回调{fb['回调']} 惩罚-{fb['惩罚']}")
    print(f"\n【技术】收盘{t['close']} MA5/10/20/60={t['ma5']}/{t['ma10']}/{t['ma20']}/{t['ma60']}  "
          f"{'多头' if t['bull'] else '非多头'}  MACD{'金叉' if t['macd_gold'] else '死叉'}  "
          f"RSI{t['rsi14']}  BOLL%b={t['boll_pctb']}")
    print(f"  动量: 5日{t['ret5']}% 20日{t['ret20']}% 60日{t['ret60']}%  位置: 60日{t['pos60']} 120日{t['pos120']} 250日{t['pos250']}  "
          f"距年高{t['off_hi250_pct']}%  ATR%={t['atr_pct']}  信号:{t['signals'] or '—'}")
    print(f"  支撑={t['near_support']}  压力={t['near_resist']}")

    fl = r["fund_flow"]
    if fl.get("available"):
        ratio = fl.get("main_ratio_avg5")
        ratio_s = f"  近5日均占比{ratio}%" if ratio is not None else ""
        print(f"\n【资金】主力近5日净{fl['main_net_5d_yi']}亿({fl['trend']}){ratio_s}  [{fl.get('source','')}]")

    f10 = r.get("f10") or {}
    if f10.get("available"):
        parts = []
        lift = f10.get("lift") or []
        if lift:
            nx = lift[0]
            parts.append(f"解禁:{nx['date']}({_d(nx.get('ratio_pct'),'%')}·{nx.get('type','')})")
        else:
            parts.append("解禁:暂无未来批次")
        pl = f10.get("pledge")
        if pl and pl.get("ratio_pct") is not None:
            parts.append(f"质押:{pl['ratio_pct']}%")
        fc = f10.get("forecast")
        if fc:
            parts.append(f"业绩预告:{fc.get('type')}({fc.get('notice_date')},报告期{fc.get('report_date')})")
        hd = f10.get("holders")
        if hd:
            parts.append(f"股东户数:{hd.get('num')}({_d(hd.get('chg_pct'),'%')})@{hd.get('end_date')}")
        mg = f10.get("margin")
        if mg:
            parts.append(f"融资余额:{_d(mg.get('rzye_yi'),'亿')}(5日净{_d(mg.get('rz_net5d_yi'),'亿')})")
        print("\n【事件面F10】" + "  ".join(parts))
        if f10.get("event_risks"):
            print(f"  ⚠ 事件风险(+{f10.get('risk_bump',0)}分): {'；'.join(f10['event_risks'])}")
        if f10.get("event_notes"):
            print(f"  注: {'；'.join(f10['event_notes'])}")
    sp = r["sector_peers"]
    if sp.get("available"):
        print(f"\n【同行】{sp['sector_name']}  板块均20日涨{sp.get('sect_avg_ret20')}%  "
              f"本股20日强弱排名 {sp.get('target_rank')}/{sp.get('n')}")
        print("  | 代码 | 名称 | 现价 | 今涨% | 5日% | 20日% |")
        print("  |---|---|---|---|---|---|")
        for p in sp["peers"]:
            mark = " ◀本股" if p.get("is_target") else ""
            print(f"  | {p['code']} | {p['name']}{mark} | {p['price']} | {p.get('chg')} | {p.get('ret5')} | {p['ret20']} |")

    ic = r["index_context"]
    idx = ic["indices"]
    print(f"\n【大盘】{ic['regime']}  " + "  ".join(
        f"{k}:{(v.get('chg'))}%" for k, v in idx.items() if v.get('chg') is not None))

    sc = r["scenario"]
    print(f"\n【情景(T+{r['hold_days']})】 看多 {sc['bull']['price']}({sc['bull']['from_now']:+}%)  "
          f"基准 {sc['base']['price']}({sc['base']['from_now']:+}%)  "
          f"看空 {sc['bear']['price']}({sc['bear']['from_now']:+}%)")
    pm = r["price_map"]
    print(f"【价位地图】支撑{pm['key_support']} | 压力{pm['key_resist']} | 止损{pm['stop']}({pm.get('stop_atr_mult','?')}×ATR与结构取紧) | "
          f"加仓区{pm['add_zone'][0]}–{pm['add_zone'][1]} | 止盈区{pm['take_profit'][0]}–{pm['take_profit'][1]}")
    print(f"【R:R】现价做多 风险回报比={pm.get('rr')} → {pm.get('rr_note','')}")
    for w in r.get("map_warnings", []):
        print(f"  ⚠ 价位自检: {w}")

    prev = r.get("prev_diag")
    if prev:
        print(f"\n【上次诊断对比】{prev.get('cn_time')}  当时价{prev.get('price')} → 现价{r['quote']['price']}  "
              f"当时裁定:{prev.get('final_action')}(分{prev.get('final_score')})  当时止损{prev.get('stop')}")

    print(f"\n【引擎基线动作】{v['action']}")
    print(f"  依据: {' / '.join(v['drivers'])}")

    op = r.get("operation_plan") or []
    if op:
        print(f"\n【操作方案（分情景 · 含 T+{r['hold_days']} 到期）】")
        print("| 情景/触发 | 动作 | 仓位 | 说明 |")
        print("|---|---|---|---|")
        for o in op:
            print(f"| {o['trigger']} | {o['action']} | {o['position']} | {o['note']} |")

    print("\n（以上为 Agent① 量化底座；Agent②基本面 / ③消息(中英文) / ④资金板块 / ⑤风险裁决 由 Claude Code 在此之上 WebSearch 综合）")
    print(f"\n候选查询代码: {r['code']} {r['name']}  行业:{r.get('industry')}")


# ---------------------------------------------------------------- HTML 报告
_RISK_COLOR = {"低": "#2e7d32", "中": "#1565c0", "中高": "#ef6c00", "高": "#c62828"}


def _esc(x) -> str:
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _d(x, suffix: str = "", dash: str = "—"):
    """None/NaN 友好显示：空值→破折号，否则带后缀。"""
    if x is None or (isinstance(x, float) and x != x):
        return dash
    return f"{x}{suffix}"


def _as_num(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def _try_final_score(rows: list[dict]) -> float | None:
    """从五维评分按 SKILL 权重自动算综合裁定分：
    综合 = 技术0.28+基本面0.22+消息0.22+资金0.28 − 风险0.15。四个看多维齐全才算。"""
    m = {}
    for a in rows:
        d, s = a.get("dim", ""), _as_num(a.get("score"))
        if s is None:
            continue
        if "技术" in d:
            m["tech"] = s
        elif "基本面" in d or "财务" in d:
            m["fund"] = s
        elif "消息" in d or "催化" in d:
            m["news"] = s
        elif "资金" in d or "板块" in d:
            m["cap"] = s
        elif "风险" in d:
            m["risk"] = s
    if {"tech", "fund", "news", "cap"} <= set(m):
        base = m["tech"] * 0.28 + m["fund"] * 0.22 + m["news"] * 0.22 + m["cap"] * 0.28
        return round(max(0.0, min(100.0, base - m.get("risk", 0) * 0.15)), 1)
    return None


def _agent_dir(dim: str) -> str:
    """该维度评分方向标注。"""
    return "↓越低越好" if "风险" in dim else "↑越高越好"


def _report_ts(r: dict) -> str:
    """报告文件名时间戳 = 本次**诊断生成时间**(取自结果JSON)，不是渲染时刻。
    这样同一次诊断无论重渲染几次(预览→完整→重出完整)都映射到同一文件名，不产生中间文件；
    而另一次新诊断(重新跑引擎)时间不同 → 另存一份，历史得以保留。"""
    s = r.get("cn_time")  # "YYYY-MM-DD HH:MM:SS"（中国时间）
    if s:
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d_%H-%M-%S")
        except ValueError:
            pass
    ga = r.get("generated_at")
    if ga:
        try:
            return dt.datetime.fromisoformat(ga).strftime("%Y-%m-%d_%H-%M-%S")
        except ValueError:
            pass
    return eng._cn_now().strftime("%Y-%m-%d_%H-%M-%S")


def render_html(r: dict, report_dir: str | None = None, is_final: bool = True) -> str:
    ts = _report_ts(r)  # 时间戳=诊断生成时间(同次诊断重渲染→同名文件,不产生中间文件)
    rdir = pathlib.Path(report_dir) if report_dir else (HERE / "reports")
    rdir.mkdir(parents=True, exist_ok=True)
    # 完整报告 → diag_{code}_cn_{ts}.html（不同诊断各存一份、保留历史；同次诊断重渲染同名→覆盖自身）
    # 半成品预览 → diag_{code}_preview_cn_{ts}.html（不累积）
    tag = "" if is_final else "_preview"
    path = rdir / f"diag_{r['code']}{tag}_cn_{ts}.html"
    # 写完整报告时，清掉同代码、同次诊断的预览（同 ts 的 _preview_）；其它完整报告永不删除。
    for old in rdir.glob(f"diag_{r['code']}_preview_cn_*.html"):
        if old.resolve() != path.resolve():
            try:
                old.unlink()
            except OSError:
                pass

    q, t, v = r["quote"], r["technical"], r["engine_verdict"]
    ca, sc, pm = r.get("cost_analysis"), r["scenario"], r["price_map"]
    fl, sp, ic = r["fund_flow"], r["sector_peers"], r["index_context"]
    rl = r["risk_level"]
    rcolor = _RISK_COLOR.get(rl, "#666")

    pnl_html = ""
    if ca:
        pcolor = "#c62828" if ca["pnl_pct"] >= 0 else "#2e7d32"  # A股红涨绿跌
        pnl_html = (f"<div class=stat><span class=lab>持仓盈亏</span>"
                    f"<b style='color:{pcolor}'>{ca['pnl_pct']:+}%</b><span class=sub>{ca['status']}</span></div>"
                    f"<div class=stat><span class=lab>成本价</span><b>{ca['cost']}</b>"
                    f"<span class=sub>解套需涨{ca['breakeven_rally_pct']}%</span></div>")

    # 5-agent 综合裁定表（引擎给底座分，Claude 回填基本面/消息/最终）
    agent_rows = r.get("agent_scores")  # 由 Claude 回填: list of {dim,score,stance,key}
    if not agent_rows:
        agent_rows = [
            {"dim": "① 技术·量价", "score": v["stance"], "key": "引擎量化底座(见技术区)"},
            {"dim": "② 基本面·财务", "score": "—", "key": "待 Claude WebSearch 回填(PE/PB/业绩/估值分位)"},
            {"dim": "③ 消息·催化剂(中英文)", "score": "—", "key": "待回填(公告/研报/政策/海外)"},
            {"dim": "④ 资金·板块·大盘", "score": "—", "key": f"主力5日{fl.get('main_net_5d_yi','?')}亿 / {ic['regime']}"},
            {"dim": "⑤ 风险裁决", "score": r["risk_score"], "key": "/".join(r.get("risk_reasons", [])) or "见风险区"},
        ]
    agent_tr = "".join(
        f"<tr><td class=dim>{_esc(a['dim'])}</td><td class=sc>{_d(a.get('score'))}</td>"
        f"<td class=dir>{_agent_dir(a.get('dim',''))}</td><td class=keyc>{_esc(a.get('key',''))}</td></tr>"
        for a in agent_rows)
    # 综合裁定行：优先用 Claude 回填的 final_score，否则按权重自动合成
    final_score = r.get("final_score")
    if final_score is None:
        final_score = _try_final_score(agent_rows)
    fa = r.get("final_action") or v["action"]
    fc = r.get("final_confidence") or "待五方回填"
    agent_tr += (
        f"<tr class=total><td class=dim>综合裁定</td>"
        f"<td class=sc>{_d(final_score)}{'/100' if final_score is not None else ''}</td>"
        f"<td class=dir>↑越高越好</td><td class=keyc>{_esc(fa)} · 置信度{_esc(fc)}</td></tr>")

    peer_tr = ""
    if sp.get("available"):
        for p in sp["peers"]:
            cls = " class=tgt" if p.get("is_target") else ""
            peer_tr += (f"<tr{cls}><td>{_esc(p['code'])}</td><td>{_esc(p['name'])}"
                        f"{'◀' if p.get('is_target') else ''}</td><td>{p['price']}</td>"
                        f"<td>{_d(p.get('chg'))}</td><td>{_d(p.get('ret5'))}</td><td>{_d(p['ret20'])}</td></tr>")

    flow_tr = ""
    if fl.get("available"):
        for d in fl["days"][-6:]:
            mc = "#c62828" if (d.get("main_yi") or 0) >= 0 else "#2e7d32"
            flow_tr += (f"<tr><td>{_esc(d['date'])}</td><td style='color:{mc}'>{_d(d.get('main_yi'))}</td>"
                        f"<td>{_d(d.get('main_ratio'))}</td><td>{_d(d.get('chg'))}</td></tr>")

    idx_html = "  ".join(
        f"<span class=pill>{_esc(k)} {x.get('chg')}%</span>"
        for k, x in ic["indices"].items() if x.get("chg") is not None)

    # 操作方案表（分情景 + T+N 到期）
    op_plan = r.get("operation_plan") or []
    op_tr = ""
    for i, o in enumerate(op_plan):
        cls = " class=expire" if "到期" in o.get("trigger", "") else ""
        op_tr += (f"<tr{cls}><td class=trg>{_esc(o.get('trigger',''))}</td>"
                  f"<td class=act2>{_esc(o.get('action',''))}</td>"
                  f"<td>{_esc(o.get('position',''))}</td>"
                  f"<td class=opnote>{_esc(o.get('note',''))}</td></tr>")

    final_md = r.get("final_md")  # Claude 回填最终裁决全文
    agents_md = r.get("agents_md")  # Claude 回填五方辩论全文
    extra = ""
    if agents_md:
        extra += f"<h2>多方辩论详情</h2><div class=md><pre>{_esc(agents_md)}</pre></div>"
    if final_md:
        extra += f"<h2>⑤ 首席裁决官 · 综合裁定与操作</h2><div class=md><pre>{_esc(final_md)}</pre></div>"

    # 数据/价位警示条（可信度：异常必须摆在明面上）
    warn_html = ""
    warns = list(r.get("data_warnings") or []) + list(r.get("map_warnings") or [])
    if warns:
        warn_html = ("<div class=warns>" + "".join(f"<div>⚠ {_esc(w)}</div>" for w in warns)
                     + "</div>")

    # 验证复核状态（Agent⑥ 回填 verification: {"passed":[...], "failed":[...], "notes":...}）
    verif = r.get("verification")
    verif_html = ""
    if isinstance(verif, dict):
        np_, nf = len(verif.get("passed") or []), len(verif.get("failed") or [])
        color = "#2e7d32" if nf == 0 else "#c62828"
        items = "；".join((verif.get("passed") or [])[:10])
        fitems = "；".join(verif.get("failed") or [])
        verif_html = (f"<div class=note style='border-color:{color}'>"
                      f"<b style='color:{color}'>验证·复核官：{np_}项通过"
                      f"{('，'+str(nf)+'项未过: '+_esc(fitems)) if nf else ''}</b>"
                      f"<br><span style='color:#888'>{_esc(items)}</span></div>")

    # 上次诊断对比
    prev = r.get("prev_diag")
    prev_html = ""
    if prev and prev.get("price"):
        chg_since = round((q["price"] / prev["price"] - 1) * 100, 1) if q.get("price") else None
        prev_html = (f"<div class=note><b>与上次诊断对比</b>（{_esc(prev.get('cn_time'))}）："
                     f"当时价 {prev['price']} → 现价 {q.get('price')}（{_d(chg_since,'%')}）；"
                     f"当时裁定「{_esc(prev.get('final_action') or '—')}」"
                     f"{('，分'+str(prev.get('final_score'))) if prev.get('final_score') is not None else ''}"
                     f"，当时止损 {_d(prev.get('stop'))}</div>")

    # F10 事件面卡片
    def kv2(g, val):  # 行内已自行转义，不再二次转义
        return f"<div><span class=g>{_esc(g)}</span><b>{val}</b></div>"

    f10 = r.get("f10") or {}
    f10_card = ""
    if f10.get("available"):
        lift = f10.get("lift") or []
        lift_s = (f"{lift[0]['date']}（{_d(lift[0].get('ratio_pct'),'%')}·{_esc(lift[0].get('type') or '')}）"
                  if lift else "暂无未来批次")
        pl = f10.get("pledge") or {}
        fc = f10.get("forecast") or {}
        hd = f10.get("holders") or {}
        mg = f10.get("margin") or {}
        rows_kv = (
            kv2("下次解禁", lift_s)
            + kv2("质押比例", _d(pl.get("ratio_pct"), "%") + f"（{_esc(pl.get('date') or '')}）" if pl else "—")
            + kv2("业绩预告", f"{_esc(fc.get('type') or '—')}（{_esc(fc.get('notice_date') or '')}·报告期{_esc(fc.get('report_date') or '')}）" if fc else "—")
            + kv2("股东户数", f"{_d(hd.get('num'))}（{_d(hd.get('chg_pct'),'%')}）@{_esc(hd.get('end_date') or '')}" if hd else "—")
            + kv2("融资余额", f"{_d(mg.get('rzye_yi'),'亿')} · 5日净{_d(mg.get('rz_net5d_yi'),'亿')} · 占流通{_d(mg.get('rzye_ratio_pct'),'%')}" if mg else "—"))
        ev = ""
        if f10.get("event_risks"):
            ev = (f"<div style='color:#c62828;font-size:12px;margin-top:6px'>⚠ 事件风险+{f10.get('risk_bump',0)}分："
                  f"{_esc('；'.join(f10['event_risks']))}</div>")
        elif f10.get("event_notes"):
            ev = f"<div style='color:#888;font-size:12px;margin-top:6px'>{_esc('；'.join(f10['event_notes']))}</div>"
        f10_card = f"<div class=card><h3>事件面 F10（东财数据中心·客观）</h3><div class=kv>{rows_kv}</div>{ev}</div>"

    action = r.get("final_action") or v["action"]
    conf = r.get("final_confidence") or "引擎基线(待五方回填)"

    css = (
        "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:#eef1f5;color:#1a1a1a;line-height:1.55}"
        ".wrap{max-width:1000px;margin:0 auto;padding:20px}"
        "h1{font-size:22px;margin:6px 0}h2{font-size:16px;margin:22px 0 8px;border-left:4px solid #1565c0;padding-left:9px}"
        ".meta{background:#fff;border:1px solid #e0e4e8;border-radius:10px;padding:12px 14px;font-size:13px;color:#333}"
        ".banner{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:linear-gradient(90deg,#1565c0,#1e88e5);"
        "color:#fff;border-radius:12px;padding:16px 20px;margin:14px 0}"
        ".banner .act{font-size:22px;font-weight:800}.banner .conf{background:rgba(255,255,255,.2);padding:3px 10px;border-radius:20px;font-size:13px}"
        ".stats{display:flex;gap:26px;flex-wrap:wrap;margin:12px 0;background:#fff;border:1px solid #e6e9ed;border-radius:12px;padding:14px 18px}"
        ".stat .lab{color:#999;font-size:11px;display:block}.stat b{font-size:18px}.stat .sub{color:#888;font-size:11px;margin-left:6px}"
        ".grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:680px){.grid{grid-template-columns:1fr}}"
        ".card{background:#fff;border:1px solid #e6e9ed;border-radius:12px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.05)}"
        ".card h3{margin:0 0 8px;font-size:14px;color:#1565c0}"
        ".kv{font-size:13px;line-height:1.9}.kv b{font-weight:700}.kv .g{color:#888;display:inline-block;min-width:64px}"
        ".badge{color:#fff;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600;background:%s}" % rcolor +
        ".pill{background:#eef4ff;color:#1565c0;border-radius:6px;padding:2px 8px;font-size:12px;margin-right:4px;white-space:nowrap}"
        "table{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px;border-radius:8px;overflow:hidden}"
        "th,td{padding:6px 8px;text-align:center;border-bottom:1px solid #eef1f4;white-space:nowrap}"
        "th{background:#1565c0;color:#fff;font-weight:600}tr.tgt{background:#fff7e6;font-weight:700}td.dim{text-align:left;font-weight:600}td.sc{font-weight:700;color:#1565c0}"
        "td.dir{font-size:11px;color:#888}tr.total{background:#eef4ff}tr.total td{border-top:2px solid #1565c0;font-size:13.5px}tr.total .dim,tr.total .sc{font-weight:800;color:#0d3c75}"
        "td.trg{text-align:left;font-weight:600;white-space:normal}td.act2{font-weight:700;color:#0d3c75;white-space:normal}td.opnote{text-align:left;color:#555;white-space:normal;font-size:12px}"
        "tr.expire{background:#fff7e6}tr.expire td{border-top:2px solid #ef6c00}tr.expire .trg{color:#b45309}"
        # 自适应换行表(无横向滚动条)：固定列宽 + 长文本自动换行
        "table.wt{table-layout:fixed}table.wt th,table.wt td{white-space:normal;word-break:break-word;vertical-align:top;line-height:1.5}"
        "table.wt td.keyc{text-align:left}"
        ".note{font-size:12px;color:#666;background:#fff;border:1px dashed #cfd6dd;border-radius:8px;padding:8px 11px;margin:8px 0}"
        ".warns{background:#fff3f0;border:1px solid #ffab91;border-radius:8px;padding:8px 11px;margin:10px 0;font-size:12.5px;color:#b71c1c;line-height:1.7}"
        ".scroll{overflow-x:auto}.md pre{white-space:pre-wrap;background:#fff;border:1px solid #e0e4e8;border-radius:8px;padding:12px;font-size:13px}"
        ".foot{font-size:12px;color:#888;margin-top:18px;padding-top:10px;border-top:1px solid #e0e4e8}"
        ".map{display:flex;gap:10px;flex-wrap:wrap;font-size:13px}.map div{background:#f5f8fc;border-radius:8px;padding:7px 12px;text-align:center}"
        ".map i{color:#999;font-style:normal;font-size:11px;display:block}.map b{font-size:14px}"
        ".map small{color:#9aa4b0;font-size:10px;display:block;margin-top:2px}"
    )

    def kv(g, val):
        return f"<div><span class=g>{g}</span><b>{_esc(val)}</b></div>"

    cost_card = ""
    if ca:
        ad = ca["avg_down_equal"]
        uw = ca["pnl_pct"] < 0
        cost_card = (
            f"<div class=card><h3>持仓成本</h3><div class=kv>"
            + kv("成本价", ca["cost"]) + kv("盈亏%", f"{ca['pnl_pct']:+}% ({ca['status']})")
            + (kv("解套需涨", f"{ca['breakeven_rally_pct']}%") if uw else "")
            + kv("成本位置", f"{ca['cost_vs_price']} · {ca['cost_vs_ma20']}")
            + (kv("等量补仓", f"新成本{ad['new_cost']} · 解套需涨{ad['new_breakeven_rally_pct']}%") if uw else "")
            + "</div></div>")

    html = (
        f"<!doctype html><html lang=zh><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>个股诊断 {r['name']} {r['code']} {ts}</title><style>{css}</style></head><body><div class=wrap>"
        f"<h1>🔍 个股诊断报告 · {_esc(r['name'])} <span style='color:#888;font-family:monospace;font-size:15px'>{r['code']}</span></h1>"
        f"<div class=meta><b>生成(中国时间)</b>：{r['cn_time']} CST · {_esc(r.get('session') or '')} &nbsp;|&nbsp; <b>T</b>：{r.get('as_of')} {r.get('as_of_dow','')}"
        f" → <b>评估视野 T+{r['hold_days']}</b>：{r.get('eval_by')} {r.get('eval_dow','')}"
        f"<br>行业：{_esc(r.get('industry') or '—')} · {_esc(r.get('board') or '')} &nbsp;|&nbsp; 因子权重：{_esc(r.get('weights_meta',{}).get('note',''))}"
        f"{(' · '+_esc(r.get('note'))) if r.get('note') else ''}</div>"
        + warn_html + verif_html + prev_html +
        f"<div class=banner><span class=act>{_esc(action)}</span><span class=conf>置信度 {_esc(conf)}</span>"
        f"<span class=conf>持仓健康度 {v['health_score']}/100 ↑（=0.7×量价stance+0.3×(100−总风险)）</span>"
        f"<span class=conf>短线量化分 {r['quant_score']}/100 ↑（动量型，长线浮盈股低分正常）</span>"
        f"<span class=conf>风险 {rl}（技术{r['risk_score']}"
        f"{('+事件'+str(v.get('event_risk_bump'))+'='+str(v.get('total_risk'))) if v.get('event_risk_bump') else ''}/100 ↓）</span></div>"
        f"<div class=stats>"
        f"<div class=stat><span class=lab>现价</span><b>{q['price']}</b><span class=sub>{_d(q.get('chg_pct'),'%')}</span></div>"
        f"{pnl_html}"
        f"<div class=stat><span class=lab>PE / PB</span><b>{_d(q.get('pe'))} / {_d(q.get('pb'))}</b></div>"
        f"<div class=stat><span class=lab>流通市值</span><b>{_d(q.get('float_cap_yi'),'亿')}</b></div>"
        f"<div class=stat><span class=lab>量价stance</span><b>{v['stance']}</b><span class=sub>/100 仅技术·资金，≠综合裁定</span></div>"
        f"<div class=stat><span class=lab>现价R:R</span><b>{_d(pm.get('rr'))}</b><span class=sub>{_esc(pm.get('rr_note',''))}</span></div></div>"

        f"<h2>6-Agent 综合裁定（⑤裁定 · ⑥复核）</h2>"
        f"<table class=wt><colgroup><col style='width:23%'><col style='width:13%'><col style='width:15%'><col style='width:49%'></colgroup>"
        f"<tr><th>维度</th><th>评分(0-100)</th><th>方向</th><th>关键依据</th></tr>{agent_tr}</table>"
        f"<div class=note>说明：①②③④ 为看多强度，<b>越高越偏多</b>；⑤风险为危险度，<b>越低越好</b>。"
        f"综合裁定 = 技术×0.28 + 基本面×0.22 + 消息×0.22 + 资金板块×0.28 − 风险×0.15（越高越偏多）。</div>"

        f"<h2>关键价位地图（T+{r['hold_days']} 视野）</h2>"
        f"<div class=note>给一只票的全部关键挂单价位：从下到上依次是 止损→支撑→加仓区→MA20→压力→止盈区。</div><div class=map>"
        f"<div title='ATR倍数随持有视野缩放，与结构支撑取较紧者'><i>止损</i><b>{pm['stop']}</b>"
        f"<small>{pm.get('stop_atr_mult','?')}×ATR/结构取紧,破位离场</small></div>"
        f"<div title='下方最近的强支撑(均线/近期低点)'><i>关键支撑</i><b>{pm['key_support']}</b><small>下方最近支撑</small></div>"
        f"<div title='趋势条件满足时分批低吸的区间'><i>加仓/补仓区</i><b>{pm['add_zone'][0]}–{pm['add_zone'][1]}</b><small>回踩低吸区</small></div>"
        f"<div title='20日均线,中期多空分界'><i>MA20</i><b>{pm['ma20']}</b><small>中期多空线</small></div>"
        f"<div title='上方最近的阻力(均线/近期高点/年内高)'><i>关键压力</i><b>{pm['key_resist']}</b><small>上方最近阻力</small></div>"
        f"<div title='分批止盈/减仓的目标区间'><i>止盈区</i><b>{pm['take_profit'][0]}–{pm['take_profit'][1]}</b><small>减仓目标区</small></div>"
        f"<div title='T+N 视野三档情景目标价'><i>看空/基准/看多</i><b>{sc['bear']['price']} / {sc['base']['price']} / {sc['bull']['price']}</b><small>T+{r['hold_days']}三档情景</small></div>"
        f"</div>"

        f"<h2>📋 操作方案（分情景 · 含 T+{r['hold_days']} 到期处理）</h2>"
        f"<div class=note>不是一句话——把「涨到哪/跌到哪/回踩/到期」各情景该怎么操作、加减多少仓都列清。"
        f"末行就是你问的「到 T+{r['hold_days']} 该不该清」。</div>"
        f"<table class=wt><colgroup><col style='width:25%'><col style='width:19%'><col style='width:14%'><col style='width:42%'></colgroup>"
        f"<tr><th>情景 / 触发</th><th>动作</th><th>仓位</th><th>说明</th></tr>{op_tr}</table>"

        f"<div class=grid style='margin-top:14px'>"
        f"<div class=card><h3>技术面</h3><div class=kv>"
        + kv("均线", f"MA5/10/20/60 {t['ma5']}/{t['ma10']}/{t['ma20']}/{t['ma60']} · {'多头' if t['bull'] else '非多头'}")
        + kv("MACD/RSI", f"{'金叉' if t['macd_gold'] else '死叉'} · RSI {t['rsi14']} · BOLL%b {t['boll_pctb']}")
        + kv("动量", f"5日{t['ret5']}% 20日{t['ret20']}% 60日{t['ret60']}%")
        + kv("位置", f"60日{t['pos60']} 120日{t['pos120']} 250日{t['pos250']} · 距年高{t['off_hi250_pct']}%")
        + kv("ATR/信号", f"ATR%{t['atr_pct']} · {t['signals'] or '—'}")
        + "</div></div>"
        + cost_card
        + f"<div class=card><h3>资金流（主力净额/亿）</h3>"
        + (f"<div class=kv>{kv('近5日主力净', _d(fl.get('main_net_5d_yi'),'亿')+' · '+fl.get('trend',''))}{kv('近5日均占比', _d(fl.get('main_ratio_avg5'),'%'))}{kv('数据源', fl.get('source',''))}</div>"
           f"<div class=scroll><table><tr><th>日期</th><th>主力净亿</th><th>占比%</th><th>涨跌%</th></tr>{flow_tr}</table></div>"
           if fl.get("available") else "<div class=kv>资金流数据暂不可用</div>")
        + "</div>"
        + f10_card
        + f"<div class=card><h3>大盘环境</h3><div class=kv>{kv('regime', ic['regime'])}</div><div style='margin-top:6px'>{idx_html}</div></div>"
        + "</div>"

        + (f"<h2>同行对比 · {_esc(sp.get('sector_name',''))}（20日强弱 {_d(sp.get('target_rank'))}/{sp.get('n')}，板块均{_d(sp.get('sect_avg_ret20'),'%')}）</h2>"
           f"<div class=scroll><table><tr><th>代码</th><th>名称</th><th>现价</th><th>今涨%</th><th>5日%</th><th>20日%</th></tr>{peer_tr}</table></div>"
           if sp.get("available") else "")

        + f"<h2>引擎基线动作</h2><div class=card><div class=kv>"
        + kv("建议", v["action"]) + kv("依据", " / ".join(v["drivers"]))
        + kv("提示", "深套+弱势不补仓；过热浮盈优先止盈；最终以五方裁决为准") + "</div></div>"
        + extra
        + f"<div class=foot>数据源：东方财富/腾讯/新浪（免费公开行情）+ 主力资金流 + IC校准因子权重。"
        f"本报告为量化研究分析，<b>非投资建议</b>；A股 T+1，注意仓位与止损纪律。成本相关测算仅供参考。</div>"
        f"</div></body></html>"
    )
    path.write_text(html, encoding="utf-8")
    return str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="单只A股持仓诊断引擎 (Agent① 量化底座, 0-API)")
    ap.add_argument("--code", required=True, help="股票代码，如 600519")
    ap.add_argument("--cost", type=float, default=None, help="持仓成本价（可选）")
    ap.add_argument("--hold-days", type=int, default=20, help="评估视野(交易日)，驱动情景与权重窗口。默认20")
    ap.add_argument("--weights", default="auto", help="auto=≤24h自动校准IC权重(默认); none=用持有天数自适应默认权重")
    ap.add_argument("--out", default=r"C:\Trading_analysis\data\diag_latest.json", help="结果JSON路径")
    ap.add_argument("--no-report", action="store_true", help="不生成HTML报告")
    ap.add_argument("--report-dir", default=None, help="HTML报告目录(默认 reports/)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if args.no_cache:
        eng._USE_CACHE = False
    if args.refresh:
        eng._REFRESH = True

    r = diagnose(args.code, args.cost, hold_days=args.hold_days,
                 weights_mode=args.weights, out_path=args.out)
    print_report(r)
    if not args.no_report:
        # 引擎直出的是"预览"(未含 Agent②~⑤ 回填)；完整版由 make_diag_report.py 出并永久保留
        rp = render_html(r, args.report_dir, is_final=False)
        print(f"\n📄 HTML诊断报告(预览)已保存: {rp}")
        print("   (做完五方裁决后跑 make_diag_report.py 出完整版，完整版每次都另存、不覆盖)")


if __name__ == "__main__":
    main()
