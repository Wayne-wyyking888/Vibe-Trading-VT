#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weekly A-share quant screener — Agent 1 engine for the weekly-ashare-rank skill.

直连东方财富行情接口（浏览器 UA，免费、无需任何 API key、不依赖 akshare），
拉取沪深A股实时快照 + 历史K线，计算短线 动量/量能/技术 因子，剔除
ST/次新/低流动性/今日涨跌停附近，输出按综合因子打分排序的候选股。

这是纯数据、确定性的引擎。Claude Code 在此输出之上扮演：
  - Agent 2 catalyst_analyst：WebSearch 查催化剂/政策/资金面
  - Agent 3 risk_challenger：综合打分、风险挑战、给入场/目标/止损价

用法:
  python ashare_weekly_rank.py --top 15 --pool 40
  python ashare_weekly_rank.py --sector 光通信 --top 8
  python ashare_weekly_rank.py --out C:/Trading_analysis/data/rank.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import sys
import time
import warnings

warnings.filterwarnings("ignore")  # 静音 pandas spearman 常量列等无害告警

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:
    sys.stderr.write("需要 requests: pip install requests\n")
    sys.exit(1)


# ---------------------------------------------------------------- 东财直连客户端

# 东财行情有多个镜像主机，轮换可显著降低 502 限流概率
_PUSH_HOSTS = [
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://13.push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
]
_KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://13.push2his.eastmoney.com",
]
_CLIST_PATH = "/api/qt/clist/get"
_KLINE_PATH = "/api/qt/stock/kline/get"

# 熔断：东财一旦被限流，本次运行后续直接跳过它走回退源，避免每只票都白等重试
_EM_DEAD = False

# ---------------------------------------------------------------- 本地缓存
# K线/快照当天缓存，避免周五反复跑时重复拉数、触发限流。K线收盘后当天不变，
# 给较长 TTL（覆盖周末）；快照盘中会变，给短 TTL。
_USE_CACHE = True
_REFRESH = False  # 强制重拉（绕过读缓存，但仍写回）
_CACHE_DIR = pathlib.Path.home() / ".vibe-trading" / "cache" / "ashare_weekly"


def _cache_get(kind: str, key: str, max_age_h: float):
    if not _USE_CACHE or _REFRESH:
        return None
    p = _CACHE_DIR / kind / f"{key}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        ts = dt.datetime.fromisoformat(obj["_ts"])
        if (dt.datetime.now() - ts).total_seconds() > max_age_h * 3600:
            return None
        return obj["data"]
    except Exception:  # noqa: BLE001
        return None


def _cache_put(kind: str, key: str, data) -> None:
    if not _USE_CACHE:
        return
    p = _CACHE_DIR / kind / f"{key}.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_ts": dt.datetime.now().isoformat(), "data": data},
                                ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*",
    }
)

# 全市场A股: 深市主板+创业板 / 沪市主板+科创板
_FS_ALL_A = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
# 行情字段（fltt=2 → 已是小数，无需再缩放）
_SPOT_FIELDS = "f12,f14,f2,f3,f5,f6,f8,f10,f20,f21"
_COL_MAP = {
    "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅",
    "f5": "成交量", "f6": "成交额", "f8": "换手率", "f10": "量比",
    "f20": "总市值", "f21": "流通市值",
}


def log(msg: str) -> None:
    """进度信息打到 stderr，stdout 只留最终 JSON/表格。"""
    sys.stderr.write(f"[engine] {msg}\n")
    sys.stderr.flush()


def _get(hosts: list[str], path: str, params: dict, retries: int = 8) -> dict:
    """带主机轮换 + 指数退避(含抖动)的 GET，返回 JSON dict。

    东财对高频访问会软封 IP（返回 502 / 空响应 / 断连），通常数十秒到数分钟
    自动解除。这里用较长的指数退避(最长单次≈15s，总计≈70s)+ 多镜像主机轮换
    扛过临时限流；周度运行只发 ~30 个请求，正常不会触发。
    """
    last = None
    attempts = max(retries, len(hosts))
    for i in range(attempts):
        host = hosts[i % len(hosts)]
        try:
            r = _SESSION.get(host + path, params=params, timeout=15)
            r.raise_for_status()
            txt = r.text.strip()
            if not txt:  # 空响应也是限流信号
                raise RuntimeError("empty response (throttled)")
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(15.0, 0.8 * (2 ** i)) + random.uniform(0, 0.5))
    raise RuntimeError(f"请求失败 {path}: {last}")


def _clist_top(fs: str, fields: str, fid: str = "f6", pz: int = 600,
               retries: int = 8) -> list[dict]:
    """单页拉取 clist：服务端按 fid 降序，取前 pz 行（避免翻页限流）。"""
    params = {
        "pn": 1, "pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": fid, "fs": fs, "fields": fields,
    }
    data = _get(_PUSH_HOSTS, _CLIST_PATH, params, retries=retries).get("data")
    if not data or not data.get("diff"):
        return []
    diff = data["diff"]
    return list(diff.values()) if isinstance(diff, dict) else list(diff)


_SINA_HQ = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)


def _em_spot(top: int) -> pd.DataFrame | None:
    """东财快照（主源）。快速失败以便回退。"""
    global _EM_DEAD
    try:
        rows = _clist_top(_FS_ALL_A, _SPOT_FIELDS, fid="f6", pz=top, retries=2)
        if rows:
            return pd.DataFrame(rows).rename(columns=_COL_MAP)
    except Exception as e:  # noqa: BLE001
        log(f"  eastmoney 快照失败: {str(e)[:60]}")
    _EM_DEAD = True  # 快照失败=被限流，后续K线直接跳过东财
    return None


