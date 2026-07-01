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
# 本次实际命中的快照数据源（透明度：写进结果，让用户知道 universe 从哪来、是否兜底）
_LAST_SPOT_SRC = "?"

# ---------------------------------------------------------------- 本地缓存
# K线/快照当天缓存，避免周五反复跑时重复拉数、触发限流。K线收盘后当天不变，
# 给较长 TTL（覆盖周末）；快照盘中会变，给短 TTL。
_USE_CACHE = True
_REFRESH = False  # 强制重拉（绕过读缓存，但仍写回）
_FETCH_NOTICES = True  # 给最终候选附近期公告/业绩预告(Agent②客观种子)；--no-notices 关闭
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
_SPOT_FIELDS = "f12,f14,f2,f3,f5,f6,f8,f9,f10,f20,f21,f100"
_COL_MAP = {
    "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅",
    "f5": "成交量", "f6": "成交额", "f8": "换手率", "f9": "市盈率", "f10": "量比",
    "f20": "总市值", "f21": "流通市值", "f100": "行业",
}

import os as _os
_M2_CHG1_PEN = _os.environ.get("M2_CHG1_PEN", "1") != "0"   # M2 当天暴涨惩罚开关(对照实验/回滚用；默认启用)


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


_CLIST_PER_PAGE = 100  # 东财 clist 单页硬上限 100 行（pz>100 会被截断），必须翻页累计


def _clist_top(fs: str, fields: str, fid: str = "f6", pz: int = 600,
               retries: int = 8) -> list[dict]:
    """分页拉取 clist：服务端按 fid 降序。

    东财 clist 单页最多返回 100 行（即便传 pz=600 也只给 100），
    因此必须用 pn 翻页累计到 pz 行——否则"全市场前600"实际只有前100，
    universe 缩水 6 倍、漏掉大量中盘短线机会。中途某页限流则用已得行(部分universe)。
    """
    rows: list[dict] = []
    pages = max(1, (pz + _CLIST_PER_PAGE - 1) // _CLIST_PER_PAGE)
    for pn in range(1, pages + 1):
        params = {
            "pn": pn, "pz": _CLIST_PER_PAGE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": fid, "fs": fs, "fields": fields,
        }
        try:
            data = _get(_PUSH_HOSTS, _CLIST_PATH, params, retries=retries).get("data")
        except Exception:  # noqa: BLE001
            break  # 翻页中途被限流：保留已拿到的行，部分 universe 仍可用
        if not data or not data.get("diff"):
            break
        diff = data["diff"]
        page = list(diff.values()) if isinstance(diff, dict) else list(diff)
        if not page:
            break
        rows.extend(page)
        if len(page) < _CLIST_PER_PAGE:
            break  # 末页（不足整页），后面没有了
        if pn < pages:
            time.sleep(0.15)  # 翻页间轻微节流
    return rows[:pz]


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
    global _LAST_SPOT_SRC
    import zlib
    log(f"拉取全市场成交额前 {top_by_amount} 只 ...")
    # 缓存 key 含字段签名(crc32)：_SPOT_FIELDS 变更(加 PE/行业等)后旧缓存自动失效，避免命中缺列旧数据
    _spot_key = f"all_{top_by_amount}_v{zlib.crc32(_SPOT_FIELDS.encode()) & 0xffff}"
    cached = _cache_get("spot", _spot_key, max_age_h=6)
    if cached is not None:
        df = pd.DataFrame(cached)  # cached 是 列→list 的 dict，行数取重建后的 DataFrame
        log(f"  命中缓存，{len(df)} 行")
        _LAST_SPOT_SRC = "缓存"
        return df
    for name, fn in (("eastmoney", _em_spot), ("sina", _sina_spot), ("seed", _seed_spot)):
        df = fn(top_by_amount)
        if df is not None and len(df) > 0:
            log(f"  数据源={name}，收到 {len(df)} 行")
            _LAST_SPOT_SRC = name
            _cache_put("spot", _spot_key, df.to_dict("list"))
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


def _sina_kline_close(code: str) -> float | None:
    """新浪K线最新收盘（第三方交叉验证用，独立于东财/腾讯）。
    新浪返回不复权价，但『最新交易日收盘』与前复权最新值一致，校验当日收盘足够。"""
    sec = _tx_secid(code)  # 同 sh/sz 前缀规则
    try:
        r = _SESSION.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData",
            params={"symbol": sec, "scale": 240, "datalen": 3},
            headers={"Referer": "https://finance.sina.com.cn/"}, timeout=15,
        )
        data = r.json()
        if data:
            return round(float(data[-1]["close"]), 2)
    except Exception:  # noqa: BLE001
        return None
    return None


def _yahoo_close(code: str) -> float | None:
    """雅虎财经最新收盘（外网独立源，沪市覆盖好/深市偶缺，作第4兜底）。"""
    suf = ".SS" if code.startswith(("60", "68", "9", "5")) else ".SZ"
    try:
        r = _SESSION.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suf}",
            params={"range": "5d", "interval": "1d"}, timeout=12,
        )
        res = r.json()["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        if closes:
            return round(float(closes[-1]), 2)
    except Exception:  # noqa: BLE001
        return None
    return None


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
    """剔除 ST/退市/北交所/科创板(688/689,多数账户无权限)/低流动性/大跌票。
    涨停票保留（后续按连板/一字板打标处理）。"""
    out = df.copy()
    n0 = len(out)

    out = out[~out["名称"].astype(str).str.contains("ST|退", case=False, na=False)]
    n1 = len(out)

    code = out["代码"].astype(str)
    # 仅留 沪市主板(60)/深市主板(00)/创业板(30)；剔除科创板 68(688/689,需单独开通权限,多数账户买不了)
    out = out[code.str.match(r"^(60|00|30)")]
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
        f"{n0} →非ST {n1} →主板/创业板(剔科创688) {n2} →流通≥{min_float_cap/1e8:.0f}亿 {n3} "
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
        chg1=round(chg_last, 2) if chg_last == chg_last else None,  # M2 当天涨幅(暴涨惩罚/剔除用)
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

    # 惩罚：过度乖离 / 极端高位 / 高位连板（追高风险）+ 当天暴涨(M2：消息/板块高潮拉起，次日易回吐)
    chg1 = f.get("chg1")
    chg1 = chg1 if isinstance(chg1, (int, float)) and chg1 == chg1 else 0.0
    pen = ((10 if dist > 15 else (5 if dist > 10 else 0))
           + (8 if f["rng_pos"] > 95 else 0)
           + (10 if f.get("limit_streak", 0) >= 3 else 0)
           + ((20 if chg1 >= 9.5 else (12 if chg1 >= 7 else 0)) if _M2_CHG1_PEN else 0))

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

    # ---- 止损：以买区下沿(最差成交价)为基准，留足噪音缓冲 ----
    # 旧版止损贴近买区下沿(走样回测中 兴业/新宙邦/三孚 都被 ~2% 噪音扫损)。
    # 新版要求止损至少在买区下沿之下 max(1×ATR, 4%)，并把单笔风险封顶在 8%。
    if pos > 0 and not f.get("oneword"):
        risk_buf = max(atr, lo * 0.04)
        stop = round(lo - risk_buf, 2)
        stop = round(max(stop, lo * 0.92), 2)          # 单笔风险≤8%(高波动封顶)
    else:
        stop = round(max(close - 1.3 * atr, close * 0.94), 2)

    # ---- 过热硬剔除：极端高位 + 乖离/急涨 → 默认踢出买入池(0 仓观察)，不再"半仓" ----
    # 走样回测中 新宙邦(60位98)/三孚(乖离+21) 标了"过热半仓"照买照亏；改为默认剔除，
    # 仅当 Agent② 查到≤7天新鲜独立催化才可由裁决官改回轻仓。
    ext = ((2 if f.get("rng_pos", 50) > 95 else (1 if f.get("rng_pos", 50) > 88 else 0))
           + (2 if f.get("dist_ma10", 0) > 20 else (1 if f.get("dist_ma10", 0) > 12 else 0))
           + (1 if (f.get("ret5") or 0) > 25 else 0))
    overheat = ext >= 3
    if overheat and pos > 0:
        pos = 0
        mode = "过热·默认剔除(观察)"
        tactic = ("60位/乖离/急涨过热触发硬剔除：默认不买；仅当 Agent② 查到≤7天新鲜独立催化，"
                  "裁决官方可改回轻仓(≤原仓位一半)只接深回踩，否则只观察")
    f["overheat"] = overheat

    # ---- M2 当天暴涨兑现硬剔除：T日涨幅≥7%(多为消息/板块高潮拉起) → 踢出买入池，宁错过 ----
    # 次日利好兑现/见光死回吐风险大(2026-06-29 医药政策涨停、次日 CRO 集体回吐、泰格T+1亏最多的教训)。
    chg1_val = f.get("chg1") or 0
    cashout = chg1_val >= 7 and not f.get("oneword")
    if cashout and pos > 0:
        pos = 0
        mode = "暴涨兑现·默认剔除(观察)"
        tactic = (f"T日大涨{chg1_val:.0f}%≥7%(多为消息/板块高潮拉起)，次日易利好兑现回吐/见光死；"
                  "默认不进买入池，仅观察是否有独立于板块的新鲜催化再议")
    f["cashout"] = cashout

    # 放弃条件：高开追高 + 向下跳空见光死(P9 2026-06-29 恒逸教训) + 破止损。
    # 低开击穿买区下沿/直奔止损 = 催化失败见光死，放弃不低吸，绝不把跳空跳水当"打折抄底"。
    gap_lim = 2 if rl in ("中高", "高") else 3
    abort = (f"竞价高开>{gap_lim}% 或 低开击穿买区下沿{lo}(向下跳空/见光死,不低吸) "
             f"或 跌破{stop} → 放弃")

    # ---- 补仓区间(分批建仓第二档)：把已隐含的"首笔60%+回踩补40%"显式成价格带 ----
    # 仅对"可买且不过热"的票给出；位于 买区下沿 与 止损 之间且高于止损。
    # 总仓不变(=持仓上限)，只是把一次建仓拆两档摊低回踩成本；破止损全部止损、不再补。
    add_zone, add_note = "", ""
    if pos > 0 and not overheat and not f.get("oneword"):
        add_hi = round(lo * 0.995, 2)
        add_lo = round(stop * 1.015, 2)
        if add_hi - add_lo >= max(0.01, lo * 0.008):    # 需有有效空间(否则止损太近不值得分档)
            add_zone = f"{add_lo}–{add_hi}"
            add_note = (f"首笔约60%在买区{lo}–{hi}；回踩到{add_lo}–{add_hi} 且企稳(不破{stop})再补40%；"
                        f"合计≤持仓上限{pos}%，破{stop} 全部止损、不再补")

    f.update(buy_low=lo, buy_high=hi, buy_zone=(f"{lo}" if lo == hi else f"{lo}–{hi}"),
             position_pct=pos, entry_mode=mode, entry_tactic=tactic,
             stop=stop, abort=abort, add_zone=add_zone, add_note=add_note)
    return f


def derive_targets(f: dict, hold_days: int) -> dict:
    """基线目标价/预期收益/盈亏比(ATR 法，作为 Agent③ 的量化基线，可被催化剂目标改写)。"""
    close = f["close"]
    atr = f.get("atr14") or close * 0.05
    buy_mid = (f.get("buy_low", close) + f.get("buy_high", close)) / 2
    stop = f.get("stop", close * 0.94)
    k = max(2.0, hold_days * 0.5)          # 持有越久目标越远
    target = round(close + k * atr, 2)
    risk_amt = max(0.01, buy_mid - stop)
    rr = (target - buy_mid) / risk_amt
    lo = (target / f.get("buy_high", close) - 1) * 100
    hi = (target / max(f.get("buy_low", close), 0.01) - 1) * 100
    f["target"] = target
    f["rr"] = f"{rr:.1f}:1"
    f["exp_return"] = f"+{lo:.0f}~{hi:.0f}%" if hi - lo >= 1 else f"+{max(lo,0):.0f}%"
    return f


# ---------------------------------------------------------------- 环境闸门 → 仓位/状态

def _load_market_env(out_path: str | None, as_of: str) -> dict | None:
    """读 market_gate_latest.json（同 out_path 目录优先），只在与 T(as_of) 同日时采用。
    返回 {regime, score, max_total_position_pct, plan, reasons}；缺失/过期则 None。"""
    import os
    cands = []
    if out_path:
        cands.append(os.path.join(os.path.dirname(out_path), "market_gate_latest.json"))
    cands.append(r"C:\Trading_analysis\data\market_gate_latest.json")
    for p in cands:
        try:
            with open(p, encoding="utf-8") as fh:
                g = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        gd = (g.get("sentiment") or {}).get("date") or ""
        if gd and gd != as_of:
            continue  # 环境数据非当日 → 不用，避免拿过期 regime 误压仓位
        return {
            "regime": g.get("regime", ""),
            "score": g.get("score"),
            "max_total_position_pct": g.get("max_total_position_pct"),
            "plan": g.get("plan", ""),
            "reasons": g.get("reasons", [])[:4],
            "t1_forecast": g.get("t1_forecast"),  # T+1 前瞻(展示+背离告警用,不改仓位/打分)
            "as_of": gd,
        }
    return None


def _regime_single_cap(regime: str) -> int | None:
    """按 regime 给单票仓位上限：观望→0(全员观察)、防守→6、中性→8、进攻→不压(None)。"""
    if not regime:
        return None
    if "观望" in regime:
        return 0
    if "防守" in regime:
        return 6
    if "中性" in regime:
        return 8
    return None  # 进攻/未知：沿用 buy_plan 原仓位


def _apply_regime_policy(picks: list[dict], market_env: dict | None) -> None:
    """① 按 regime 压单票仓位上限；② 给每只打 entry_status（可买/观察·不入场/过热剔除/一字板/价格存疑），
    让 HTML 卡片状态与文字结论天然一致。position 被压到 0 的票清掉补仓与目标(避免显示成可买)。"""
    cap = _regime_single_cap(market_env.get("regime", "") if market_env else "")
    for c in picks:
        if cap is not None:
            c["position_pct"] = min(c.get("position_pct", 0), cap)
        # 状态判定（优先级：一字板 > 过热 > 价格存疑 > 仓位0观察 > 可买）
        if c.get("oneword"):
            c["entry_status"] = "一字板·难买入"
        elif c.get("overheat"):
            c["entry_status"] = "过热·默认剔除(观察)"
        elif str(c.get("verify", {}).get("status", "")).startswith("存疑"):
            c["entry_status"] = "价格存疑·待核"
        elif c.get("position_pct", 0) <= 0:
            c["entry_status"] = "观察·不入场"
        else:
            c["entry_status"] = "可买"
        # 不可入场的票：清掉补仓/目标，避免卡片显示成可买方案
        if c["entry_status"] != "可买":
            c["add_zone"] = ""
            c["add_note"] = ""
            if c["entry_status"] in ("观察·不入场", "过热·默认剔除(观察)", "一字板·难买入"):
                c["target"] = "—"
                c["rr"] = "—"
                c["exp_return"] = "—"


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


def verify_picks(cands: list[dict]) -> list[dict]:
    """对最终候选做跨源收盘价校验：东财 / 腾讯 / 新浪 / 雅虎 四源最新收盘取共识。
    ≥2 源齐备且最大偏差≤1% → 一致(标注参与源)；≥2源但偏差大 → 存疑(跨源偏差大)；
    仅 1 源可用 → 单源未校验。多源兜底=东财被限流时仍能用 腾讯×新浪 完成交叉验证，
    避免单源错价误导买入/止损。雅虎(外网)仅在国内源不足时才补，省网络开销。"""
    out = []
    for c in cands:
        code = c["code"]
        em = _em_kline(code, bars=5) if not _EM_DEAD else None
        tx = _tx_kline(code, bars=5)
        em_c = round(float(em["收盘"].iloc[-1]), 2) if em is not None and len(em) else None
        tx_c = round(float(tx["收盘"].iloc[-1]), 2) if tx is not None and len(tx) else None
        sina_c = _sina_kline_close(code)
        srcs = {"东财": em_c, "腾讯": tx_c, "新浪": sina_c}
        # 国内源不足2个时，外网雅虎补位
        yh_c = None
        if sum(v is not None for v in srcs.values()) < 2:
            yh_c = _yahoo_close(code)
            srcs["雅虎"] = yh_c
        avail = {k: v for k, v in srcs.items() if v is not None}
        dev = None
        if len(avail) >= 2:
            vals = list(avail.values())
            dev = round((max(vals) - min(vals)) / max(vals) * 100, 2)
            tag = "×".join(avail.keys())
            status = f"一致({tag})" if dev <= 1.0 else f"存疑(跨源偏差大:{tag})"
        elif len(avail) == 1:
            status = f"单源未校验({next(iter(avail))})"
        else:
            status = "无源"
        c["verify"] = {"em_close": em_c, "tx_close": tx_c, "sina_close": sina_c,
                       "yahoo_close": yh_c, "sources": avail, "dev_pct": dev, "status": status}
        out.append({"code": code, "name": c.get("name", ""),
                    "em": em_c, "tx": tx_c, "sina": sina_c, "yahoo": yh_c,
                    "dev": dev, "status": status})
        time.sleep(0.1)
    return out


# ---------------------------------------------------------------- Agent② 客观催化剂种子
# 之前 Agent② 全靠盲搜 WebSearch，会漏掉 T 日盘后公告（如业绩预告），见 SKILL P8。
# 这里给最终候选附"近期公告标题+日期 + 最新业绩预告"客观清单，让 Agent② 有据可查、
# Agent④ 可比对漏查。免费、无 key、失败不抛（纯增量，不影响原有打分/回测）。

def _dc_get(report: str, filt: str, sort: str = "", ps: int = 20) -> list[dict]:
    """东财数据中心 datacenter-web 通用查询（免费、无 key）。失败返回 []。"""
    p = {"reportName": report, "columns": "ALL", "filter": filt,
         "pageSize": str(ps), "pageNumber": "1", "source": "WEB", "client": "WEB"}
    if sort:
        p["sortColumns"], p["sortTypes"] = sort.split(":")
    try:
        r = _SESSION.get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                         params=p, timeout=12,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://data.eastmoney.com/"})
        obj = r.json()
        return ((obj.get("result") or {}).get("data")) or []
    except Exception as e:  # noqa: BLE001
        log(f"  datacenter {report} 失败: {str(e)[:50]}")
        return []


def _em_announcements(code: str, as_of_d: dt.date, days: int = 14,
                      limit: int = 8) -> list[dict]:
    """东财个股公告列表（近 days 天，标题+日期+URL）。失败返回 []。"""
    p = {"sr": "-1", "page_size": "20", "page_index": "1", "ann_type": "A",
         "client_source": "web", "stock_list": code, "f_node": "0", "s_node": "0"}
    out: list[dict] = []
    try:
        r = _SESSION.get("https://np-anotice-stock.eastmoney.com/api/security/ann",
                         params=p, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        rows = ((r.json().get("data") or {}).get("list")) or []
    except Exception as e:  # noqa: BLE001
        log(f"  公告抓取 {code} 失败: {str(e)[:50]}")
        return []
    cutoff = as_of_d - dt.timedelta(days=days)
    for it in rows:
        nd = str(it.get("notice_date", ""))[:10]
        try:
            ddate = dt.date.fromisoformat(nd)
        except ValueError:
            continue
        if ddate < cutoff:
            continue
        art = str(it.get("art_code") or "")
        out.append({"date": nd, "title": (it.get("title") or "").strip(),
                    "fresh": 0 <= (as_of_d - ddate).days <= 10,
                    "url": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html" if art else ""})
        if len(out) >= limit:
            break
    return out


def fetch_recent_notices(code: str, as_of: str, fresh_days: int = 10) -> dict:
    """Agent② 客观催化剂种子：最新业绩预告 + 近14天公告清单。失败不抛。
    has_fresh_event=有≤fresh_days天的业绩预告或公告（供 Agent④ 比对漏查）。"""
    res: dict = {"forecast": None, "notices": [], "has_fresh_event": False}
    try:
        as_of_d = dt.date.fromisoformat(as_of)
    except (ValueError, TypeError):
        as_of_d = dt.date.today()
    # 1) 最新业绩预告（东财 datacenter，与 stock-diagnostic 同一已验证接口）
    pf = _dc_get("RPT_PUBLIC_OP_NEWPREDICT", f'(SECURITY_CODE="{code}")',
                 sort="NOTICE_DATE:-1", ps=1)
    if pf:
        r0 = pf[0]
        nd = str(r0.get("NOTICE_DATE", ""))[:10]
        fresh = False
        try:
            fresh = 0 <= (as_of_d - dt.date.fromisoformat(nd)).days <= fresh_days
        except ValueError:
            pass
        res["forecast"] = {"notice_date": nd, "type": r0.get("PREDICT_TYPE"),
                           "content": (r0.get("PREDICT_CONTENT") or "")[:80],
                           "chg_lo": r0.get("ADD_AMP_LOWER"),
                           "chg_hi": r0.get("ADD_AMP_UPPER"), "fresh": fresh}
        if fresh:
            res["has_fresh_event"] = True
    # 2) 近期公告清单
    res["notices"] = _em_announcements(code, as_of_d)
    if any(n.get("fresh") for n in res["notices"]):
        res["has_fresh_event"] = True
    return res


def _secucode(code: str) -> str:
    """A股代码 → 东财 SECUCODE(带交易所后缀)。6/9=沪, 4/8=北, 其余=深。"""
    c = str(code)
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def fetch_fundamentals(codes: list[str]) -> dict[str, dict]:
    """M1 基本面地基：批量取每只『最新报告期扣非归母净利同比』+ 报告期名。
    免费东财 datacenter(RPT_F10_FINANCE_MAINFINADATA)，与 forecast 同源、无 key、失败不抛。
    注：这是"当前时点最新财报"、无历史 PIT，故只用于 M4 实盘头部排序 gate，不进回测。"""
    out: dict[str, dict] = {}
    for code in codes:
        rows = _dc_get("RPT_F10_FINANCE_MAINFINADATA",
                       f'(SECUCODE="{_secucode(code)}")',
                       sort="REPORT_DATE:-1", ps=1)
        if not rows:
            continue
        r0 = rows[0]
        kcfj = r0.get("KCFJCXSYJLRTZ")   # 扣非归母净利润同比增长率(%)
        try:
            kcfj = round(float(kcfj), 2) if kcfj is not None else None
        except (TypeError, ValueError):
            kcfj = None
        out[str(code)] = {"kcfj_yoy": kcfj,
                          "report_name": str(r0.get("REPORT_DATE_NAME") or "")[:12]}
        time.sleep(0.15)
    return out


def run(sector: str | None, pool: int, top: int, out_path: str | None,
        weights: dict | None = None, hold_days: int = 5,
        verify: bool = True) -> dict:
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
        pe_v = pd.to_numeric(r.get("市盈率"), errors="coerce")
        f["pe"] = round(float(pe_v), 1) if pd.notna(pe_v) else None          # M1 市盈率(f9)
        ind = r.get("行业")
        f["industry"] = str(ind) if ind is not None and str(ind) not in ("nan", "None", "-") else None  # M1 所属行业(f100，为M3预留)
        f = risk_assess(f)  # 客观技术风险分(spot字段就绪后算)
        f = buy_plan(f)     # 买入方案
        f = derive_targets(f, hold_days)  # 基线目标价/预期收益/盈亏比
        rows.append(f)
        if i % 10 == 0:
            log(f"  已处理 {i}/{len(ranked_pre)}")

    rows.sort(key=lambda x: x["score"], reverse=True)
    # M2: 不可买票(pos=0：过热/暴涨兑现/一字板)沉出头部，可买票优先进 top(宁错过)
    rows.sort(key=lambda x: 0 if x.get("position_pct", 0) > 0 else 1)
    picks = rows[:top]
    # ---- M4 基本面 gate：给头部附最新扣非同比，扣非负增长/PE过高/亏损 → 沉出头部(仅实盘,不进回测) ----
    if picks:
        fund = fetch_fundamentals([c["code"] for c in picks])
        for c in picks:
            c.update(fund.get(c["code"], {}))

        def _bad_fund(c):
            k, pe = c.get("kcfj_yoy"), c.get("pe")
            neg = (k is not None and k < 0)                     # 扣非负增长=主营下滑(泰格2025全年式)
            pe_bad = (pe is not None and (pe < 0 or pe > 120))  # 亏损 / 估值过高
            return 1 if (neg or pe_bad) else 0

        # 三档稳定排序：①可买+基本面好 ②可买+基本面差(扣非负/PE高) ③不可买(pos=0) → 头部第1必为可买优质票
        picks.sort(key=lambda c: (0 if (c.get("position_pct", 0) > 0 and not _bad_fund(c))
                                  else (1 if c.get("position_pct", 0) > 0 else 2)))
    # 跨源价格校验：只对最终候选做（防脏数据误导买入/止损价）
    if verify and picks:
        log(f"跨源价格校验 Top{len(picks)} ...")
        verify_picks(picks)
    # T = 最新交易日；用权威交易日历算 T+1(买入)、T+N(最晚卖出)
    dates = [r.get("last_date") for r in rows if r.get("last_date")]
    as_of = max(dates) if dates else dt.date.today().isoformat()
    win = _trade_window(as_of, hold_days)
    # Agent② 客观催化剂种子：给最终候选附近期公告+业绩预告(免费，失败不抛)
    if _FETCH_NOTICES and picks:
        log(f"抓取近期公告/业绩预告 Top{len(picks)} (Agent②客观种子) ...")
        for c in picks:
            nt = fetch_recent_notices(c["code"], as_of)
            c["recent_notices"] = nt["notices"]
            c["forecast"] = nt["forecast"]
            c["has_fresh_event"] = nt["has_fresh_event"]
            time.sleep(0.2)
    # 数据自检：universe 太小/兜底源/价格存疑 → 显式警示，避免"看着精准其实样本不足"
    sanity = []
    if _LAST_SPOT_SRC == "seed":
        sanity.append("⚠universe走种子兜底(非全市场)，覆盖面有限")
    elif _LAST_SPOT_SRC == "sina":
        sanity.append("行情源回退到新浪(无量比，盘口因子略弱)")
    if len(filt) < 60:
        sanity.append(f"⚠初筛后universe仅{len(filt)}只，样本偏小、排名区分度下降")
    suspect = [c["code"] for c in picks if c.get("verify", {}).get("status", "").startswith("存疑")]
    if suspect:
        sanity.append(f"⚠跨源价格存疑(请人工复核): {', '.join(suspect)}")
    # 市场环境闸门(Agent⓪)：读同目录 market_gate_latest.json，按 regime 给总仓闸门并标每只可买/观察状态
    market_env = _load_market_env(out_path, as_of)
    _apply_regime_policy(picks, market_env)
    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "hold_days": hold_days,         # 最多持有交易日数
        "sector": sector or "全市场",
        "weights": weights or "默认均衡",
        "calendar_authoritative": win["authoritative"],
        "spot_source": _LAST_SPOT_SRC,
        "universe_after_filter": int(len(filt)),
        "scored": len(rows),
        "sanity_flags": sanity,
        "market_env": market_env,        # Agent⓪ 环境闸门(供 HTML 顶部横幅 + 总仓上限)；无则 None
        "candidates": picks,
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
    print(f"行情源={result.get('spot_source','?')}  初筛后universe={result['universe_after_filter']}  "
          f"打分={result['scored']}  输出Top {len(result['candidates'])}  (生成于 {result['generated_at']})")
    for s in result.get("sanity_flags", []):
        print(f"  {s}")
    print()
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
    # 买入方案表（可执行：状态/区间/仓位/方式/止损/放弃）
    env = result.get("market_env")
    if isinstance(env, dict) and env.get("regime"):
        print(f"\n🌡 市场环境：{env['regime']}（环境分 {env.get('score','?')}）· 总仓上限 "
              f"{env.get('max_total_position_pct','?')}% · {env.get('plan','')}")
        fc = env.get("t1_forecast")
        if isinstance(fc, dict) and fc.get("direction"):
            print(f"🔮 T+1 前瞻(展示用,不改仓位/打分)：{fc['direction']} · "
                  f"P涨{fc.get('prob_up')}/P跌{fc.get('prob_down')} · 预计跳空{fc.get('exp_gap_range','')} · "
                  f"情绪{fc.get('sentiment_continuation','')} · 置信度{fc.get('confidence','')} "
                  f"(回测命中率{fc.get('hit_rate')}/MAE{fc.get('gap_mae')}%/样本{fc.get('n_eval')})")
            if fc.get("divergence"):
                print(f"   {fc['divergence']}")
    print("\n### 买入方案（T+1 执行）")
    bh = ["排名", "代码", "名称", "状态", "买入/参考区间", "补仓区间", "建议仓位", "入场方式", "止损", "放弃条件"]
    print("| " + " | ".join(bh) + " |")
    print("|" + "|".join(["---"] * len(bh)) + "|")
    for i, c in enumerate(result["candidates"], 1):
        st = c.get("entry_status", "可买" if c.get("position_pct", 0) > 0 else "观察·不入场")
        print(f"| {i} | {c['code']} | {c['name']} | {st} | {c.get('buy_zone','')} | "
              f"{c.get('add_zone','') or '—'} | "
              f"{c.get('position_pct','')}% | {c.get('entry_mode','')}：{c.get('entry_tactic','')} | "
              f"{c.get('stop','')} | {c.get('abort','')} |")

    # 跨源价格校验结果（若已做）
    vrows = [c for c in result["candidates"] if c.get("verify")]
    if vrows:
        print("\n### 跨源价格校验（东财 / 腾讯 / 新浪 / 雅虎 收盘取共识）")
        print("| 代码 | 名称 | 东财 | 腾讯 | 新浪 | 雅虎 | 最大偏差% | 状态 |")
        print("|---|---|---|---|---|---|---|---|")
        for c in vrows:
            v = c["verify"]
            print(f"| {c['code']} | {c['name']} | {v.get('em_close') or '-'} | "
                  f"{v.get('tx_close') or '-'} | {v.get('sina_close') or '-'} | "
                  f"{v.get('yahoo_close') or '-'} | {v.get('dev_pct') if v.get('dev_pct') is not None else '-'} | "
                  f"{v.get('status','')} |")

    # Agent③ 重点关注：买不进 / 追高风险
    oneword = [c["code"] for c in result["candidates"] if c.get("oneword")]
    hot = [f"{c['code']}({c['limit_streak']}板)" for c in result["candidates"] if c.get("limit_streak", 0) >= 3]
    if oneword:
        print(f"\n⚠ 一字板(次日难买入): {', '.join(oneword)}")
    if hot:
        print(f"⚠ 高位连板(追高风险): {', '.join(hot)}")
    # Agent② 客观催化剂种子：近期公告/业绩预告清单（⚡=≤10天新鲜，重点核验）
    has_seed = any(c.get("recent_notices") or c.get("forecast") for c in result["candidates"])
    if has_seed:
        print("\n### 📋 近期公告/业绩预告（Agent② 客观种子 · ⚡=≤10天新鲜事件，必须专搜核验）")
        for c in result["candidates"]:
            fc = c.get("forecast"); ns = c.get("recent_notices") or []
            if not fc and not ns:
                continue
            flag = "⚡" if c.get("has_fresh_event") else "  "
            print(f"{flag}{c['code']} {c['name']}:")
            if fc:
                lo, hi = fc.get("chg_lo"), fc.get("chg_hi")
                amp = (f" 净利变动{lo}~{hi}%" if (lo is not None or hi is not None) else "")
                print(f"     {'⚡' if fc.get('fresh') else '·'} 业绩预告 {fc.get('notice_date')} "
                      f"{fc.get('type') or ''}{amp}")
            for n in ns[:5]:
                print(f"     {'⚡' if n.get('fresh') else '·'} {n['date']} {n['title']}")
        print("> Agent②须逐只比对此清单：有 ⚡ 新鲜事件的票，必须专搜该事件确认强度/price-in，"
              "不得只凭泛搜下结论；引擎列了⚡却未被Agent②提及=漏查，Agent④打回。")

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


def _shrink_weights(raw: dict, shrink: float) -> dict:
    """把回测原始桶权重向中性 1.0 收缩，降低对噪声IC的过拟合。

    w_final = shrink×1.0 + (1−shrink)×w_raw。shrink=0 纯回测、=1 全中性。
    理由：桶权重由 ~25 截面的 |IC| 估出，IC 标准误(≈0.03)与均值(≈0.05)同量级，
    直接重注极端配比(如 tape0.58/pull0.52 vs mom1.3)样本外不稳；收缩后更稳健，
    也让"多久 re-backtest 一次"变得不敏感。
    """
    shrink = min(1.0, max(0.0, shrink))
    return {b: round(shrink * 1.0 + (1 - shrink) * v, 2) for b, v in raw.items()}


def backtest(sector: str | None, sample: int, fwd: int = 5, step: int = 5,
             hist_bars: int = 200, shrink: float = 0.5) -> dict:
    """对样本股做横截面因子 IC 回测：因子(T) vs 未来 fwd 日收益。输出每因子 IC/ICIR + 建议权重。
    weights 已对原始 |IC| 配比做收缩(shrink, 默认0.5)，weights_raw 保留收缩前以便透明对照。"""
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
    weights_raw = {b: round(v / base, 2) if v > 0 else 0.6 for b, v in bw.items()}
    weights = _shrink_weights(weights_raw, shrink)  # 收缩后才是引擎实际使用的权重

    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "fwd_days": fwd, "n_records": len(df), "n_dates": int(df["_date"].nunique()),
            "n_stocks": len(codes), "ic": ic_rows,
            "shrink": shrink, "weights_raw": weights_raw, "weights": weights}


def print_backtest(bt: dict) -> None:
    print(f"\n## 因子 IC 回测  ({bt['generated_at']})")
    print(f"样本股={bt['n_stocks']}  样本点={bt['n_records']}  截面日数={bt['n_dates']}  预测窗口={bt['fwd_days']}日\n")
    print("| 因子 | IC均值 | ICIR | |IC| | 截面日数 |")
    print("|---|---|---|---|---|")
    for r in bt["ic"]:
        print(f"| {r['factor']} | {r['IC']} | {r['ICIR']} | {round(r['absIC'],4)} | {r['n_days']} |")
    print("\n按经济含义归桶后的桶权重（写入 weights.json，可 --weights auto 调用）:")
    if bt.get("weights_raw"):
        print("  收缩前(纯|IC|): " + "  ".join(f"{k}={v}" for k, v in bt["weights_raw"].items()))
    print(f"  收缩后(shrink={bt.get('shrink','?')},实际使用): "
          + "  ".join(f"{k}={v}" for k, v in bt["weights"].items()))
    print("\n解读：IC>0 因子值越大未来越涨；|IC|>0.03 即有效，>0.05 较强；ICIR>0.5 稳定。")
    print("收缩=向中性1.0拉近，降低对噪声IC的过拟合；shrink越大越保守(0=纯回测,1=全中性)。")


# ---------------------------------------------------------------- Phase2b 综合分实战验证
# 因子IC只验证「单因子」是否有预测力；本节验证「最终综合分排名」本身有没有 edge——
# 即用户真正照着买的那张表。做法：在历史每个横截面用 *与实盘完全相同的* composite()
# 打分，取 Top-K，按 T+1 开盘买入、持有 fwd 个交易日(收盘卖)算真实收益，逐截面汇总。

def _score_factors_at(o, c, h, l, v, code: str, t: int) -> dict | None:
    """在第 t 根K线(仅用 ≤t 数据，无未来函数)复刻 hist_factors 的关键因子，
    键名/单位与 composite() 期望完全一致，从而能直接复用实盘打分函数做回测。"""
    if t < 21 or c[t] <= 0:
        return None
    last = float(c[t])

    def ma(n):
        return float(c[t - n + 1:t + 1].mean()) if t - n + 1 >= 0 else np.nan

    ma5_, ma10_, ma20_ = ma(5), ma(10), ma(20)
    bull = bool(ma5_ == ma5_ and ma10_ == ma10_ and ma20_ == ma20_ and ma5_ > ma10_ > ma20_)
    ret5 = last / float(c[t - 5]) - 1 if c[t - 5] > 0 else np.nan
    ret20 = last / float(c[t - 20]) - 1 if c[t - 20] > 0 else np.nan
    v5 = float(v[t - 4:t + 1].mean())
    v20 = float(v[t - 19:t + 1].mean())
    vr = v5 / v20 if v20 > 0 else np.nan
    vol_today = float(v[t]) / v20 if v20 > 0 else np.nan
    dist_ma10 = last / ma10_ - 1 if ma10_ > 0 else 0.0
    w0 = max(0, t - 59)
    lo, hi = float(l[w0:t + 1].min()), float(h[w0:t + 1].max())
    rng_pos = (last - lo) / (hi - lo) if hi > lo else 0.5
    cser = pd.Series(c[:t + 1], dtype=float)
    dif = cser.ewm(span=12).mean() - cser.ewm(span=26).mean()
    dea = dif.ewm(span=9).mean()
    macd_gold = bool(dif.iloc[-1] > dea.iloc[-1])
    drng = float(h[t] - l[t])
    tail = (last - float(l[t])) / drng if drng > 0 else 0.5
    is_yang = bool(last > float(o[t]))
    body_ratio = abs(last - float(o[t])) / drng if drng > 0 else 0.0
    # 连板（用 ≤t 的日涨跌幅）：创业板/科创 20%，其余 10%
    limit_pct = 20.0 if str(code).startswith(("30", "68")) else 10.0
    thr = limit_pct * 0.985
    dchg = (cser.pct_change() * 100).to_numpy()
    streak = 0
    for x in dchg[::-1]:
        if x == x and x >= thr:
            streak += 1
        else:
            break
    pullback = bool(bull and ret20 == ret20 and ret20 > 0.05
                    and -3 <= dist_ma10 * 100 <= 5 and is_yang)
    # vr/vol_today 必须给 None(而非 np.nan)：composite() 用 `x if x else 1.0` 判空，
    # np.nan 是 truthy 会把 nan 漏进打分；与 hist_factors 的处理保持一致。
    vr = None if vr != vr else vr
    vol_today = None if vol_today != vol_today else vol_today
    return dict(
        ret5=ret5 * 100, ret20=ret20 * 100, vr=vr, vol_today=vol_today,
        tail_strength=tail, dist_ma10=dist_ma10 * 100, bull=bull, macd_gold=macd_gold,
        rng_pos=rng_pos * 100, is_yang=is_yang, body_ratio=body_ratio,
        pullback=pullback, limit_streak=streak,
        chg1=(last / float(c[t - 1]) - 1) * 100 if c[t - 1] > 0 else np.nan,  # M2 当天涨幅(回测同实盘)
    )


def validate(sector: str | None, sample: int, fwd: int = 5, top_k: int = 5,
             step: int = 5, hist_bars: int = 220, weights: dict | None = None) -> dict:
    """综合分排名的走样本验证：每个横截面按 composite() 取 Top-K，
    T+1 开盘买入、持有 fwd 日(收盘卖)算真实收益，对比全样本(市场)与 Bottom-K。"""
    if weights is None:
        weights = _auto_weights(fwd)
    log(f"=== 综合分实战验证：样本≤{sample} 只，Top{top_k}，T+1开盘买入持有{fwd}日 ===")
    spot = fetch_sector_spot(sector) if sector and sector not in ("全市场", "all") else get_spot()
    filt = prefilter(spot)
    codes = [str(x) for x in filt["代码"].tolist()][:sample]
    log(f"  样本股 {len(codes)} 只，构建 (日期,股票)→综合分/未来收益 面板 ...")

    panel: list[dict] = []
    for i, code in enumerate(codes, 1):
        h = get_kline(code, bars=hist_bars)
        if h is None or len(h) < 95:
            continue
        o, c, hh, ll, vv = (h[k].to_numpy(dtype=float)
                            for k in ("开盘", "收盘", "最高", "最低", "成交量"))
        dates = h["日期"].astype(str).to_numpy()
        # 需要 t+1 开盘买、t+fwd 收盘卖 → t 最大到 len-1-fwd
        for t in range(70, len(c) - fwd - 1, step):
            f = _score_factors_at(o, c, hh, ll, vv, code, t)
            if f is None:
                continue
            sc = composite(dict(f), weights)["score"]
            buy = float(o[t + 1])
            sell = float(c[t + fwd])
            if buy <= 0:
                continue
            panel.append({"_date": str(dates[t])[:10], "code": code,
                          "score": sc, "fwd_ret": sell / buy - 1})
        time.sleep(0.1)
        if i % 20 == 0:
            log(f"  已处理 {i}/{len(codes)}")

    if not panel:
        raise RuntimeError("验证无数据（行情源不可用）")
    df = pd.DataFrame(panel)

    top_rets, bot_rets, mkt_rets, top_wins, ic_list = [], [], [], [], []
    n_sections = 0
    for _, g in df.groupby("_date"):
        if len(g) < max(8, top_k * 2):      # 截面太小不足以区分 Top/Bottom
            continue
        n_sections += 1
        g = g.sort_values("score", ascending=False)
        k = min(top_k, len(g) // 2)
        top = g.head(k)["fwd_ret"]
        bot = g.tail(k)["fwd_ret"]
        top_rets.append(float(top.mean()))
        bot_rets.append(float(bot.mean()))
        mkt_rets.append(float(g["fwd_ret"].mean()))
        top_wins.extend((top > 0).tolist())
        ic = g["score"].corr(g["fwd_ret"], method="spearman")
        if ic == ic:
            ic_list.append(float(ic))

    def _avg(x):
        return round(float(np.mean(x)) * 100, 2) if x else None

    top_avg = _avg(top_rets)
    mkt_avg = _avg(mkt_rets)
    bot_avg = _avg(bot_rets)
    win_rate = round(float(np.mean(top_wins)) * 100, 1) if top_wins else None
    excess = round(top_avg - mkt_avg, 2) if (top_avg is not None and mkt_avg is not None) else None
    spread = round(top_avg - bot_avg, 2) if (top_avg is not None and bot_avg is not None) else None
    ic_mean = round(float(np.mean(ic_list)), 4) if ic_list else None
    ic_ir = (round(ic_mean / (float(np.std(ic_list)) + 1e-9), 3)
             if ic_list and len(ic_list) > 1 else None)

    # 给 Agent 用的一句话判定
    if top_avg is None or n_sections < 5:
        verdict = "数据不足，验证不充分"
    elif excess and excess > 0 and (spread or 0) > 0 and (win_rate or 0) >= 50:
        verdict = "通过：Top组跑赢市场且胜率达标，综合分有 edge"
    elif excess and excess > 0:
        verdict = "弱通过：Top组小幅跑赢，edge 偏弱，置信度宜保守"
    else:
        verdict = "未通过：Top组未跑赢市场，本期慎用排名/调低仓位"

    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "fwd_days": fwd, "top_k": top_k, "n_stocks": len(codes),
            "n_sections": n_sections, "n_records": len(df),
            "weights": weights or "默认均衡",
            "top_avg_ret": top_avg, "market_avg_ret": mkt_avg, "bottom_avg_ret": bot_avg,
            "excess_vs_market": excess, "top_minus_bottom": spread,
            "top_win_rate": win_rate, "rank_ic": ic_mean, "rank_icir": ic_ir,
            "verdict": verdict}


def print_validation(v: dict) -> None:
    print(f"\n## 综合分实战验证（走样本，无未来函数）  ({v['generated_at']})")
    print(f"样本股={v['n_stocks']}  有效截面={v['n_sections']}  样本点={v['n_records']}  "
          f"Top{v['top_k']}  持有{v['fwd_days']}日(T+1开盘买→收盘卖)\n")
    print("| 指标 | 数值 | 含义 |")
    print("|---|---|---|")
    print(f"| Top{v['top_k']}平均收益 | {v['top_avg_ret']}% | 照排名买前{v['top_k']}只的平均{v['fwd_days']}日收益 |")
    print(f"| 市场平均 | {v['market_avg_ret']}% | 同期样本全体平均(基准) |")
    print(f"| Bottom{v['top_k']}平均 | {v['bottom_avg_ret']}% | 排名垫底组(对照) |")
    print(f"| 超额(Top−市场) | {v['excess_vs_market']}% | >0 才说明排名有正向选择力 |")
    print(f"| 多空价差(Top−Bottom) | {v['top_minus_bottom']}% | 越大越说明分数单调有效 |")
    print(f"| Top组胜率 | {v['top_win_rate']}% | 前{v['top_k']}只里收正的比例 |")
    print(f"| 排名IC(Spearman) | {v['rank_ic']}  ICIR={v['rank_icir']} | 综合分与未来收益的秩相关 |")
    print(f"\n**裁定**：{v['verdict']}")
    print("> 说明：样本取自当前高流动性股票回溯历史，存在幸存者偏差，绝对收益偏乐观；")
    print("> 应重点看『超额/多空价差/胜率』等相对指标是否稳定为正，而非绝对收益数字。")


# ---------------------------------------------------------------- HTML 报告
def _cn_now() -> dt.datetime:
    """中国当地时间(UTC+8，中国无夏令时)。即时取系统UTC再换算，不受机器时区影响。"""
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8)))