def _sina_spot(top: int) -> pd.DataFrame | None:
    """新浪行情中心（回退源），按成交额降序分页。不含量比。"""
    rows: list[dict] = []
    page, num = 1, 100
    while len(rows) < top and page <= 12:
        try:
            r = _SESSION.get(
                _SINA_HQ,
                params={"page": page, "num": num, "sort": "amount", "asc": 0,
                        "node": "hs_a", "symbol": "", "_s_r_a": "page"},
                headers={"Referer": "https://finance.sina.com.cn/"}, timeout=15,
            )
            arr = r.json()
        except Exception as e:  # noqa: BLE001
            log(f"  sina 第{page}页失败: {str(e)[:50]}")
            break
        if not arr:
            break
        rows.extend(arr)
        page += 1
        time.sleep(0.2)
    if not rows:
        return None
    df = pd.DataFrame(rows[:top])
    out = pd.DataFrame({
        "代码": df["code"].astype(str),
        "名称": df["name"].astype(str),
        "最新价": pd.to_numeric(df["trade"], errors="coerce"),
        "涨跌幅": pd.to_numeric(df["changepercent"], errors="coerce"),
        "成交量": pd.to_numeric(df["volume"], errors="coerce"),
        "成交额": pd.to_numeric(df["amount"], errors="coerce"),
        "换手率": pd.to_numeric(df["turnoverratio"], errors="coerce"),
        "量比": np.nan,  # 新浪不提供量比，用K线 vr 代偿
        "总市值": pd.to_numeric(df["mktcap"], errors="coerce") * 1e4,   # 万元→元
        "流通市值": pd.to_numeric(df["nmc"], errors="coerce") * 1e4,
    })
    # 限流时新浪会返回降级数据（成交额全 0、按代码排序的北交所股），剔除并判失败
    out = out[out["成交额"] > 0].sort_values("成交额", ascending=False)
    if len(out) < 50:
        log("  sina 返回疑似降级数据（有效行过少），判失败")
        return None
    return out.reset_index(drop=True)


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _tx_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯批量实时报价 → {code: {name, price, float_cap, total_cap}}。盘前也能拿到名称/流通市值。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        q = ",".join(_tx_secid(c) for c in chunk)
        try:
            r = _SESSION.get("https://qt.gtimg.cn/q=" + q, timeout=15)
            r.encoding = "gbk"
        except Exception:  # noqa: BLE001
            continue
        for line in r.text.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            val = line.split("=", 1)[1].strip('"')
            if not val:
                continue
            a = val.split("~")
            if len(a) < 46:
                continue
            out[a[2]] = {"name": a[1], "price": _f(a[3]),
                         "float_cap": _f(a[44]) * 1e8, "total_cap": _f(a[45]) * 1e8}
        time.sleep(0.2)
    return out