_RISK_COLOR = {"低": "#2e7d32", "中": "#1565c0", "中高": "#ef6c00", "高": "#c62828"}


def _esc(x) -> str:
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


PREVIEW_NAME = "ashare_rank_cn_preview.html"  # 引擎自动预览版的固定名(反复覆盖,不堆时间戳中间文件)


def render_html(result: dict, report_dir: str | None = None, preview: bool = False) -> str:
    """把选股结果渲染成自包含(无外部依赖)的易读 HTML。
    preview=True(引擎自动出的量化预览)→ 固定文件名,每次覆盖,引擎跑几次都只占1个;
    preview=False(make_report 出的完整版)→ 时间戳命名,并清掉预览中间文件,只留这份最终版。"""
    cn = _cn_now()
    ts = cn.strftime("%Y-%m-%d_%H-%M-%S")
    rdir = pathlib.Path(report_dir) if report_dir else (pathlib.Path(__file__).resolve().parent / "reports")
    rdir.mkdir(parents=True, exist_ok=True)
    if preview:
        path = rdir / PREVIEW_NAME
    else:
        path = rdir / f"ashare_rank_cn_{ts}.html"
        prev = rdir / PREVIEW_NAME  # 完整版生成 → 删掉预览中间文件
        if prev.exists():
            try:
                prev.unlink()
            except OSError:
                pass

    cands = result.get("candidates", [])
    auth = "权威交易日历" if result.get("calendar_authoritative") else "⚠仅跳周末(日历未取到)"
    note = result.get("note", "")

    # 市场环境横幅（Agent⓪ regime → 顶部醒目条；颜色随档位，并点明本期是否空仓观望）
    env = result.get("market_env")
    env_html = ""
    if isinstance(env, dict) and env.get("regime"):
        reg = env["regime"]
        ec = ("#c62828" if "观望" in reg else "#ef6c00" if "防守" in reg
              else "#1565c0" if "中性" in reg else "#2e7d32")
        cap = env.get("max_total_position_pct")
        buyable_n = sum(1 for c in cands if c.get("entry_status", "") == "可买")
        concl = ("本期结论=空仓观望，下列全部为『观察·不入场』" if "观望" in reg
                 else f"可买 {buyable_n} 只，其余为观察/剔除")
        env_html = (
            f"<div class=envbar style='border-color:{ec}'>"
            f"<b style='color:{ec}'>🌡 市场环境：{_esc(reg)}（环境分 {env.get('score','?')}）</b>"
            f" · 总仓上限 <b>{cap}%</b> · {_esc(concl)}"
            + (f"<br><span class=envsub>" + _esc(' · '.join(env.get('reasons', []))) + "</span>" if env.get("reasons") else "")
            + (f"<br><span class=envsub>预案：{_esc(env.get('plan',''))}</span>" if env.get("plan") else "")
            + "</div>"
        )
        # T+1 前瞻子横幅（展示+背离告警用，绝不改仓位/打分）
        fc = env.get("t1_forecast")
        if isinstance(fc, dict) and fc.get("direction"):
            dc = ("#2e7d32" if "多" in fc["direction"]
                  else "#c62828" if "空" in fc["direction"] else "#6b7280")
            env_html += (
                f"<div class=envbar style='border-color:{dc};margin-top:6px'>"
                f"<b style='color:{dc}'>🔮 T+1 前瞻（展示用·不改仓位/打分）：{_esc(fc['direction'])}</b>"
                f" · P涨 <b>{fc.get('prob_up')}</b>/P跌 <b>{fc.get('prob_down')}</b>"
                f" · 预计跳空 <b>{_esc(fc.get('exp_gap_range',''))}</b>"
                f" · 情绪{_esc(fc.get('sentiment_continuation',''))} · 置信度 <b>{_esc(fc.get('confidence',''))}</b>"
                f"<br><span class=envsub>回测命中率 {fc.get('hit_rate')} · 跳空MAE {fc.get('gap_mae')}% · "
                f"评估样本 {fc.get('n_eval')}（扩张窗口·无前视） · "
                + _esc(' · '.join(fc.get('drivers', []))) + "</span>"
                + (f"<br><span class=envsub style='color:#c62828'><b>{_esc(fc.get('divergence',''))}</b></span>"
                   if fc.get("divergence") else "")
                + f"<br><span class=envsub>{_esc(fc.get('note',''))}</span>"
                + "</div>"
            )

    # 主体：每只股一张卡片（长文本整行铺开，避免横向滚动）
    cards = ""
    for i, c in enumerate(cands, 1):
        rl = c.get("risk_level", "")
        color = _RISK_COLOR.get(rl, "#666")
        chips = "".join(f"<span class=chip>{_esc(s)}</span>"
                        for s in _signal_tag(c).split("/") if s)
        rnote = c.get("risk_note", "")
        rnote_html = (f"<div class=line><span class='k grey'>风险补充</span>{_esc(rnote)}</div>"
                      if rnote else "")
        # 入场状态徽标（数据驱动：让卡片可买/观察与文字结论天然一致）
        status = c.get("entry_status", "可买" if c.get("position_pct", 0) > 0 else "观察·不入场")
        buyable = status == "可买"
        st_color = "#2e7d32" if buyable else "#6b7280"   # 可买=绿，观察/剔除=灰
        status_badge = f"<span class=badge style='background:{st_color}'>{_esc(status)}</span>"
        buy_lab = "买入" if buyable else "参考价"
        card_cls = "card" if buyable else "card obs"      # 观察卡片整体灰化
        cards += (
            f"<div class='{card_cls}'><div class=ctop>"
            f"<div class=rank>{i}</div>"
            f"<div class=tt><span class=title>{_esc(c.get('name',''))}</span>"
            f"<span class=tk>{_esc(c.get('code',''))}</span></div>"
            f"<div class=tags>{status_badge}<span class=qs>量化 {c.get('score','')}</span>"
            f"<span class=badge style='background:{color}'>风险 {rl}({c.get('risk_score','')})</span>"
            f"{chips}</div></div>"
            f"<div class=stats>"
            f"<div class=stat><span class=lab>预期收益</span><b class=ret>{_esc(c.get('exp_return','—'))}</b></div>"
            f"<div class=stat><span class=lab>置信度</span><b>{_esc(c.get('confidence','—'))}</b></div>"
            f"<div class=stat><span class=lab>R:R</span><b>{_esc(c.get('rr','—'))}</b></div>"
            f"<div class=stat><span class=lab>持仓上限</span><b>{c.get('position_pct','')}%</b></div></div>"
            f"<div class=plan><span><i>{buy_lab}</i><b>{_esc(c.get('buy_zone',''))}</b></span>"
            f"<span><i>目标</i><b>{c.get('target','')}</b></span>"
            f"<span><i>止损</i><b>{c.get('stop','')}</b></span></div>"
            + (f"<div class=line><span class='k'>补仓</span>{_esc(c.get('add_note',''))}</div>"
               if c.get('add_zone') else "")
            + f"<div class=line><span class=k>入场</span>{_esc(c.get('entry_mode',''))}：{_esc(c.get('entry_tactic',''))}</div>"
            f"<div class=line><span class='k red'>放弃</span>{_esc(c.get('abort',''))}</div>"
            f"<div class=line><span class='k green'>催化剂</span>{_esc(c.get('catalyst','—'))}</div>"
            + ((f"<div class=line><span class=k>基本面</span>扣非同比 <b>{c.get('kcfj_yoy')}%</b>"
                + (f" · PE {c.get('pe')}" if c.get('pe') is not None else "")
                + (f" · {_esc(c.get('industry'))}" if c.get('industry') else "")
                + (f"（{_esc(c.get('report_name'))}）" if c.get('report_name') else "")
                + "</div>") if c.get('kcfj_yoy') is not None else "")
            + f"{rnote_html}</div>"
        )

    # 量化明细（透明附录，可折叠）
    detail_rows = ""
    for i, c in enumerate(cands, 1):
        detail_rows += (
            f"<tr><td>{i}</td><td class=code>{_esc(c.get('code',''))}</td>"
            f"<td class=nm>{_esc(c.get('name',''))}</td><td>{c.get('close','')}</td>"
            f"<td>{c.get('ret5','')}</td><td>{c.get('ret20','')}</td><td>{c.get('vr','')}</td>"
            f"<td>{c.get('vol_today','')}</td><td>{c.get('tail_strength','')}</td>"
            f"<td>{c.get('dist_ma10','')}</td><td>{c.get('rng_pos','')}</td><td>{c.get('atr_pct','')}</td>"
            f"<td>{c.get('amount_yi','')}</td><td>{c.get('float_cap_yi','')}</td>"
            f"<td>{'多头' if c.get('bull') else '—'}/{'金叉' if c.get('macd_gold') else '—'}</td></tr>"
        )

    oneword = [c["code"] for c in cands if c.get("oneword")]
    hot = [f"{c['code']}({c['limit_streak']}板)" for c in cands if c.get("limit_streak", 0) >= 3]
    warn = env_html  # 市场环境横幅置顶（若有）
    if preview:
        warn += ("<p class=warn>📝 量化预览版（仅引擎打分，未含消息面/裁决）——"
                 "生成完整版后本文件会被自动清除，请以带时间戳的完整版为准。</p>")
    for s in result.get("sanity_flags", []):
        warn += f"<p class=warn>{_esc(s)}</p>"
    if oneword:
        warn += f"<p class=warn>⚠ 一字板(次日难买入)：{_esc(', '.join(oneword))}</p>"
    if hot:
        warn += f"<p class=warn>⚠ 高位连板(追高风险)：{_esc(', '.join(hot))}</p>"

    # 综合分实战验证裁定（若 Agent 把 validate 结果回填到 result["validation"]）
    val = result.get("validation")
    if isinstance(val, dict) and val.get("verdict"):
        warn += (f"<p class=warn>📐 策略验证：{_esc(val['verdict'])}　"
                 f"(Top{val.get('top_k','')}超额 {val.get('excess_vs_market','?')}% · "
                 f"胜率 {val.get('top_win_rate','?')}% · 排名IC {val.get('rank_ic','?')} · "
                 f"有效截面 {val.get('n_sections','?')})</p>")

    extra = ""
    if result.get("catalysts_md"):
        extra += f"<h2>消息面（Agent②）</h2><div class=md><pre>{_esc(result['catalysts_md'])}</pre></div>"
    if result.get("final_md"):
        extra += f"<h2>最终裁决（Agent③）</h2><div class=md><pre>{_esc(result['final_md'])}</pre></div>"

    css = (
        "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;"
        "background:#eef1f5;color:#1a1a1a;line-height:1.5}"
        ".wrap{max-width:1000px;margin:0 auto;padding:20px}"
        "h1{font-size:21px;margin:6px 0}h2{font-size:16px;margin:20px 0 8px;border-left:4px solid #1565c0;padding-left:8px}"
        ".meta{background:#fff;border:1px solid #e0e4e8;border-radius:10px;padding:12px 14px;font-size:13px;color:#333}"
        ".warn{background:#fff3e0;border-left:4px solid #ef6c00;padding:7px 11px;margin:8px 0;font-size:13px;border-radius:6px}"
        ".envbar{background:#fff;border:1px solid #ddd;border-left:5px solid #c62828;border-radius:8px;"
        "padding:10px 13px;margin:10px 0;font-size:14px}"
        ".envsub{color:#666;font-size:12px}"
        # 卡片
        ".card{background:#fff;border:1px solid #e6e9ed;border-radius:12px;padding:14px 16px;margin:12px 0;"
        "box-shadow:0 1px 4px rgba(0,0,0,.06)}"
        ".card.obs{background:#f7f8fa;opacity:.82}.card.obs .plan{background:#eceef1}"
        ".ctop{display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:1px solid #eef1f4;padding-bottom:9px}"
        ".rank{width:26px;height:26px;border-radius:50%;background:#1565c0;color:#fff;display:flex;"
        "align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0}"
        ".tt .title{font-size:16px;font-weight:700}.tt .tk{color:#999;font-family:monospace;font-size:12px;margin-left:6px}"
        ".tags{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}"
        ".qs{background:#eef4ff;color:#1565c0;border-radius:6px;padding:2px 8px;font-weight:700;font-size:12px}"
        ".badge{color:#fff;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600}"
        ".chip{background:#fff3e0;color:#c62828;border:1px solid #ffd8a8;border-radius:10px;padding:1px 8px;font-size:11px}"
        ".stats{display:flex;gap:26px;flex-wrap:wrap;margin:10px 0 4px}"
        ".stat .lab{color:#999;font-size:11px;display:block}.stat b{font-size:15px}.stat .ret{color:#c62828}"
        ".plan{background:#f5f8fc;border-radius:8px;padding:8px 12px;display:flex;gap:24px;flex-wrap:wrap;"
        "font-size:13.5px;margin:8px 0}.plan i{color:#999;font-style:normal;margin-right:5px;font-size:12px}"
        ".plan b{font-weight:700}"
        ".line{font-size:12.5px;line-height:1.65;margin:5px 0;color:#333}"
        ".k{font-weight:700;margin-right:6px}.k{color:#1565c0}.k.red{color:#b71c1c}.k.green{color:#1b5e20}.k.grey{color:#888}"
        # 明细表(折叠)
        "table{width:100%;border-collapse:collapse;background:#fff;font-size:12px;border-radius:8px;overflow:hidden}"
        "th,td{padding:6px 8px;text-align:center;border-bottom:1px solid #eef1f4;white-space:nowrap}"
        "th{background:#1565c0;color:#fff;font-weight:600}.code{font-family:monospace;color:#666}.nm{font-weight:600}"
        ".scroll{overflow-x:auto}"
        ".legend{font-size:12px;color:#666;background:#fff;border:1px dashed #cfd6dd;border-radius:8px;padding:10px;margin-top:14px}"
        ".foot{font-size:12px;color:#888;margin-top:14px;padding-top:10px;border-top:1px solid #e0e4e8}"
        ".md pre{white-space:pre-wrap;background:#fff;border:1px solid #e0e4e8;border-radius:8px;padding:12px;font-size:13px}"
        "details{margin-top:14px}summary{cursor:pointer;font-weight:600;color:#1565c0;font-size:14px;padding:6px 0}"
    )

    html = (
        f"<!doctype html><html lang=zh><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>A股短线选股报告 {ts}</title><style>{css}</style></head><body><div class=wrap>"
        f"<h1>📊 A股短线选股报告</h1>"
        f"<div class=meta><b>生成(中国时间)</b>：{cn.strftime('%Y-%m-%d %H:%M:%S')} CST"
        f" &nbsp;|&nbsp; <b>T(数据截止)</b>：{result.get('as_of','?')} {result.get('as_of_dow','')}"
        f" &nbsp;→&nbsp; <b>买入日 T+1</b>：{result.get('buy_date','?')} {result.get('buy_dow','')}"
        f" &nbsp;→&nbsp; <b>最晚卖出 T+{result.get('hold_days','?')}</b>：{result.get('sell_by','?')} {result.get('sell_dow','')}"
        f"<br>范围：{_esc(result.get('sector','全市场'))} &nbsp;|&nbsp; {auth}"
        f"{(' · '+_esc(note)) if note else ''} &nbsp;|&nbsp; 候选 {len(cands)} 只"
        f"（universe {result.get('universe_after_filter','?')} → 打分 {result.get('scored','?')}）</div>"
        f"{warn}"
        f"<h2>选股结果（按综合排名，每卡含买入方案）</h2>{cards}"
        f"{extra}"
        f"<details><summary>量化明细（点击展开：因子原始值）</summary><div class=scroll><table><tr>"
        f"<th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>5日%</th><th>20日%</th><th>量比</th>"
        f"<th>当日量</th><th>尾盘</th><th>距MA10</th><th>60位</th><th>ATR%</th><th>额亿</th><th>流通亿</th><th>均线/MACD</th>"
        f"</tr>{detail_rows}</table></div></details>"
        f"<div class=legend><b>看卡片</b>：上排=量化分·风险等级(分)·盘口信号；中间=预期收益/置信度/R:R/仓位；"
        f"蓝条=买入价/目标/止损；下面三行=入场方式·放弃条件·催化剂。"
        f"预期收益/目标/R:R 为引擎ATR基线(Agent③可按催化剂改写)；未做消息面时 置信度/催化剂 显示 — 。</div>"
        f"<div class=foot>数据源：东方财富 / 新浪 / 腾讯（免费公开行情）。本报告为量化研究分析，"
        f"<b>非投资建议</b>；A股 T+1，当日买入次日才可卖，注意仓位与止损纪律。</div>"
        f"</div></body></html>"
    )
    path.write_text(html, encoding="utf-8")
    return str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly A-share quant screener (Agent 1 engine, 0-API)")
    ap.add_argument("--sector", default=None, help="行业/概念板块名，如 光通信；省略=全市场")
    ap.add_argument("--pool", type=int, default=40, help="进入历史因子计算的候选数")
    ap.add_argument("--top", type=int, default=15, help="最终输出候选数")
    ap.add_argument("--out", default=None, help="把结果 JSON 写到此路径")
    ap.add_argument("--hold-days", type=int, default=5, help="最多持有交易日数N(T+1买入,持有≤N)；驱动权重与回测窗口")
    ap.add_argument("--backtest", action="store_true", help="运行因子IC回测并产出建议权重")
    ap.add_argument("--validate", action="store_true",
                    help="走样本验证『综合分排名』本身的 edge（Top-K超额/胜率/多空价差），不出选股")
    ap.add_argument("--val-topk", type=int, default=5, help="验证时每截面取前K只")
    ap.add_argument("--shrink", type=float, default=0.5,
                    help="回测桶权重向中性1.0收缩的系数[0~1]，默认0.5(0=纯回测,1=全中性)，降低过拟合")
    ap.add_argument("--bt-sample", type=int, default=60, help="回测/验证样本股数")
    ap.add_argument("--weights", default=None,
                    help="权重: 默认自动检测同目录weights.json(需持有天数匹配且≤7天); 'auto'=强制用; 路径=指定; 'none'=用默认配比")
    ap.add_argument("--no-cache", action="store_true", help="禁用缓存")
    ap.add_argument("--refresh", action="store_true", help="强制重拉(绕过读缓存)")
    ap.add_argument("--no-report", action="store_true", help="不生成HTML报告")
    ap.add_argument("--no-verify", action="store_true", help="跳过最终候选的跨源价格校验")
    ap.add_argument("--no-notices", action="store_true", help="跳过近期公告/业绩预告抓取(Agent②客观种子)")
    ap.add_argument("--report-dir", default=None, help="HTML报告目录(默认 skill下 reports/)")
    args = ap.parse_args()

    global _USE_CACHE, _REFRESH, _FETCH_NOTICES
    if args.no_cache:
        _USE_CACHE = False
    if args.refresh:
        _REFRESH = True
    if args.no_notices:
        _FETCH_NOTICES = False

    here = pathlib.Path(__file__).resolve().parent
    if args.backtest:
        bt = backtest(args.sector, args.bt_sample, fwd=args.hold_days, shrink=args.shrink)
        (here / "weights.json").write_text(
            json.dumps(bt, ensure_ascii=False, indent=2), encoding="utf-8")
        print_backtest(bt)
        log(f"已写出 weights.json: {here / 'weights.json'}")
        return

    if args.validate:
        # 用与实盘一致的权重做验证（默认自动检测 weights.json）
        wp = here / "weights.json"
        vw = None
        if args.weights != "none" and wp.exists():
            try:
                obj = json.loads(wp.read_text(encoding="utf-8"))
                if obj.get("fwd_days") == args.hold_days:
                    vw = obj.get("weights")
            except Exception:  # noqa: BLE001
                pass
        v = validate(args.sector, args.bt_sample, fwd=args.hold_days,
                     top_k=args.val_topk, weights=vw)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(v, fh, ensure_ascii=False, indent=2)
            log(f"已写出验证结果: {args.out}")
        print_validation(v)
        return

    # 权重解析：显式 --weights 优先；否则自动检测同目录 weights.json
    # （需 持有天数匹配 且 不超过7天），匹配不上就回落到持有天数自适应默认权重。
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
                if obj.get("fwd_days") == args.hold_days and age_d <= 7:
                    weights = obj.get("weights")
                    log(f"自动使用回测权重(fwd={args.hold_days}, {age_d}天前): {weights}")
                else:
                    log(f"weights.json 不匹配(fwd={obj.get('fwd_days')} vs {args.hold_days}, {age_d}天前)，"
                        f"用持有天数自适应默认权重")
            except Exception:  # noqa: BLE001
                pass

    result = run(args.sector, args.pool, args.top, args.out,
                 weights=weights, hold_days=args.hold_days, verify=not args.no_verify)
    print_table(result)
    if not args.no_report:
        rp = render_html(result, args.report_dir, preview=True)
        print(f"\n📄 量化预览报告(中间版,完整版生成后会清除): {rp}")


if __name__ == "__main__":
    main()