def _seed_spot(top: int) -> pd.DataFrame | None:
    """兜底源：读 universe_seed.txt 的高流动性票，用腾讯补名称/流通市值。
    成交额/涨跌幅等留空，后续由K线打分（盘前实时数据归零时仍可用）。"""
    seed = pathlib.Path(__file__).with_name("universe_seed.txt")
    if not seed.exists():
        return None
    codes = [ln.strip() for ln in seed.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if not codes:
        return None
    log(f"  种子 universe: {len(codes)} 只，用腾讯补名称/流通市值")
    q = _tx_quote(codes)
    rows = [{"代码": c, "名称": q.get(c, {}).get("name", c),
             "最新价": q.get(c, {}).get("price", np.nan), "涨跌幅": np.nan,
             "成交量": np.nan, "成交额": np.nan, "换手率": np.nan, "量比": np.nan,
             "总市值": q.get(c, {}).get("total_cap", np.nan),
             "流通市值": q.get(c, {}).get("float_cap", np.nan)} for c in codes]
    return pd.DataFrame(rows)


def get_spot(top_by_amount: int = 600) -> pd.DataFrame:
    """全市场A股快照：按成交额降序取前 top_by_amount 只。东财→新浪→种子 自动回退。"""
    log(f"拉取全市场成交额前 {top_by_amount} 只 ...")
    cached = _cache_get("spot", f"all_{top_by_amount}", max_age_h=6)
    if cached is not None:
        log(f"  命中缓存，{len(cached)} 行")
        return pd.DataFrame(cached)
    for name, fn in (("eastmoney", _em_spot), ("sina", _sina_spot), ("seed", _seed_spot)):
        df = fn(top_by_amount)
        if df is not None and len(df) > 0:
            log(f"  数据源={name}，收到 {len(df)} 行")
            _cache_put("spot", f"all_{top_by_amount}", df.to_dict("list"))
            return df
    raise RuntimeError("所有行情源均不可用（eastmoney + sina + seed 都失败）")


def _secid(code: str) -> str:
    """股票代码 → 东财 secid。沪市(6/68/9/5)=1.，深市(0/3)=0.。"""
    code = str(code)
    return f"1.{code}" if code.startswith(("60", "68", "9", "5")) else f"0.{code}"


_TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _tx_secid(code: str) -> str:
    return ("sh" if code.startswith(("60", "68", "9", "5")) else "sz") + str(code)


def _em_kline(code: str, bars: int = 160) -> pd.DataFrame | None:
    """东财前复权日线（主源），快速失败。被限流后熔断跳过。"""
    global _EM_DEAD
    if _EM_DEAD:
        return None
    params = {
        "secid": _secid(code), "klt": 101, "fqt": 1,
        "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "lmt": bars, "end": "20500101",
    }
    try:
        data = _get(_KLINE_HOSTS, _KLINE_PATH, params, retries=2).get("data")
    except Exception:  # noqa: BLE001
        _EM_DEAD = True
        return None
    if not data or not data.get("klines"):
        return None
    recs = [ln.split(",") for ln in data["klines"]]
    df = pd.DataFrame(recs, columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"])
    for c in ("开盘", "收盘", "最高", "最低", "成交量", "成交额"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _tx_kline(code: str, bars: int = 160) -> pd.DataFrame | None:
    """腾讯前复权日线（回退源）。"""
    sec = _tx_secid(code)
    try:
        r = _SESSION.get(_TX_KLINE, params={"param": f"{sec},day,,,{bars},qfq"}, timeout=15)
        node = r.json().get("data", {}).get(sec, {})
    except Exception:  # noqa: BLE001
        return None
    kl = node.get("qfqday") or node.get("day") or []
    if not kl:
        return None
    df = pd.DataFrame([row[:6] for row in kl],
                      columns=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
    for c in ("开盘", "收盘", "最高", "最低", "成交量"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def get_kline(code: str, bars: int = 160) -> pd.DataFrame | None:
    """前复权日线（日期/开盘/收盘/最高/最低/成交量）。东财→腾讯，带当天缓存。"""
    cached = _cache_get("klines", code, max_age_h=60)  # 收盘后不变，TTL覆盖周末
    if cached is not None:
        df = pd.DataFrame(cached)
        if len(df) >= 25:
            return df
    for fn in (_em_kline, _tx_kline):
        df = fn(code, bars)
        if df is not None and len(df) >= 25:
            _cache_put("klines", code, df.to_dict("list"))
            return df
    return None


def _board_name_to_code() -> dict[str, str]:
    """东财 行业板块 + 概念板块 名称 → 板块代码(BKxxxx)。"""
    out: dict[str, str] = {}
    for fs in ("m:90 t:2", "m:90 t:3"):  # t:2 行业, t:3 概念
        try:
            for it in _clist_top(fs, "f12,f14", fid="f3", pz=600):
                out[str(it.get("f14"))] = str(it.get("f12"))
        except Exception as e:  # noqa: BLE001
            log(f"  板块列表拉取失败 ({fs}): {e}")
    return out


def fetch_sector_spot(sector: str) -> pd.DataFrame:
    """按板块名（模糊匹配行业/概念板块）直接拉成分股快照。失败则回退全市场。"""
    bmap = _board_name_to_code()
    matched = {name: bk for name, bk in bmap.items() if sector in name or name in sector}
    if not matched:
        log(f"  ⚠ 未匹配到板块『{sector}』，回退全市场")
        return get_spot()
    log(f"  匹配板块: {list(matched.keys())}")
    rows: list[dict] = []
    for name, bk in matched.items():
        try:
            rows.extend(_clist_top(f"b:{bk}", _SPOT_FIELDS, fid="f6", pz=1000))
        except Exception as e:  # noqa: BLE001
            log(f"  板块成分拉取失败 {name}({bk}): {e}")
        time.sleep(0.3)
    if not rows:
        log("  板块成分为空，回退全市场")
        return get_spot()
    df = pd.DataFrame(rows).rename(columns=_COL_MAP).drop_duplicates("代码")
    log(f"  板块成分股合计 {len(df)} 只")
    return df


# ---------------------------------------------------------------- 初筛

def prefilter(
    df: pd.DataFrame,
    min_float_cap: float = 15e8,
    min_amount: float = 1e8,
    max_chg: float = 21.0,
    min_chg: float = -7.0,
) -> pd.DataFrame:
    """剔除 ST/退市/北交所/低流动性/大跌票。涨停票保留（后续按连板/一字板打标处理）。"""
    out = df.copy()
    n0 = len(out)

    out = out[~out["名称"].astype(str).str.contains("ST|退", case=False, na=False)]
    n1 = len(out)

    code = out["代码"].astype(str)
    out = out[code.str.match(r"^(60|00|30|68)")]
    n2 = len(out)

    out = out[pd.to_numeric(out["流通市值"], errors="coerce") >= min_float_cap]
    n3 = len(out)

    amt = pd.to_numeric(out["成交额"], errors="coerce")
    if amt.notna().any():  # 成交额仅在有实时/排行数据时过滤；种子/盘前缺失则跳过
        out = out[amt >= min_amount]
    n4 = len(out)

    chg = pd.to_numeric(out["涨跌幅"], errors="coerce")
    out = out[(chg < max_chg) & (chg > min_chg)]
    n5 = len(out)

    # 量比仅东财提供；新浪源全为 NaN 时跳过此过滤（用K线 vr 代偿）
    lb = pd.to_numeric(out["量比"], errors="coerce")
    if lb.notna().any():
        out = out[lb >= 1.0]
    n6 = len(out)

    log(
        "初筛漏斗: "
        f"{n0} →非ST {n1} →主板/双创 {n2} →流通≥{min_float_cap/1e8:.0f}亿 {n3} "
        f"→成交额≥{min_amount/1e8:.1f}亿 {n4} →非涨跌停 {n5} →量比 {n6}"
    )
    return out


def prescore(df: pd.DataFrame) -> pd.DataFrame:
    """快照粗排：量比/换手率/涨幅，挑出值得拉K线的候选。"""
    d = df.copy()
    for c in ("量比", "换手率", "涨跌幅"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    turn = d["换手率"].clip(0, 30)
    turn_score = 1 - (turn - 8).abs() / 22
    # 量比缺失（新浪源）时，该项给中性 0.5，权重让渡给换手率/涨幅
    vr_rank = d["量比"].rank(pct=True)
    if d["量比"].notna().any():
        vr_rank = vr_rank.fillna(0.5)
        d["prescore"] = (
            vr_rank * 0.5 + turn_score * 0.3 + d["涨跌幅"].clip(0, 9).rank(pct=True) * 0.2
        )
    else:
        d["prescore"] = (
            turn_score.rank(pct=True) * 0.6 + d["涨跌幅"].clip(0, 9).rank(pct=True) * 0.4
        )
    return d.sort_values("prescore", ascending=False)


# ---------------------------------------------------------------- 历史因子

def hist_factors(code: str, name: str) -> dict | None:
    """日线 → MA排列/动量/量比/ATR/MACD/区间位置。"""
    h = get_kline(code)
    if h is None or len(h) < 25:
        return None

    open_, close, high, low, vol = h["开盘"], h["收盘"], h["最高"], h["最低"], h["成交量"]
    ma5, ma10, ma20 = (close.rolling(w).mean() for w in (5, 10, 20))
    last = float(close.iloc[-1])
    bull = bool(ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1])
    ret5 = last / float(close.iloc[-6]) - 1 if len(close) > 6 else np.nan
    ret20 = last / float(close.iloc[-21]) - 1 if len(close) > 21 else np.nan
    vr = (
        float(vol.iloc[-5:].mean()) / float(vol.iloc[-20:].mean())
        if len(vol) >= 20 and vol.iloc[-20:].mean() > 0 else np.nan
    )

    prev = close.shift()
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    dist_ma10 = last / float(ma10.iloc[-1]) - 1
    win = min(60, len(close))
    lo, hi = float(low.iloc[-win:].min()), float(high.iloc[-win:].max())
    rng_pos = (last - lo) / (hi - lo) if hi > lo else 0.5

    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    macd_gold = bool(dif.iloc[-1] > dea.iloc[-1])

    # 最后一个完整交易日的 成交额 与 涨跌幅（盘前/收盘后实时快照会归零，用K线兜底）
    if "成交额" in h.columns and pd.notna(h["成交额"].iloc[-1]):
        amt_last = float(h["成交额"].iloc[-1])
    else:  # 腾讯K线无成交额，用 量(手)×100×收盘 估算
        amt_last = float(vol.iloc[-1]) * 100 * last
    daily_chg = close.pct_change() * 100
    chg_last = float(daily_chg.iloc[-1]) if len(close) > 1 else np.nan

    # === Phase1 盘口形态（用周五当天 OHLC，对周一方向高度预测）===
    o1, h1, l1, c1 = (float(open_.iloc[-1]), float(high.iloc[-1]),
                      float(low.iloc[-1]), last)
    day_rng = h1 - l1
    tail_strength = (c1 - l1) / day_rng if day_rng > 0 else 0.5  # 尾盘收在区间高位=主力锁仓
    is_yang = bool(c1 > o1)
    body_ratio = abs(c1 - o1) / day_rng if day_rng > 0 else 0.0  # 实体占比
    vol20 = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else float(vol.mean())
    vol_today = float(vol.iloc[-1]) / vol20 if vol20 > 0 else np.nan  # 当日放量倍数

    # === Phase1 涨停/连板（创业板/科创 20%，其余 10%）===
    limit_pct = 20.0 if code.startswith(("30", "68")) else 10.0
    thr = limit_pct * 0.985
    limit_up_today = bool(chg_last >= thr) if chg_last == chg_last else False
    streak = 0
    for v in daily_chg.iloc[::-1]:
        if v == v and v >= thr:
            streak += 1
        else:
            break
    oneword = bool(limit_up_today and day_rng / float(close.iloc[-2]) < 0.015)  # 一字板买不进

    # === Phase1 回调买点（强势股回踩MA10不破、今日企稳收阳）===
    pullback = bool(bull and ret20 == ret20 and ret20 > 0.05
                    and -3 <= dist_ma10 * 100 <= 5 and is_yang)

    return dict(
        code=code, name=name, close=round(last, 2), bull=bull,
        ret5=round(ret5 * 100, 2), ret20=round(ret20 * 100, 2),
        vr=round(vr, 2) if vr == vr else None,
        atr14=round(atr14, 3), atr_pct=round(atr14 / last * 100, 2),
        dist_ma10=round(dist_ma10 * 100, 2), rng_pos=round(rng_pos * 100, 1),
        macd_gold=macd_gold, ma5=round(float(ma5.iloc[-1]), 2),
        ma10=round(float(ma10.iloc[-1]), 2), ma20=round(float(ma20.iloc[-1]), 2),
        amt_yi_kline=round(amt_last / 1e8, 2),
        chg_last=round(chg_last, 2) if chg_last == chg_last else None,
        last_date=str(h["日期"].iloc[-1])[:10],
        tail_strength=round(tail_strength, 2), is_yang=is_yang,
        body_ratio=round(body_ratio, 2),
        vol_today=round(vol_today, 2) if vol_today == vol_today else None,
        limit_up_today=limit_up_today, limit_streak=streak, oneword=oneword,
        pullback=pullback,
    )


def composite(f: dict, weights: dict | None = None) -> dict:
    """综合因子打分(v2): 动量35 + 量能25 + 技术25 + 盘口15 + 回调10 − 过热/连板惩罚。

    weights 可由 --backtest 的 IC 结果覆盖默认配比（传 {mom,vol,tech,tape,pull}）。
    """
    w = {"mom": 1.0, "vol": 1.0, "tech": 1.0, "tape": 1.0, "pull": 1.0}
    if weights:
        w.update(weights)

    ret5 = f["ret5"] if f["ret5"] == f["ret5"] else 0
    ret20 = f["ret20"] if f["ret20"] == f["ret20"] else 0
    vr = f["vr"] if f["vr"] else 1.0
    vol_today = f.get("vol_today") or 1.0
    tail = f.get("tail_strength", 0.5)
    dist = f["dist_ma10"]

    # 动量 0~35
    mom = min(max(ret5, -5), 12) / 12 * 18 + min(max(ret20, -15), 25) / 25 * 17
    # 量能 0~25：5/20日量比 + 当日放量
    vol = min(max(vr - 1, 0), 1.2) / 1.2 * 15 + min(max(vol_today - 1, 0), 2) / 2 * 10
    # 技术 0~25
    tech = ((12 if f["bull"] else 0) + (7 if f["macd_gold"] else 0)
            + (6 if -2 <= dist <= 6 else 0))
    # 盘口 0~15：尾盘强弱 + 收阳 + 实体
    tape = tail * 8 + (4 if f.get("is_yang") else 0) + (3 if f.get("body_ratio", 0) > 0.4 else 0)
    # 回调买点 0~10
    pull = 10 if f.get("pullback") else 0

    # 惩罚：过度乖离 / 极端高位 / 高位连板（追高风险）
    pen = ((10 if dist > 15 else (5 if dist > 10 else 0))
           + (8 if f["rng_pos"] > 95 else 0)
           + (10 if f.get("limit_streak", 0) >= 3 else 0))

    mom, vol, tech, tape, pull = (mom * w["mom"], vol * w["vol"], tech * w["tech"],
                                  tape * w["tape"], pull * w["pull"])
    score = max(0.0, min(100.0, mom + vol + tech + tape + pull - pen))
    f.update(
        score=round(score, 1), mom=round(mom, 1), vol_score=round(vol, 1),
        tech=round(tech, 1), tape_score=round(tape, 1), pull_score=round(pull, 1),
        penalty=pen,
    )
    return f


def risk_assess(f: dict) -> dict:
    """客观技术风险评分(0~100，越高越危险) + 等级。基本面风险由 Agent③ 叠加。"""
    rs, reasons = 0, []
    rp = f.get("rng_pos", 50)
    if rp > 95:
        rs += 25; reasons.append(f"60日位{rp:.0f}极高")
    elif rp > 85:
        rs += 15; reasons.append(f"60日位{rp:.0f}偏高")
    elif rp > 70:
        rs += 5
    dm = f.get("dist_ma10", 0)
    if dm > 15:
        rs += 20; reasons.append(f"乖离MA10+{dm:.0f}%")
    elif dm > 10:
        rs += 12; reasons.append(f"乖离MA10+{dm:.0f}%")
    elif dm > 5:
        rs += 5
    ap = f.get("atr_pct") or 0
    if ap > 8:
        rs += 15; reasons.append(f"波动大ATR{ap:.0f}%")
    elif ap > 6:
        rs += 10
    elif ap > 4:
        rs += 5
    ls = f.get("limit_streak", 0)
    if ls >= 3:
        rs += 25; reasons.append(f"{ls}连板追高")
    elif ls == 2:
        rs += 12; reasons.append("2连板")
    if f.get("oneword"):
        rs += 10; reasons.append("一字板难买")
    amt = f.get("amount_yi") or f.get("amt_yi_kline") or 99
    if amt < 2:
        rs += 10; reasons.append(f"成交仅{amt:.1f}亿")
    fc = f.get("float_cap_yi")
    if fc and fc < 50:
        rs += 8; reasons.append(f"小盘{fc:.0f}亿")
    rs = min(100, rs)
    level = "低" if rs < 25 else ("中" if rs < 45 else ("中高" if rs < 65 else "高"))
    f["risk_score"] = rs
    f["risk_level"] = level
    f["risk_reasons"] = reasons[:3]
    return f


def buy_plan(f: dict) -> dict:
    """按 风险/信号/ATR 生成可执行买入方案：买入区间 + 仓位 + 入场方式 + 放弃条件 + 止损。
    （T+1 跳空开盘，方案均含竞价应对；总仓纪律单票≤10%。）"""
    close = f["close"]
    ma5 = f.get("ma5") or close
    atr = f.get("atr14") or close * 0.05
    rl = f.get("risk_level", "中")
    ls = f.get("limit_streak", 0)

    if f.get("oneword"):
        lo = hi = round(close, 2)
        pos, mode = 0, "放弃/打板客竞价挂涨停价"
        tactic = "一字板买不进：常规放弃；打板客可竞价挂涨停价博成交，仓位自控"
    elif f.get("pullback"):                      # 回踩低吸（最优）
        lo, hi = round(min(ma5, close) * 0.99, 2), round(close, 2)
        pos, mode = (10 if rl in ("低", "中") else 6), "回踩低吸"
        tactic = "竞价/盘中回踩MA5附近首笔60%，再回踩补40%"
    elif ls >= 2 or rl == "高":                  # 高位连板 / 高风险
        lo, hi = round(close * 0.97, 2), round(close, 2)
        pos, mode = 3, "轻仓博弈"
        tactic = "追高风险大：仅轻仓，只接回踩，竞价高开>3%放弃"
    elif f.get("dist_ma10", 0) > 9:              # 偏离MA10过远：不追，等回调
        lo, hi = round(close * 0.96, 2), round(close * 0.99, 2)
        pos, mode = (6 if rl in ("低", "中") else 4), "等回调低吸"
        tactic = f"已偏离MA10 +{f.get('dist_ma10',0):.0f}%，不追高；只在回调到{round(close*0.96,2)}–{round(close*0.99,2)}买"
    elif f.get("dist_ma10", 0) <= 4 and f.get("bull"):   # 贴均线/突破
        lo, hi = round(close, 2), round(close * 1.01, 2)
        pos, mode = (10 if rl == "低" else 8), "突破追入"
        tactic = "竞价不高开>2%首笔半仓，放量破前高补仓"
    else:                                        # 默认：分批试探
        lo, hi = round(close * 0.99, 2), round(close * 1.005, 2)
        pos, mode = (8 if rl in ("低", "中") else 5), "分批试探"
        tactic = "竞价试探半仓，看盘中转强再补仓"

    stop = round(max(close - 1.3 * atr, close * 0.94), 2)
    gap_lim = 2 if rl in ("中高", "高") else 3
    abort = f"竞价高开>{gap_lim}% 或 跌破{stop} 放弃"

    f.update(buy_low=lo, buy_high=hi, buy_zone=(f"{lo}" if lo == hi else f"{lo}–{hi}"),
             position_pct=pos, entry_mode=mode, entry_tactic=tactic,
             stop=stop, abort=abort)
    return f


# ---------------------------------------------------------------- 主流程

def _auto_weights(hold_days: int) -> dict | None:
    """按持有天数自动调因子配比：超短线重盘口/动量，偏波段重趋势。"""
    if hold_days <= 3:      # 超短线(T+1~T+3)：盘口/动量/反转更重
        return {"mom": 1.15, "vol": 1.1, "tech": 0.9, "tape": 1.25, "pull": 1.1}
    if hold_days >= 10:     # 偏波段：趋势/技术更重，盘口噪音降权
        return {"mom": 1.0, "vol": 0.9, "tech": 1.2, "tape": 0.7, "pull": 0.9}
    return None             # 5日左右用默认均衡配比


_DOW_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _trade_calendar() -> list[str] | None:
    """A股官方交易日历(含节假日)。优先 akshare(权威, 非东财源)，缓存30天。"""
    c = _cache_get("calendar", "sse", max_age_h=24 * 30)
    if c:
        return c
    try:
        import akshare as ak  # 仅日历用，走 Sina 源，不受东财限流影响
        df = ak.tool_trade_date_hist_sina()
        days = sorted({str(x)[:10] for x in df["trade_date"].tolist()})
        if days:
            _cache_put("calendar", "sse", days)
            return days
    except Exception as e:  # noqa: BLE001
        log(f"  交易日历获取失败({str(e)[:40]})，回退仅跳周末")
    return None


def _trade_window(last_date: str, hold_days: int) -> dict:
    """由最新交易日 T 算 买入日 T+1 与 最晚卖出日 T+N 的真实日期（含节假日）。"""
    try:
        d0 = dt.date.fromisoformat(str(last_date)[:10])
    except Exception:  # noqa: BLE001
        return {"as_of": str(last_date), "buy_date": "?", "sell_by": "?",
                "authoritative": False, "note": ""}

    cal = _trade_calendar()
    s0 = d0.isoformat()
    if cal and s0 in cal:
        idx = cal.index(s0)
        fut = cal[idx + 1: idx + 1 + hold_days]
    elif cal:
        fut = [x for x in cal if x > s0][:hold_days]
    else:
        fut = []

    if len(fut) >= hold_days:
        t1, tn = fut[0], fut[hold_days - 1]
        t1d, tnd = dt.date.fromisoformat(t1), dt.date.fromisoformat(tn)
        gap1 = (t1d - d0).days
        # 窗口内日历天数 vs 交易天数：差得越多说明夹了越多非交易日(周末/假期)
        span = (tnd - d0).days
        has_holiday = gap1 > 3 or span > hold_days + 3  # 长假特征
        note = ""
        if gap1 > 3:
            note = f"T→T+1 间隔{gap1}天（含节假日，注意长假跳空）"
        elif gap1 > 1:
            note = "T+1 跨周末"
        return {"as_of": s0, "as_of_dow": _DOW_CN[d0.weekday()],
                "buy_date": t1, "buy_dow": _DOW_CN[t1d.weekday()],
                "sell_by": tn, "sell_dow": _DOW_CN[tnd.weekday()],
                "authoritative": True, "has_holiday": has_holiday, "note": note}

    # 回退：仅跳周末（无权威日历时）
    def step(d):
        nxt = d + dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += dt.timedelta(days=1)
        return nxt
    t1d = step(d0)
    tnd = t1d
    for _ in range(hold_days - 1):
        tnd = step(tnd)
    return {"as_of": s0, "as_of_dow": _DOW_CN[d0.weekday()],
            "buy_date": t1d.isoformat(), "buy_dow": _DOW_CN[t1d.weekday()],
            "sell_by": tnd.isoformat(), "sell_dow": _DOW_CN[tnd.weekday()],
            "authoritative": False, "has_holiday": False,
            "note": "（日历未取到，仅跳周末，遇节假日可能偏差）"}


def run(sector: str | None, pool: int, top: int, out_path: str | None,
        weights: dict | None = None, hold_days: int = 5) -> dict:
    if weights is None:
        weights = _auto_weights(hold_days)
    if sector and sector not in ("全市场", "all", "ALL"):
        spot = fetch_sector_spot(sector)
    else:
        spot = get_spot()

    filt = prefilter(spot)
    if filt.empty:
        log("初筛后无候选，放宽阈值重试")
        filt = prefilter(spot, min_float_cap=8e8, min_amount=5e7)
    # 无成交额信号（种子/盘前）时跳过粗排，全部送K线打分
    if pd.to_numeric(filt["成交额"], errors="coerce").notna().sum() == 0:
        log("  无成交额信号(种子/盘前)，跳过粗排，全部候选送K线打分")
        ranked_pre = filt.head(max(pool, 150))
    else:
        ranked_pre = prescore(filt).head(pool)
    log(f"进入历史因子计算的候选: {len(ranked_pre)} 只")

    rows = []
    for i, (_, r) in enumerate(ranked_pre.iterrows(), 1):
        code, name = str(r["代码"]), str(r["名称"])
        f = hist_factors(code, name)
        time.sleep(0.15)
        if f is None:
            continue
        f = composite(f, weights)
        spot_chg = pd.to_numeric(r.get("涨跌幅"), errors="coerce")
        spot_turn = pd.to_numeric(r.get("换手率"), errors="coerce")
        spot_amt = pd.to_numeric(r.get("成交额"), errors="coerce")
        spot_fcap = pd.to_numeric(r.get("流通市值"), errors="coerce")
        # 实时快照在盘前/收盘后会归零或缺失 → 用K线最后完整交易日兜底
        f["chg_today"] = round(float(spot_chg), 2) if pd.notna(spot_chg) and spot_chg != 0 else f.get("chg_last")
        f["turnover"] = round(float(spot_turn), 2) if pd.notna(spot_turn) and spot_turn != 0 else None
        f["amount_yi"] = (round(float(spot_amt) / 1e8, 2) if pd.notna(spot_amt) and spot_amt > 0
                           else f.get("amt_yi_kline"))
        f["float_cap_yi"] = round(float(spot_fcap) / 1e8, 1) if pd.notna(spot_fcap) and spot_fcap > 0 else None
        f = risk_assess(f)  # 客观技术风险分(spot字段就绪后算)
        f = buy_plan(f)     # 买入方案
        rows.append(f)
        if i % 10 == 0:
            log(f"  已处理 {i}/{len(ranked_pre)}")

    rows.sort(key=lambda x: x["score"], reverse=True)
    # T = 最新交易日；用权威交易日历算 T+1(买入)、T+N(最晚卖出)
    dates = [r.get("last_date") for r in rows if r.get("last_date")]
    as_of = max(dates) if dates else dt.date.today().isoformat()
    win = _trade_window(as_of, hold_days)
    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "hold_days": hold_days,         # 最多持有交易日数
        "sector": sector or "全市场",
        "weights": weights or "默认均衡",
        "calendar_authoritative": win["authoritative"],
        "universe_after_filter": int(len(filt)),
        "scored": len(rows),
        "candidates": rows[:top],
    }
    result.update(win)  # as_of/buy_date/sell_by + 各自星期 + note
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        log(f"已写出 JSON: {out_path}")
    return result


def _signal_tag(c: dict) -> str:
    """汇总盘口/涨停信号成一个短标记。"""
    tags = []
    s = c.get("limit_streak", 0)
    if c.get("oneword"):
        tags.append("一字板!")
    elif s >= 1:
        tags.append(f"{s}连板")
    if c.get("pullback"):
        tags.append("回踩")
    if c.get("is_yang") and c.get("tail_strength", 0) >= 0.7:
        tags.append("尾盘强")
    return "/".join(tags)


def print_table(result: dict) -> None:
    auth = "" if result.get("calendar_authoritative") else " [日历未取到,仅跳周末]"
    note = result.get("note", "")
    note_s = f"  ⚠{note}" if note else ""
    print(f"\n## Agent 1 量化筛选结果 — {result['sector']}")
    print(f"T(数据截止)={result.get('as_of','?')} {result.get('as_of_dow','')}  "
          f"→ 买入日 T+1={result.get('buy_date','?')} {result.get('buy_dow','')}  "
          f"→ 最晚卖出 T+{result.get('hold_days','?')}={result.get('sell_by','?')} {result.get('sell_dow','')}"
          f"{auth}{note_s}")
    print(f"初筛后universe={result['universe_after_filter']}  打分={result['scored']}  "
          f"输出Top {len(result['candidates'])}  (生成于 {result['generated_at']})\n")
    hdr = ["代码", "名称", "综合", "风险", "动量", "量能", "技术", "盘口", "回调", "收盘", "今涨%",
           "5日%", "20日%", "量比", "当日量", "尾盘", "距MA10", "60位", "ATR%", "额亿",
           "流通亿", "信号", "多头", "MACD"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "|".join(["---"] * len(hdr)) + "|")
    for c in result["candidates"]:
        risk = f"{c.get('risk_level','')}({c.get('risk_score','')})"
        print(
            f"| {c['code']} | {c['name']} | {c['score']} | {risk} | {c['mom']} | {c['vol_score']} | "
            f"{c['tech']} | {c.get('tape_score','')} | {c.get('pull_score','')} | {c['close']} | "
            f"{c.get('chg_today','')} | {c['ret5']} | {c['ret20']} | {c.get('vr','')} | "
            f"{c.get('vol_today','')} | {c.get('tail_strength','')} | {c['dist_ma10']} | "
            f"{c['rng_pos']} | {c.get('atr_pct','')} | {c.get('amount_yi','')} | "
            f"{c.get('float_cap_yi','')} | {_signal_tag(c)} | "
            f"{'是' if c['bull'] else '否'} | {'是' if c['macd_gold'] else '否'} |"
        )
    # 买入方案表（可执行：区间/仓位/方式/止损/放弃）
    print("\n### 买入方案（T+1 执行）")
    bh = ["排名", "代码", "名称", "买入区间", "建议仓位", "入场方式", "止损", "放弃条件"]
    print("| " + " | ".join(bh) + " |")
    print("|" + "|".join(["---"] * len(bh)) + "|")
    for i, c in enumerate(result["candidates"], 1):
        print(f"| {i} | {c['code']} | {c['name']} | {c.get('buy_zone','')} | "
              f"{c.get('position_pct','')}% | {c.get('entry_mode','')}：{c.get('entry_tactic','')} | "
              f"{c.get('stop','')} | {c.get('abort','')} |")

    # Agent③ 重点关注：买不进 / 追高风险
    oneword = [c["code"] for c in result["candidates"] if c.get("oneword")]
    hot = [f"{c['code']}({c['limit_streak']}板)" for c in result["candidates"] if c.get("limit_streak", 0) >= 3]
    if oneword:
        print(f"\n⚠ 一字板(次日难买入): {', '.join(oneword)}")
    if hot:
        print(f"⚠ 高位连板(追高风险): {', '.join(hot)}")
    print("\n候选股代码（供 Agent 2 查催化剂）:", ", ".join(c["code"] for c in result["candidates"]))


# ---------------------------------------------------------------- Phase2 因子IC回测
# qlib Alpha158 / gtja191 风格的公式化因子，与本引擎自有因子一起做 IC 检验。
# 每个因子按经济含义归入 5 个评分桶，回测出的 |IC| 决定各桶权重（数据驱动，替代手拍）。
FACTOR_BUCKETS = {
    "mom":  ["mom5", "mom20", "mom60", "ma_dev20"],     # 动量
    "vol":  ["vol_ratio", "vol_today", "vstd20"],        # 量能/波动
    "tech": ["bull", "ma_dev5", "rsv", "rngpos60"],      # 趋势/位置
    "tape": ["tail", "kmid", "kup", "klow"],             # 盘口形态(qlib K线因子)
    "pull": ["rev1"],                                    # 短期反转/回调
}


def _factors_at(o, c, h, l, v, t: int) -> dict:
    """在第 t 根K线(仅用 ≤t 的数据，无未来函数)计算一组公式化因子。"""
    def chg(n):
        return c[t] / c[t - n] - 1 if t - n >= 0 and c[t - n] > 0 else np.nan

    def ma(n):
        return c[t - n + 1:t + 1].mean() if t - n + 1 >= 0 else np.nan

    out = {"mom5": chg(5), "mom20": chg(20), "mom60": chg(60),
           "rev1": -chg(1) if chg(1) == chg(1) else np.nan}
    m5, m10, m20 = ma(5), ma(10), ma(20)
    out["ma_dev5"] = c[t] / m5 - 1 if m5 == m5 and m5 > 0 else np.nan
    out["ma_dev20"] = c[t] / m20 - 1 if m20 == m20 and m20 > 0 else np.nan
    out["bull"] = 1.0 if (m5 == m5 and m10 == m10 and m20 == m20 and m5 > m10 > m20) else 0.0
    v5 = v[t - 4:t + 1].mean() if t - 4 >= 0 else np.nan
    v20 = v[t - 19:t + 1].mean() if t - 19 >= 0 else np.nan
    out["vol_ratio"] = v5 / v20 if v20 == v20 and v20 > 0 else np.nan
    out["vol_today"] = v[t] / v20 if v20 == v20 and v20 > 0 else np.nan
    seg = c[max(0, t - 20):t + 1]
    rets = seg[1:] / seg[:-1] - 1 if len(seg) > 1 else np.array([])
    out["vstd20"] = float(np.std(rets)) if len(rets) > 3 else np.nan
    win_l, win_h = l[max(0, t - 8):t + 1], h[max(0, t - 8):t + 1]
    rng = win_h.max() - win_l.min()
    out["rsv"] = (c[t] - win_l.min()) / rng if rng > 0 else np.nan
    drng = h[t] - l[t]
    out["tail"] = (c[t] - l[t]) / drng if drng > 0 else 0.5
    out["kmid"] = (c[t] - o[t]) / o[t] if o[t] > 0 else np.nan      # qlib KMID
    out["kup"] = (h[t] - max(o[t], c[t])) / o[t] if o[t] > 0 else np.nan   # 上影
    out["klow"] = (min(o[t], c[t]) - l[t]) / o[t] if o[t] > 0 else np.nan  # 下影
    w_l, w_h = l[max(0, t - 59):t + 1], h[max(0, t - 59):t + 1]
    r60 = w_h.max() - w_l.min()
    out["rngpos60"] = (c[t] - w_l.min()) / r60 if r60 > 0 else np.nan
    return out


def backtest(sector: str | None, sample: int, fwd: int = 5, step: int = 5,
             hist_bars: int = 200) -> dict:
    """对样本股做横截面因子 IC 回测：因子(T) vs 未来 fwd 日收益。输出每因子 IC/ICIR + 建议权重。"""
    log(f"=== 因子IC回测：样本≤{sample} 只，预测未来{fwd}日收益 ===")
    spot = fetch_sector_spot(sector) if sector and sector not in ("全市场", "all") else get_spot()
    filt = prefilter(spot)
    codes = [str(x) for x in filt["代码"].tolist()][:sample]
    log(f"  样本股 {len(codes)} 只，拉K线计算因子面板 ...")

    recs = []
    for i, code in enumerate(codes, 1):
        h = get_kline(code, bars=hist_bars)
        if h is None or len(h) < 90:
            continue
        o, c, hh, ll, vv = (h[k].to_numpy(dtype=float)
                            for k in ("开盘", "收盘", "最高", "最低", "成交量"))
        dates = h["日期"].astype(str).to_numpy()
        for t in range(70, len(c) - fwd, step):
            if c[t] <= 0:
                continue
            fac = _factors_at(o, c, hh, ll, vv, t)
            fac["_fwd"] = c[t + fwd] / c[t] - 1
            fac["_date"] = dates[t]
            recs.append(fac)
        time.sleep(0.1)
        if i % 20 == 0:
            log(f"  已处理 {i}/{len(codes)}")

    if not recs:
        raise RuntimeError("回测无数据（行情源不可用）")
    df = pd.DataFrame(recs)
    factor_cols = [col for col in df.columns if not col.startswith("_")]

    # 逐日横截面 rank IC（Spearman），再取均值/ICIR
    ic_rows = []
    for col in factor_cols:
        per = []
        for _, g in df.groupby("_date"):
            sub = g[[col, "_fwd"]].dropna()
            if len(sub) >= 5:
                ic = sub[col].corr(sub["_fwd"], method="spearman")
                if ic == ic:
                    per.append(ic)
        if per:
            m, s = float(np.mean(per)), float(np.std(per))
            ic_rows.append({"factor": col, "IC": round(m, 4), "ICIR": round(m / (s + 1e-9), 3),
                            "absIC": abs(m), "n_days": len(per)})
    ic_rows.sort(key=lambda x: -x["absIC"])

    # 桶权重 = 桶内因子平均|IC|，归一到均值≈1
    bw = {}
    for b, cols in FACTOR_BUCKETS.items():
        vals = [r["absIC"] for r in ic_rows if r["factor"] in cols]
        bw[b] = float(np.mean(vals)) if vals else 0.0
    base = np.mean([x for x in bw.values() if x > 0]) or 1.0
    weights = {b: round(v / base, 2) if v > 0 else 0.6 for b, v in bw.items()}

    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "fwd_days": fwd, "n_records": len(df), "n_dates": int(df["_date"].nunique()),
            "n_stocks": len(codes), "ic": ic_rows, "weights": weights}


def print_backtest(bt: dict) -> None:
    print(f"\n## 因子 IC 回测  ({bt['generated_at']})")
    print(f"样本股={bt['n_stocks']}  样本点={bt['n_records']}  截面日数={bt['n_dates']}  预测窗口={bt['fwd_days']}日\n")
    print("| 因子 | IC均值 | ICIR | |IC| | 截面日数 |")
    print("|---|---|---|---|---|")
    for r in bt["ic"]:
        print(f"| {r['factor']} | {r['IC']} | {r['ICIR']} | {round(r['absIC'],4)} | {r['n_days']} |")
    print("\n按经济含义归桶后的**建议权重**（写入 weights.json，可 --weights auto 调用）:")
    print("  " + "  ".join(f"{k}={v}" for k, v in bt["weights"].items()))
    print("\n解读：IC>0 因子值越大未来越涨；|IC|>0.03 即有效，>0.05 较强；ICIR>0.5 稳定。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly A-share quant screener (Agent 1 engine, 0-API)")
    ap.add_argument("--sector", default=None, help="行业/概念板块名，如 光通信；省略=全市场")
    ap.add_argument("--pool", type=int, default=40, help="进入历史因子计算的候选数")
    ap.add_argument("--top", type=int, default=15, help="最终输出候选数")
    ap.add_argument("--out", default=None, help="把结果 JSON 写到此路径")
    ap.add_argument("--hold-days", type=int, default=5, help="最多持有交易日数N(T+1买入,持有≤N)；驱动权重与回测窗口")
    ap.add_argument("--backtest", action="store_true", help="运行因子IC回测并产出建议权重")
    ap.add_argument("--bt-sample", type=int, default=60, help="回测样本股数")
    ap.add_argument("--weights", default=None,
                    help="权重: 默认自动检测同目录weights.json(需持有天数匹配且<14天); 'auto'=强制用; 路径=指定; 'none'=用默认配比")
    ap.add_argument("--no-cache", action="store_true", help="禁用缓存")
    ap.add_argument("--refresh", action="store_true", help="强制重拉(绕过读缓存)")
    args = ap.parse_args()

    global _USE_CACHE, _REFRESH
    if args.no_cache:
        _USE_CACHE = False
    if args.refresh:
        _REFRESH = True

    here = pathlib.Path(__file__).resolve().parent
    if args.backtest:
        bt = backtest(args.sector, args.bt_sample, fwd=args.hold_days)
        (here / "weights.json").write_text(
            json.dumps(bt, ensure_ascii=False, indent=2), encoding="utf-8")
        print_backtest(bt)
        log(f"已写出 weights.json: {here / 'weights.json'}")
        return

    # 权重解析：显式 --weights 优先；否则自动检测同目录 weights.json
    # （需 持有天数匹配 且 不超过14天），匹配不上就回落到持有天数自适应默认权重。
    weights = None
    if args.weights and args.weights != "none":
        wp = (here / "weights.json") if args.weights == "auto" else pathlib.Path(args.weights)
        if wp.exists():
            obj = json.loads(wp.read_text(encoding="utf-8"))
            weights = obj.get("weights", obj)
            log(f"使用权重(显式): {weights}")
        else:
            log(f"⚠ 权重文件不存在: {wp}，用默认权重")
    elif args.weights != "none":
        wp = here / "weights.json"
        if wp.exists():
            try:
                obj = json.loads(wp.read_text(encoding="utf-8"))
                ts = dt.datetime.fromisoformat(obj.get("generated_at", "2000-01-01"))
                age_d = (dt.datetime.now() - ts).days
                if obj.get("fwd_days") == args.hold_days and age_d <= 14:
                    weights = obj.get("weights")
                    log(f"自动使用回测权重(fwd={args.hold_days}, {age_d}天前): {weights}")
                else:
                    log(f"weights.json 不匹配(fwd={obj.get('fwd_days')} vs {args.hold_days}, {age_d}天前)，"
                        f"用持有天数自适应默认权重")
            except Exception:  # noqa: BLE001
                pass

    result = run(args.sector, args.pool, args.top, args.out,
                 weights=weights, hold_days=args.hold_days)
    print_table(result)


if __name__ == "__main__":
    main()
