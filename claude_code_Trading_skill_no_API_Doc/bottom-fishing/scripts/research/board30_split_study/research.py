# -*- coding: utf-8 -*-
r"""30*（20%涨跌幅）独立机制研究。

只写 C:\Trading_analysis\research\bottom_board30_split_study，不碰生产 workflow/数据。
研究口径冻结在同目录 PRE_REGISTRATION.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2]
REPO = SKILL.parents[1]
WEEKLY = SKILL.parent / "weekly-ashare-rank"
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_board30_split_study")
OLD_DATA = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
ENGINE = SKILL / "bottom_fishing.py"
sys.path.insert(0, str(WEEKLY))
import ashare_weekly_rank as WK  # noqa: E402


REPORT_START = "2023-11-01"
COOLDOWN_WARM_START = "2023-10-01"
MAX_HOLD = 20
EMBARGO = 21
COOLDOWN = 5
SEED = 20260808
ALLOWED_PREFIXES = ("60", "00", "30")

W = dict(
    defensive=8.6,
    above_ma10=5.2,
    dif_up=4.5,
    rsv_recover=3.9,
    dd_sweet=3.7,
    above_ma5=3.7,
    gap_reclaim=4.4,
    rsv_deep=-7.4,
    downstk4=-6.3,
    zt20=-5.4,
    atr_hi=-3.5,
    fresh_low=-3.1,
)


@dataclass(frozen=True)
class Scheme:
    name: str
    zt_ret: float
    gap_drop: float
    atr_high: float
    dd_floor: float
    rsv_high: float


SCHEMES = (
    Scheme("legacy", 0.093, 0.02, 7.0, -45.0, 40.0),
    Scheme("limit20", 0.185, 0.02, 7.0, -45.0, 40.0),
    Scheme("limit20_atr10", 0.185, 0.02, 10.0, -45.0, 40.0),
    Scheme("scaled20", 0.185, 0.03, 10.0, -50.0, 45.0),
    Scheme("scaled20_rsv40", 0.185, 0.03, 10.0, -50.0, 40.0),
)
ATR_CAPS = (3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0)
DEF_THRESHOLDS = (16.0, 18.0, 20.0, 22.0, 24.0)
STOCK_THRESHOLDS = (13.0, 15.0, 17.0, 19.0, 21.0)
BASELINE_ID = "legacy|atr4|def18|stock15"

TX_HOSTS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
)


def cn_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8)))


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def json_dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pathlib.Path):
        return str(obj)
    raise TypeError(type(obj).__name__)


def fetch_kline(sym: str, bars: int) -> pd.DataFrame | None:
    return fetch_kline_between(sym, "", "", bars)


def fetch_kline_between(sym: str, start: str, end: str, bars: int) -> pd.DataFrame | None:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    last_error = ""
    for attempt in range(5):
        for base in TX_HOSTS:
            try:
                url = f"{base}?param={sym},day,{start},{end},{bars},qfq"
                req = urllib.request.Request(url, headers=headers)
                raw = urllib.request.urlopen(req, timeout=20).read()
                payload = json.loads(raw)
                rows = payload["data"][sym].get("qfqday") or payload["data"][sym].get("day") or []
                df = pd.DataFrame(
                    [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows],
                    columns=["d", "o", "c", "h", "l", "v"],
                )
                if len(df) >= 90:
                    return df.drop_duplicates("d").sort_values("d").reset_index(drop=True)
                last_error = f"rows={len(df)}"
            except Exception as exc:  # noqa: BLE001
                last_error = repr(exc)
        time.sleep(1.5 * (attempt + 1))
    print(f"[fetch] FAIL {sym}: {last_error[:160]}", flush=True)
    return None


def extend_history(refresh: bool) -> None:
    """补腾讯旧段；重叠 OHLCV 必须逐值一致后才合并。支持 checkpoint/resume。"""
    recent_path = OUT / "klines.parquet"
    meta_path = OUT / "meta.parquet"
    audit_path = OUT / "fetch_audit.json"
    for path in (recent_path, meta_path, audit_path):
        if not path.exists():
            raise FileNotFoundError(f"先完成 --fetch，缺少 {path}")
    meta = pd.read_parquet(meta_path)
    recent = pd.read_parquet(recent_path).drop_duplicates(["code", "d"])
    partial_path = OUT / "history.partial.parquet"
    if refresh and partial_path.exists():
        partial_path.unlink()
    frames: list[pd.DataFrame] = []
    if partial_path.exists():
        old = pd.read_parquet(partial_path).drop_duplicates(["code", "d"])
        frames.append(old)
        print(f"[history] resume={old.code.nunique()}只 {len(old)}行", flush=True)
    completed = set() if not frames else set(frames[0].code.unique())
    failures = []
    for k, row in meta.reset_index(drop=True).iterrows():
        code = str(row.code)
        if code in completed:
            continue
        sym = ("sh" if code.startswith("60") else "sz") + code
        df = fetch_kline_between(sym, "2022-01-01", "2023-12-31", 900)
        if df is None:
            failures.append(code)
        else:
            df = df.copy()
            df["code"] = code
            frames.append(df)
            completed.add(code)
        if (k + 1) % 25 == 0:
            checkpoint = pd.concat(frames, ignore_index=True).drop_duplicates(["code", "d"]).sort_values(["code", "d"])
            checkpoint.to_parquet(partial_path, index=False)
            frames = [checkpoint]
            print(f"[history] {k+1}/{len(meta)} success={len(completed)} fail={len(failures)} checkpoint", flush=True)
    if not frames:
        raise RuntimeError("旧段全部抓取失败")
    old = pd.concat(frames, ignore_index=True).drop_duplicates(["code", "d"]).sort_values(["code", "d"])
    if old.code.nunique() < 300:
        raise RuntimeError(f"旧段覆盖异常，仅 {old.code.nunique()} 只")

    # 同源 qfq 分段若复权基准不一致，重叠日会产生假跳空；任何 OHLCV 不一致都拒绝合并。
    overlap = recent.merge(old, on=["code", "d"], suffixes=("_new", "_old"))
    cols = ["o", "c", "h", "l", "v"]
    mismatch = np.zeros(len(overlap), dtype=bool)
    max_abs = {}
    for col in cols:
        delta = (overlap[f"{col}_new"] - overlap[f"{col}_old"]).abs()
        max_abs[col] = float(delta.max()) if len(delta) else None
        mismatch |= delta.fillna(np.inf).values > 1e-8
    if len(overlap) == 0 or mismatch.any():
        raise RuntimeError(f"新旧 qfq 重叠不一致 overlap={len(overlap)} mismatch={int(mismatch.sum())} max_abs={max_abs}")

    recent_backup = OUT / "klines_recent_640.parquet"
    if not recent_backup.exists():
        recent.to_parquet(recent_backup, index=False)
    merged = pd.concat([old, recent], ignore_index=True).drop_duplicates(["code", "d"], keep="last")
    merged = merged.sort_values(["code", "d"]).reset_index(drop=True)
    merged.to_parquet(recent_path, index=False)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["historical_extension"] = {
        "completed_at_cn": cn_now().isoformat(),
        "range": ["2022-01-01", "2023-12-31"],
        "source": "Tencent qfq same hosts",
        "history_codes": int(old.code.nunique()),
        "history_rows": int(len(old)),
        "failures_this_resume": failures,
        "overlap_rows": int(len(overlap)),
        "overlap_mismatch_rows": int(mismatch.sum()),
        "overlap_max_abs": max_abs,
        "merged_codes": int(merged.code.nunique()),
        "merged_rows": int(len(merged)),
        "merged_date_min": str(merged.d.min()),
        "merged_date_max": str(merged.d.max()),
    }
    json_dump(audit_path, audit)
    if partial_path.exists():
        partial_path.unlink()
    print(f"[history] DONE {old.code.nunique()}只; overlap={len(overlap)}逐值一致; merged={len(merged)}行 {merged.d.min()}→{merged.d.max()}")


def fetch_data(refresh: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [OUT / "klines.parquet", OUT / "meta.parquet", OUT / "index_399006.parquet"]
    if not refresh and all(p.exists() for p in targets):
        print("[fetch] 数据已存在；使用 --refresh 才会重抓。")
        return

    snapshot_path = OUT / "universe_snapshot.parquet"
    partial_path = OUT / "klines.partial.parquet"
    # 首次 --refresh 固定“当日成交额前600”快照；调用层中断后，不带 --refresh 即复用同一快照续抓，
    # 防止重启时股票池时点漂移。weekly 自身的行情缓存只作为首轮快照加速，不是研究产物。
    if not refresh and snapshot_path.exists():
        meta = pd.read_parquet(snapshot_path)
        print(f"[fetch] resume universe snapshot={len(meta)}", flush=True)
    else:
        WK._REFRESH = bool(refresh)
        spot = WK.get_spot(600).copy()
        rows = []
        for _, r in spot.iterrows():
            code, name = str(r.get("代码", "")), str(r.get("名称", ""))
            if len(code) != 6 or not code.startswith(ALLOWED_PREFIXES):
                continue
            if "ST" in name.upper() or "退" in name:
                continue
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "industry": str(r.get("行业", "") or ""),
                    "amount": pd.to_numeric(r.get("成交额"), errors="coerce"),
                }
            )
        meta = pd.DataFrame(rows).drop_duplicates("code").sort_values("code").reset_index(drop=True)
        meta["board"] = np.where(meta.code.str.startswith("30"), "30", "60+00")
        meta.to_parquet(snapshot_path, index=False)
        meta.to_csv(OUT / "universe_snapshot.csv", index=False, encoding="utf-8-sig")
        if refresh and partial_path.exists():
            partial_path.unlink()
    if len(meta) < 200:
        raise RuntimeError(f"股票池异常缩小，仅 {len(meta)} 只，拒绝研究")
    if not meta.code.str.startswith(ALLOWED_PREFIXES).all():
        raise AssertionError("股票池出现 60/00/30 之外代码")
    if "board" not in meta:
        meta["board"] = np.where(meta.code.str.startswith("30"), "30", "60+00")
    print(f"[fetch] universe={len(meta)} {meta.board.value_counts().to_dict()} source={WK._LAST_SPOT_SRC}")

    idx = fetch_kline("sz399006", 1600)
    if idx is None:
        raise RuntimeError("创业板指抓取失败")
    idx.to_parquet(OUT / "index_399006.parquet", index=False)

    frames: list[pd.DataFrame] = []
    if not refresh and partial_path.exists():
        partial = pd.read_parquet(partial_path)
        partial = partial[partial.code.isin(set(meta.code))].drop_duplicates(["code", "d"])
        if len(partial):
            frames.append(partial)
            print(f"[fetch] resume klines={partial.code.nunique()}只 {len(partial)}行", flush=True)
    completed = set() if not frames else set(frames[0].code.unique())
    failures = []
    for k, row in meta.iterrows():
        code = row.code
        if code in completed:
            continue
        sym = ("sh" if code.startswith("60") else "sz") + code
        df = fetch_kline(sym, 900)
        if df is None:
            failures.append(code)
        else:
            df = df.copy()
            df["code"] = code
            frames.append(df)
            completed.add(code)
        if (k + 1) % 25 == 0:
            checkpoint = pd.concat(frames, ignore_index=True).drop_duplicates(["code", "d"]).sort_values(["code", "d"])
            checkpoint.to_parquet(partial_path, index=False)
            frames = [checkpoint]
            print(f"[fetch] {k+1}/{len(meta)} success={len(completed)} fail={len(failures)} checkpoint", flush=True)
    if not frames:
        raise RuntimeError("全部个股 K 线抓取失败")
    allk = pd.concat(frames, ignore_index=True).sort_values(["code", "d"])
    ok_codes = set(allk.code.unique())
    meta["fetch_ok"] = meta.code.isin(ok_codes)
    coverage = float(meta.fetch_ok.mean())
    board_cov = meta.groupby("board").fetch_ok.mean().to_dict()
    if coverage < 0.90 or min(board_cov.values()) < 0.85:
        raise RuntimeError(f"K线覆盖不足 overall={coverage:.1%} board={board_cov}")
    allk.to_parquet(OUT / "klines.parquet", index=False)
    meta.to_parquet(OUT / "meta.parquet", index=False)
    meta.to_csv(OUT / "universe.csv", index=False, encoding="utf-8-sig")
    json_dump(
        OUT / "fetch_audit.json",
        {
            "fetched_at_cn": cn_now().isoformat(),
            "spot_source": WK._LAST_SPOT_SRC,
            "spot_top_by_amount": 600,
            "allowed_prefixes": list(ALLOWED_PREFIXES),
            "qfq": True,
            "bars": 900,
            "index_bars": 1600,
            "universe_n": len(meta),
            "success_n": len(ok_codes),
            "coverage": coverage,
            "coverage_by_board": board_cov,
            "failures": failures,
            "raw_date_min": str(allk.d.min()),
            "raw_date_max": str(allk.d.max()),
            "index_date_min": str(idx.d.min()),
            "index_date_max": str(idx.d.max()),
            "hosts": list(TX_HOSTS),
        },
    )
    if partial_path.exists():
        partial_path.unlink()
    print(f"[fetch] DONE {len(ok_codes)}只 {len(allk)}行 {allk.d.min()}→{allk.d.max()}")


def build_index(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.sort_values("d").drop_duplicates("d").reset_index(drop=True).copy()
    x["ma20"] = x.c.rolling(20).mean()
    x["i5"] = x.c.pct_change(5)
    x["defensive"] = (x.c < x.ma20) | (x.i5 < -0.02)
    count = 0
    def_days = []
    for flag in x.defensive:
        count = count + 1 if flag else 0
        def_days.append(count)
    x["def_days"] = def_days
    lo14, hi14 = x.l.rolling(14).min(), x.h.rolling(14).max()
    x["idx_rsv"] = (x.c - lo14) / (hi14 - lo14 + 1e-9) * 100
    x["idx_chg1"] = x.c.pct_change() * 100
    return x[["d", "defensive", "def_days", "idx_rsv", "idx_chg1"]]


def _score(hits: dict[str, bool], defensive: bool) -> tuple[float, float]:
    stock = sum(W[k] for k, flag in hits.items() if flag)
    total = stock + (W["defensive"] if defensive else 0.0)
    return round(total, 1), round(stock, 1)


def build_stock_panel(df: pd.DataFrame, idx: pd.DataFrame, code: str, board: str) -> pd.DataFrame:
    s = df.sort_values("d").drop_duplicates("d").reset_index(drop=True).copy()
    n = len(s)
    if n < 100:
        return pd.DataFrame()
    c, o, h, l, v = s.c, s.o, s.h, s.l, s.v
    hi60, lo60 = h.rolling(60).max(), l.rolling(60).min()
    dd60 = (c / hi60 - 1) * 100
    pos60 = (c - lo60) / (hi60 - lo60 + 1e-9) * 100
    ret = c.pct_change()
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / c * 100
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    rsv = (c - lo14) / (hi14 - lo14 + 1e-9) * 100
    is_low = l <= lo60 * 1.001
    days_low = np.full(n, np.nan)
    last = None
    for i, flag in enumerate(is_low.fillna(False)):
        if flag:
            last = i
        if last is not None:
            days_low[i] = i - last
    downstk = np.zeros(n, dtype=int)
    run = 0
    for i, value in enumerate(ret.values):
        run = run + 1 if i > 0 and not np.isnan(value) and value < 0 else 0
        downstk[i] = run
    dd250 = (c / h.rolling(250).max() - 1) * 100
    volx = v / v.rolling(20).mean()

    imap = idx.set_index("d")
    out = []
    for i in range(59, n):
        d = str(s.d.iloc[i])
        if d < COOLDOWN_WARM_START or d not in imap.index:
            continue
        # 完整成熟标签：T+1进场后还要有20根；不把未走完窗口当 timeout。
        if i + 1 + MAX_HOLD > n - 1:
            continue
        if pd.isna(dd60.iloc[i]) or not (dd60.iloc[i] <= -20 and pos60.iloc[i] <= 25):
            continue
        defensive = bool(imap.at[d, "defensive"])
        common = dict(
            above_ma10=bool(c.iloc[i] > ma10.iloc[i]),
            dif_up=bool((dif.iloc[i] - dif.iloc[i - 3]) > 0),
            above_ma5=bool(c.iloc[i] > ma5.iloc[i]),
            rsv_deep=bool(rsv.iloc[i] <= 15),
            downstk4=bool(downstk[i] >= 4),
            fresh_low=bool(not np.isnan(days_low[i]) and days_low[i] <= 1),
        )
        scores = {}
        for scheme in SCHEMES:
            hits = dict(common)
            hits.update(
                rsv_recover=bool(20 < rsv.iloc[i] <= scheme.rsv_high),
                dd_sweet=bool(scheme.dd_floor < dd60.iloc[i] <= -30),
                gap_reclaim=bool(o.iloc[i] < c.iloc[i - 1] * (1 - scheme.gap_drop) and c.iloc[i] > o.iloc[i]),
                zt20=bool((ret.iloc[max(0, i - 19): i + 1] >= scheme.zt_ret).any()),
                atr_hi=bool(atr.iloc[i] >= scheme.atr_high),
            )
            total, stock = _score(hits, defensive)
            scores[f"score_{scheme.name}"] = total
            scores[f"stock_score_{scheme.name}"] = stock

        entry = float(o.iloc[i + 1])
        stop, target = entry * 0.92, entry * 1.05
        outcome, hold_days = "timeout", MAX_HOLD
        if float(c.iloc[i + 1]) <= stop:
            outcome, hold_days = "stop", 1
        else:
            for j in range(i + 2, i + 1 + MAX_HOLD + 1):
                if float(l.iloc[j]) <= stop:
                    outcome, hold_days = "stop", j - (i + 1)
                    break
                if float(h.iloc[j]) >= target:
                    outcome, hold_days = "win", j - (i + 1)
                    break
        path_low = float(l.iloc[i + 1: i + 1 + MAX_HOLD + 1].min())
        mdd20 = (path_low / entry - 1) * 100
        out.append(
            {
                "code": code,
                "board": board,
                "d": d,
                "bar_pos": i,
                "close": float(c.iloc[i]),
                "entry": entry,
                "dd60": float(dd60.iloc[i]),
                "pos60": float(pos60.iloc[i]),
                "dd250": None if pd.isna(dd250.iloc[i]) else float(dd250.iloc[i]),
                "atr": float(atr.iloc[i]),
                "rsv": float(rsv.iloc[i]),
                "volx": None if pd.isna(volx.iloc[i]) else float(volx.iloc[i]),
                "defensive": defensive,
                "def_days": int(imap.at[d, "def_days"]),
                "idx_rsv": float(imap.at[d, "idx_rsv"]),
                "idx_chg1": float(imap.at[d, "idx_chg1"]),
                "outcome": outcome,
                "hold_days": hold_days,
                "mdd20": mdd20,
                "touch15": bool(mdd20 <= -15),
                **scores,
            }
        )
    return pd.DataFrame(out)


def build_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = pd.read_parquet(OUT / "klines.parquet")
    meta = pd.read_parquet(OUT / "meta.parquet")
    idx = build_index(pd.read_parquet(OUT / "index_399006.parquet"))
    board_map = meta.set_index("code").board.to_dict()
    frames = []
    for k, (code, g) in enumerate(raw.groupby("code")):
        p = build_stock_panel(g, idx, str(code), board_map.get(str(code), "?"))
        if len(p):
            frames.append(p)
        if (k + 1) % 100 == 0:
            print(f"[panel] {k+1}/{raw.code.nunique()} rows={sum(len(x) for x in frames)}", flush=True)
    if not frames:
        raise RuntimeError("未生成任何成熟底部区样本")
    panel = pd.concat(frames, ignore_index=True).sort_values(["code", "d"]).reset_index(drop=True)
    panel.to_parquet(OUT / "panel.parquet", index=False)
    idx.to_parquet(OUT / "index_panel.parquet", index=False)
    return panel, idx, meta


def candidate_id(scheme: str, atr_cap: float, def_th: float, stock_th: float) -> str:
    def fmt(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else str(x).replace(".", "p")
    return f"{scheme}|atr{fmt(atr_cap)}|def{fmt(def_th)}|stock{fmt(stock_th)}"


def qualified(panel: pd.DataFrame, scheme: str, atr_cap: float, def_th: float, stock_th: float) -> pd.DataFrame:
    total = panel[f"score_{scheme}"]
    stock = panel[f"stock_score_{scheme}"]
    mask = (panel.atr <= atr_cap) & (
        (panel.defensive & (total >= def_th)) | (~panel.defensive & (stock >= stock_th))
    )
    return panel[mask].copy()


def cooldown(signals: pd.DataFrame, n: int = COOLDOWN) -> pd.DataFrame:
    if signals.empty:
        return signals.copy()
    keep = []
    for _, group in signals.sort_values(["code", "d"]).groupby("code", sort=False):
        last_q = None
        for idx_, row in group.iterrows():
            pos = int(row.bar_pos)
            if last_q is None or pos - last_q > n:
                keep.append(idx_)
            last_q = pos  # 被冷却的原始过线同样刷新计时
    return signals.loc[keep].sort_values(["d", "code"]).reset_index(drop=True)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (center - half) * 100, (center + half) * 100


def stats(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {
            "n": 0, "win_n": 0, "stop_n": 0, "timeout_n": 0,
            "win_rate": None, "stop_rate": None, "timeout_rate": None, "ev": None,
            "win_ci95": [None, None], "stop_ci95": [None, None], "touch15_rate": None,
            "median_hold_days": None,
        }
    wins = int((df.outcome == "win").sum())
    stops = int((df.outcome == "stop").sum())
    timeouts = n - wins - stops
    win_rate, stop_rate = wins / n * 100, stops / n * 100
    return {
        "n": n,
        "win_n": wins,
        "stop_n": stops,
        "timeout_n": timeouts,
        "win_rate": win_rate,
        "stop_rate": stop_rate,
        "timeout_rate": timeouts / n * 100,
        "ev": win_rate / 100 * 5 - stop_rate / 100 * 8,
        "win_ci95": list(wilson(wins, n)),
        "stop_ci95": list(wilson(stops, n)),
        "touch15_rate": float(df.touch15.mean() * 100),
        "median_hold_days": float(df.hold_days.median()),
    }


def cutoff_before(idx: pd.DataFrame, end_date: str, embargo: int = EMBARGO) -> str:
    dates = sorted(d for d in idx.d.astype(str).unique() if d <= end_date)
    if len(dates) <= embargo:
        raise RuntimeError(f"指数交易日不足，无法计算 {end_date} embargo")
    return dates[-1 - embargo]


def period_masks(df: pd.DataFrame, idx: pd.DataFrame) -> tuple[dict[str, pd.Series], dict]:
    disc_end = cutoff_before(idx, "2024-12-31")
    val_end = cutoff_before(idx, "2025-12-31")
    masks = {
        "discovery": (df.d >= REPORT_START) & (df.d <= disc_end),
        "validation": (df.d >= "2025-01-01") & (df.d <= val_end),
        "holdout": df.d >= "2026-01-01",
        "full": df.d >= REPORT_START,
    }
    return masks, {"discovery_end": disc_end, "validation_end": val_end, "embargo_trade_days": EMBARGO}


def subset_period(df: pd.DataFrame, period: str, bounds: dict) -> pd.DataFrame:
    if period == "discovery":
        return df[(df.d >= REPORT_START) & (df.d <= bounds["discovery_end"])]
    if period == "validation":
        return df[(df.d >= "2025-01-01") & (df.d <= bounds["validation_end"])]
    if period == "holdout":
        return df[df.d >= "2026-01-01"]
    if period == "full":
        return df[df.d >= REPORT_START]
    raise KeyError(period)


def metric_row(cid: str, scheme: str, atr_cap: float, def_th: float, stock_th: float,
               signals: pd.DataFrame, bounds: dict,
               periods: tuple[str, ...] = ("discovery", "validation")) -> dict:
    row = {"candidate_id": cid, "scheme": scheme, "atr_cap": atr_cap,
           "def_threshold": def_th, "stock_threshold": stock_th}
    for period in periods:
        st = stats(subset_period(signals, period, bounds))
        for key in ("n", "win_rate", "stop_rate", "ev", "touch15_rate"):
            row[f"{period}_{key}"] = st[key]
    return row


def month_bootstrap_diff(base: pd.DataFrame, alt: pd.DataFrame, start: str, reps: int = 5000) -> dict:
    b = base[base.d >= start].copy()
    a = alt[alt.d >= start].copy()
    months = sorted(set(b.d.str[:7]) | set(a.d.str[:7]))
    if len(months) < 2:
        return {"months": len(months), "reps": reps, "win_diff_ci95": [None, None],
                "stop_diff_ci95": [None, None], "ev_diff_ci95": [None, None]}

    def counts(df: pd.DataFrame) -> dict[str, np.ndarray]:
        out = {}
        for m in months:
            g = df[df.d.str[:7] == m]
            out[m] = np.array([len(g), (g.outcome == "win").sum(), (g.outcome == "stop").sum()], float)
        return out

    bc, ac = counts(b), counts(a)
    rng = np.random.default_rng(SEED)
    diffs = []
    for _ in range(reps):
        draw = rng.choice(months, len(months), replace=True)
        bv = sum((bc[m] for m in draw), np.zeros(3))
        av = sum((ac[m] for m in draw), np.zeros(3))
        if bv[0] == 0 or av[0] == 0:
            continue
        bw, bs = bv[1] / bv[0] * 100, bv[2] / bv[0] * 100
        aw, ass = av[1] / av[0] * 100, av[2] / av[0] * 100
        diffs.append([aw - bw, ass - bs, (aw / 100 * 5 - ass / 100 * 8) - (bw / 100 * 5 - bs / 100 * 8)])
    arr = np.asarray(diffs)
    return {
        "months": len(months), "reps": len(arr),
        "win_diff_ci95": np.quantile(arr[:, 0], [0.025, 0.975]).tolist(),
        "stop_diff_ci95": np.quantile(arr[:, 1], [0.025, 0.975]).tolist(),
        "ev_diff_ci95": np.quantile(arr[:, 2], [0.025, 0.975]).tolist(),
    }


def monthly_table(base: pd.DataFrame, alt: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    months = sorted(set(base.d.str[:7]) | set(alt.d.str[:7]))
    for m in months:
        for mechanism, df in (("统一基线", base), (label, alt)):
            s = stats(df[df.d.str[:7] == m])
            rows.append({"month": m, "mechanism": mechanism, **{k: s[k] for k in
                        ("n", "win_rate", "stop_rate", "ev", "touch15_rate")}})
    return pd.DataFrame(rows)


def monthly_single(df: pd.DataFrame, mechanism: str) -> pd.DataFrame:
    rows = []
    for month, group in df.groupby(df.d.str[:7]):
        s = stats(group)
        rows.append({"month": month, "mechanism": mechanism, **{k: s[k] for k in
                    ("n", "win_rate", "stop_rate", "ev", "touch15_rate")}})
    return pd.DataFrame(rows)


def temporal_comparison(mechanisms: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    buckets = {
        "2023Q4": ("2023-11-01", "2023-12-31"),
        "2024H1": ("2024-01-01", "2024-06-30"),
        "2024H2": ("2024-07-01", "2024-12-31"),
        "2025H1": ("2025-01-01", "2025-06-30"),
        "2025H2": ("2025-07-01", "2025-12-31"),
        "2026H1": ("2026-01-01", "2026-06-30"),
        "2026H2_mature": ("2026-07-01", "2026-12-31"),
    }
    for bucket, (start, end) in buckets.items():
        for name, df in mechanisms.items():
            s = stats(df[(df.d >= start) & (df.d <= end)])
            rows.append({"bucket": bucket, "mechanism": name, **{k: s[k] for k in
                        ("n", "win_rate", "stop_rate", "ev", "touch15_rate")}})
    return pd.DataFrame(rows)


def reproduce_pinned_legacy() -> dict:
    required = [OLD_DATA / "panel.parquet", OLD_DATA / "klines.parquet"]
    if not all(p.exists() for p in required):
        return {"status": "skipped", "reason": "pinned bottom_ml data missing"}
    p = pd.read_parquet(required[0]).copy()
    k = pd.read_parquet(required[1])[["code", "d"]].sort_values(["code", "d"]).copy()
    k["bar_pos"] = k.groupby("code").cumcount()
    p = p.merge(k, on=["code", "d"], how="left")
    q = ((p.mkt_def & (p.score >= 18)) | (~p.mkt_def & (p.stock_score >= 15))) & (p.atr <= 4)
    raw = p[q].copy()
    cool = cooldown(raw)
    def basic(df: pd.DataFrame) -> dict:
        n = len(df)
        wins = int((df.outcome == "win").sum())
        stops = int((df.outcome == "stop").sum())
        return {
            "n": n,
            "win_rate": wins / n * 100,
            "stop_rate": stops / n * 100,
            "ev": wins / n * 5 - stops / n * 8,
        }
    raw_s, cool_s = basic(raw), basic(cool)
    expected = {"cool_win_rate": 67.1, "cool_stop_rate": 26.2}
    passed = abs(cool_s["win_rate"] - expected["cool_win_rate"]) <= 1.5 and abs(
        cool_s["stop_rate"] - expected["cool_stop_rate"]
    ) <= 1.5
    return {
        "status": "pass" if passed else "fail",
        "data_hashes": {p.name: sha256(p) for p in required},
        "raw": raw_s,
        "cooldown5": cool_s,
        "ledger_reference": expected,
        "tolerance_pp": 1.5,
    }


def fmt_pct(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:.1f}%"


def fmt_ev(x) -> str:
    return "-" if x is None or pd.isna(x) else f"{x:+.2f}%"


def table_line(name: str, value: dict) -> str:
    def rate_ci(rate_key: str, ci_key: str) -> str:
        ci = value[ci_key]
        return f"{fmt_pct(value[rate_key])} [{fmt_pct(ci[0])}, {fmt_pct(ci[1])}]"
    return (f"| {name} | {value['n']} | {rate_ci('win_rate', 'win_ci95')} | {rate_ci('stop_rate', 'stop_ci95')} | "
            f"{fmt_ev(value['ev'])} | {fmt_pct(value['touch15_rate'])} |")


def evaluate(panel: pd.DataFrame, idx: pd.DataFrame, meta: pd.DataFrame) -> dict:
    panel = panel[panel.board.isin(["30", "60+00"])].copy()
    if not set(panel.code.str[:2].unique()).issubset(set(ALLOWED_PREFIXES)):
        raise AssertionError("panel 出现 60/00/30 之外代码")
    _, bounds = period_masks(panel, idx)

    base_raw = qualified(panel, "legacy", 4.0, 18.0, 15.0)
    base_all = cooldown(base_raw)
    base30 = base_all[base_all.board == "30"].copy()
    base10 = base_all[base_all.board == "60+00"].copy()
    base30_metrics = {p: stats(subset_period(base30, p, bounds)) for p in ("discovery", "validation", "holdout", "full")}
    min_disc = max(60, math.ceil(base30_metrics["discovery"]["n"] * 0.50))
    min_val = max(60, math.ceil(base30_metrics["validation"]["n"] * 0.50))

    grid_rows = []
    p30 = panel[panel.board == "30"].copy()
    for scheme in SCHEMES:
        for atr_cap in ATR_CAPS:
            for def_th in DEF_THRESHOLDS:
                for stock_th in STOCK_THRESHOLDS:
                    cid = candidate_id(scheme.name, atr_cap, def_th, stock_th)
                    sig = cooldown(qualified(p30, scheme.name, atr_cap, def_th, stock_th))
                    row = metric_row(cid, scheme.name, atr_cap, def_th, stock_th, sig, bounds)
                    disc, val = (
                        {k.removeprefix("discovery_"): v for k, v in row.items() if k.startswith("discovery_")},
                        {k.removeprefix("validation_"): v for k, v in row.items() if k.startswith("validation_")},
                    )
                    guard = (
                        disc["n"] >= min_disc and val["n"] >= min_val
                        and val["stop_rate"] <= base30_metrics["validation"]["stop_rate"] + 2.0
                        and val["win_rate"] >= base30_metrics["validation"]["win_rate"] - 2.0
                        and val["ev"] >= base30_metrics["validation"]["ev"] - 0.25
                    )
                    row["passes_guardrails"] = bool(guard)
                    row["robust_min_ev"] = min(disc["ev"], val["ev"])
                    row["worst_stop_rate"] = max(disc["stop_rate"], val["stop_rate"])
                    row["mean_win_rate"] = (disc["win_rate"] + val["win_rate"]) / 2
                    row["dev_n"] = disc["n"] + val["n"]
                    grid_rows.append(row)
    grid = pd.DataFrame(grid_rows)
    passing = grid[grid.passes_guardrails].copy()
    if passing.empty:
        raise AssertionError("连基线都未通过预注册 guardrails，实现存在错误")
    selected_row = passing.sort_values(
        ["robust_min_ev", "worst_stop_rate", "mean_win_rate", "dev_n", "candidate_id"],
        ascending=[False, True, False, False, True],
    ).iloc[0]
    selected_id = str(selected_row.candidate_id)
    # 物理隔离：candidate_grid 只含 discovery/validation。先写网格与冻结文件，随后才计算所选机制的 holdout。
    grid.to_csv(OUT / "candidate_grid.csv", index=False, encoding="utf-8-sig")
    freeze = {
        "schema": "bottom-board30-selection-freeze/v1",
        "frozen_at_cn": cn_now().isoformat(),
        "selected_candidate_id": selected_id,
        "selection_fields": selected_row.to_dict(),
        "candidate_grid_sha256": sha256(OUT / "candidate_grid.csv"),
        "candidate_grid_contains_holdout": False,
        "selection_used_periods": ["discovery", "validation"],
    }
    json_dump(OUT / "selection_freeze.json", freeze)
    selected30 = cooldown(qualified(
        p30,
        str(selected_row.scheme),
        float(selected_row.atr_cap),
        float(selected_row.def_threshold),
        float(selected_row.stock_threshold),
    ))
    split_all = pd.concat([base10, selected30], ignore_index=True).sort_values(["d", "code"])

    # 冻结之后才做的 post-hoc 解释性敏感性；不得替换 selected_id 或 shadow gate。
    posthoc_rows = []
    for cap in (4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 99.0):
        sig = cooldown(qualified(p30, "limit20_atr10", cap, 24.0, 15.0))
        row = {"test": "atr_boundary", "variant": "no_hard_cap" if cap == 99.0 else f"atr_cap_{cap:g}"}
        for period in ("discovery", "validation", "holdout", "full"):
            st = stats(subset_period(sig, period, bounds))
            for key in ("n", "win_rate", "stop_rate", "ev"):
                row[f"{period}_{key}"] = st[key]
        posthoc_rows.append(row)
    for scheme in (s.name for s in SCHEMES):
        sig = cooldown(qualified(p30, scheme, 8.0, 24.0, 15.0))
        row = {"test": "semantic_ablation_at_frozen_thresholds", "variant": scheme}
        for period in ("discovery", "validation", "holdout", "full"):
            st = stats(subset_period(sig, period, bounds))
            for key in ("n", "win_rate", "stop_rate", "ev"):
                row[f"{period}_{key}"] = st[key]
        posthoc_rows.append(row)
    posthoc = pd.DataFrame(posthoc_rows)

    periods = ("discovery", "validation", "holdout", "full")
    comparisons = {}
    for period in periods:
        comparisons[period] = {
            "baseline_30": stats(subset_period(base30, period, bounds)),
            "selected_30": stats(subset_period(selected30, period, bounds)),
            "baseline_10group": stats(subset_period(base10, period, bounds)),
            "baseline_all": stats(subset_period(base_all, period, bounds)),
            "split_all": stats(subset_period(split_all, period, bounds)),
        }

    hold_b = comparisons["holdout"]["baseline_30"]
    hold_s = comparisons["holdout"]["selected_30"]
    hold_all_b = comparisons["holdout"]["baseline_all"]
    hold_all_s = comparisons["holdout"]["split_all"]
    retention = hold_s["n"] / hold_b["n"] * 100 if hold_b["n"] else 0.0
    period_better = sum(
        comparisons[p]["selected_30"]["ev"] > comparisons[p]["baseline_30"]["ev"]
        for p in ("discovery", "validation", "holdout")
    )
    worst_stop_worsening = max(
        comparisons[p]["selected_30"]["stop_rate"] - comparisons[p]["baseline_30"]["stop_rate"]
        for p in ("discovery", "validation", "holdout")
    )
    gate = {
        "holdout_30_ev_not_worse": hold_s["ev"] >= hold_b["ev"],
        "holdout_30_win_plus2_or_stop_minus2": (
            hold_s["win_rate"] - hold_b["win_rate"] >= 2.0
            or hold_b["stop_rate"] - hold_s["stop_rate"] >= 2.0
        ),
        "holdout_combined_ev_not_worse": hold_all_s["ev"] >= hold_all_b["ev"],
        "holdout_signal_retention_ge50": retention >= 50.0,
        "two_of_three_period_ev_better_and_no_stop_worse5": period_better >= 2 and worst_stop_worsening <= 5.0,
    }
    worth_shadow = all(gate.values()) and selected_id != BASELINE_ID

    boot_hold30 = month_bootstrap_diff(base30, selected30, "2026-01-01")
    boot_full30 = month_bootstrap_diff(base30, selected30, REPORT_START)
    statistically_confirmed = (
        boot_hold30["win_diff_ci95"][0] is not None
        and boot_hold30["win_diff_ci95"][0] > 0
        and boot_hold30["stop_diff_ci95"][1] < 0
        and boot_hold30["ev_diff_ci95"][0] > 0
    )
    monthly30 = monthly_table(base30[base30.d >= REPORT_START], selected30[selected30.d >= REPORT_START], "30独立机制")
    monthlyall = monthly_table(base_all[base_all.d >= REPORT_START], split_all[split_all.d >= REPORT_START], "分组组合")
    monthly10 = monthly_single(base10[base10.d >= REPORT_START], "60+00旧机制")
    bmonth = monthly30[monthly30.mechanism == "统一基线"].set_index("month")
    amonth = monthly30[monthly30.mechanism == "30独立机制"].set_index("month")
    month_delta = (amonth.ev - bmonth.ev).dropna()
    monthly_direction = {
        "calendar_months": int(len(set(monthly30.month))),
        "months_both_nonempty": int(len(month_delta)),
        "ev_better_months": int((month_delta > 1e-12).sum()),
        "ev_equal_months": int((month_delta.abs() <= 1e-12).sum()),
        "ev_worse_months": int((month_delta < -1e-12).sum()),
    }
    temporal = temporal_comparison({
        "30旧机制": base30,
        "30新机制": selected30,
        "60+00旧机制": base10,
        "全部旧统一基线": base_all,
        "60+00旧+30新": split_all,
    })
    monthly30.to_csv(OUT / "monthly_30_comparison.csv", index=False, encoding="utf-8-sig")
    monthlyall.to_csv(OUT / "monthly_all_comparison.csv", index=False, encoding="utf-8-sig")
    monthly10.to_csv(OUT / "monthly_60_00_baseline.csv", index=False, encoding="utf-8-sig")
    temporal.to_csv(OUT / "temporal_comparison.csv", index=False, encoding="utf-8-sig")
    posthoc.to_csv(OUT / "posthoc_sensitivity.csv", index=False, encoding="utf-8-sig")
    base_all[base_all.d >= REPORT_START].to_csv(OUT / "signals_unified_baseline.csv", index=False, encoding="utf-8-sig")
    split_all[split_all.d >= REPORT_START].to_csv(OUT / "signals_split_selected.csv", index=False, encoding="utf-8-sig")

    result = {
        "schema": "bottom-board30-split-study/v1",
        "generated_at_cn": cn_now().isoformat(),
        "production_modified": False,
        "universe_rule": "current top600 by amount; only 60*/00*/30*; exclude ST/退",
        "groups": {"10pct": ["60", "00"], "20pct": ["30"]},
        "universe": {
            "snapshot_n": int(len(meta)),
            "fetch_ok_n": int(meta.fetch_ok.sum()),
            "by_board": meta.groupby("board").size().to_dict(),
            "panel_codes_by_board": panel.groupby("board").code.nunique().to_dict(),
        },
        "data_window": {
            "report_start": REPORT_START,
            "panel_signal_min": str(panel.d.min()),
            "panel_signal_max_mature": str(panel.d.max()),
            "index_max": str(idx.d.max()),
            **bounds,
        },
        "label": "T+1 open; entry-day close stop; then daily low stop before high target; +5/-8; 20d",
        "cooldown": "N=5 stock bars; suppressed qualified signal refreshes clock",
        "candidate_count": int(len(grid)),
        "passing_count": int(len(passing)),
        "selection_min_n": {"discovery": min_disc, "validation": min_val},
        "baseline_candidate_id": BASELINE_ID,
        "selected_candidate": selected_row.to_dict(),
        "selection_freeze": freeze,
        "comparisons": comparisons,
        "holdout_deltas_30": {
            "win_pp": hold_s["win_rate"] - hold_b["win_rate"],
            "stop_pp": hold_s["stop_rate"] - hold_b["stop_rate"],
            "ev_pp_per_trade": hold_s["ev"] - hold_b["ev"],
            "signal_retention_pct": retention,
        },
        "holdout_deltas_all": {
            "win_pp": hold_all_s["win_rate"] - hold_all_b["win_rate"],
            "stop_pp": hold_all_s["stop_rate"] - hold_all_b["stop_rate"],
            "ev_pp_per_trade": hold_all_s["ev"] - hold_all_b["ev"],
        },
        "bootstrap_month_cluster": {"holdout_30": boot_hold30, "full_30": boot_full30},
        "monthly_direction_30": monthly_direction,
        "shadow_entry_gate": gate,
        "worth_shadow_discussion": worth_shadow,
        "holdout_statistically_confirmed": statistically_confirmed,
        "production_change_supported": False,
        "posthoc": {
            "used_in_selection": False,
            "atr8_was_preregistered_grid_upper_boundary": True,
            "development_continued_improving_beyond_atr8": True,
            "development_saturated_by_atr12_equals_no_hard_cap": True,
            "interpretation": "ATR8 is grid-optimal, not global-optimal; future shadow should compare cap8/cap9/no-hard-cap",
        },
        "legacy_reproduction": reproduce_pinned_legacy(),
        "biases": [
            "today-top600 survivorship/liquidity selection bias",
            "qfq history; no commissions, slippage or limit-down fill failure",
            "candidate family searched on discovery+validation; only 2026 is untouched holdout",
            "monthly outcomes cluster strongly; event-level confidence intervals are not independence proof",
        ],
    }
    json_dump(OUT / "summary.json", result)
    write_report(result)
    write_manifest()
    return result


def write_report(result: dict) -> None:
    sel = result["selected_candidate"]
    worth = result["worth_shadow_discussion"]
    lines = [
        "# 30*（20%涨跌幅）独立机制研究结果",
        "",
        f"生成时间（北京时间）：{result['generated_at_cn']}",
        "",
        f"结论：**{'达到进入生产外 shadow 讨论门槛' if worth else '未达到进入 shadow 讨论门槛'}**；"
        f"**{'统计确认' if result['holdout_statistically_confirmed'] else 'holdout统计未确认'}**。",
        "现有 bottom-fishing workflow 与生产权重未修改；本研究不支持直接改生产规则。",
        "",
        "## 冻结口径",
        "",
        f"- 股票池：{result['universe_rule']}；10%组=`60*+00*`，20%组=`30*`。",
        f"- 报告窗：{result['data_window']['report_start']} 至成熟信号 {result['data_window']['panel_signal_max_mature']}。",
        f"- discovery 截止 {result['data_window']['discovery_end']}；validation 截止 {result['data_window']['validation_end']}；边界 embargo={EMBARGO}交易日。",
        f"- 标签：{result['label']}；旋转门：{result['cooldown']}。",
        f"- 搜索 {result['candidate_count']} 个预注册候选，{result['passing_count']} 个通过开发/验证 guardrails。",
        "",
        "## 选中的 30* 机制",
        "",
        f"`{sel['candidate_id']}`（语义={sel['scheme']}，ATR≤{sel['atr_cap']}，防守总分≥{sel['def_threshold']}，非防守个股分≥{sel['stock_threshold']}）。",
        "",
    ]
    for period, title in (("discovery", "Discovery"), ("validation", "Validation"), ("holdout", "2026 最终 holdout"), ("full", "全扩窗（描述性）")):
        comp = result["comparisons"][period]
        lines.extend([
            f"## {title}", "",
            "| 口径 | n | 胜率 [Wilson95%CI] | 暴雷率 [Wilson95%CI] | EV代理/笔 | 信号后20日触-15%* |",
            "|---|---:|---:|---:|---:|---:|",
            table_line("30*统一基线", comp["baseline_30"]),
            table_line("30*独立机制", comp["selected_30"]),
            table_line("60*+00*合并旧机制", comp["baseline_10group"]),
            table_line("全部统一基线", comp["baseline_all"]),
            table_line("60+00基线 + 30独立", comp["split_all"]),
            "",
            "\\* 该列无视+5/-8已退出，继续观察信号后20日路径；不是实际持仓深尾率，也不参与选参。",
            "",
        ])
    d30 = result["holdout_deltas_30"]
    dall = result["holdout_deltas_all"]
    boot = result["bootstrap_month_cluster"]["holdout_30"]
    md = result["monthly_direction_30"]
    repro = result["legacy_reproduction"]
    lines.extend([
        "## 与旧存档口径的对账", "",
        f"- 固定旧面板 N=5 复现：n={repro['cooldown5']['n']}，胜率={repro['cooldown5']['win_rate']:.1f}%，"
        f"雷率={repro['cooldown5']['stop_rate']:.1f}%，EV={repro['cooldown5']['ev']:+.2f}%/笔；"
        f"存档参考={repro['ledger_reference']['cool_win_rate']:.1f}%/{repro['ledger_reference']['cool_stop_rate']:.1f}%，在±{repro['tolerance_pp']:.1f}pp门限内。",
        f"- 本次 2023-11→2026 成熟扩窗、全部旧统一机制：n={result['comparisons']['full']['baseline_all']['n']}，"
        f"胜率={result['comparisons']['full']['baseline_all']['win_rate']:.1f}%，"
        f"雷率={result['comparisons']['full']['baseline_all']['stop_rate']:.1f}%，"
        f"EV={result['comparisons']['full']['baseline_all']['ev']:+.2f}%/笔。股票池时点和窗口已更新，不能把两组绝对值当成同一批样本。",
        "",
        "## Holdout 增量与稳健性", "",
        f"- 30*：胜率 {d30['win_pp']:+.2f}pp，暴雷率 {d30['stop_pp']:+.2f}pp，EV {d30['ev_pp_per_trade']:+.3f}%/笔，信号保留 {d30['signal_retention_pct']:.1f}%。",
        f"- 全组合：胜率 {dall['win_pp']:+.2f}pp，暴雷率 {dall['stop_pp']:+.2f}pp，EV {dall['ev_pp_per_trade']:+.3f}%/笔。",
        f"- 按月簇 bootstrap（{boot['months']}个月，{boot['reps']}次）：胜率差95%CI={boot['win_diff_ci95']}，暴雷率差95%CI={boot['stop_diff_ci95']}，EV差95%CI={boot['ev_diff_ci95']}。",
        f"- 统计确认：{'是' if result['holdout_statistically_confirmed'] else '否；holdout区间跨0，只能视为待复验点估计'}。",
        f"- 月度方向：双方都有信号的{md['months_both_nonempty']}个月中，EV改善{md['ev_better_months']}、"
        f"持平{md['ev_equal_months']}、变差{md['ev_worse_months']}；不是逐月稳定增益。",
        "",
        "## Post-hoc ATR 边界敏感性（不参与选参）", "",
        "冻结候选的 ATR=8 是预注册网格上边界。扩展诊断显示开发段在9/10/12仍改善，12与无硬cap才饱和；",
        "因此本研究没有识别出可直接生产化的全局最优 ATR。详见 `posthoc_sensitivity.csv`。",
        "2026 holdout 已被打开，这些边界结果只能生成后续 shadow 假设，不能反过来改本次候选。",
        "",
        "## 预注册 shadow 门槛", "",
    ])
    for key, value in result["shadow_entry_gate"].items():
        lines.append(f"- {'✓' if value else '✗'} `{key}`")
    repro = result["legacy_reproduction"]
    lines.extend([
        "", "## 可复现性与限制", "",
        f"- 固定旧面板复现：`{repro.get('status')}`。",
        "- 当前成交额前600回看历史，存在幸存者/流动性选择偏差；绝对胜率需打折。",
        "- 前复权、未计佣金滑点、未模拟20%板跌停封单导致的止损成交失败。",
        "- 月度胜负强成簇；bootstrap CI 跨0时，不能把点估计优势写成已证实 alpha。",
        "- 即使达到门槛，也只能进入 production 外 shadow 复验，不能直接改不可变引擎。",
        "",
        "详细文件：`summary.json`、`candidate_grid.csv`、`monthly_*_comparison.csv`、`signals_*.csv`、`SOURCE_MANIFEST.json`。",
    ])
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest() -> None:
    source_files = [HERE / "research.py", HERE / "verify.py", HERE / "README.md", HERE / "PRE_REGISTRATION.md", ENGINE]
    data_files = [OUT / "fetch_audit.json", OUT / "index_399006.parquet", OUT / "klines.parquet",
                  OUT / "meta.parquet", OUT / "panel.parquet", OUT / "summary.json",
                  OUT / "selection_freeze.json", OUT / "verification.json"]
    manifest = {
        "schema": "bottom-board30-source-manifest/v1",
        "generated_at_cn": cn_now().isoformat(),
        "production_modified": False,
        "source_files": {str(p): {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in source_files if p.exists()},
        "data_files": {str(p): {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in data_files if p.exists()},
    }
    json_dump(OUT / "SOURCE_MANIFEST.json", manifest)


def analyze() -> dict:
    required = [OUT / "klines.parquet", OUT / "meta.parquet", OUT / "index_399006.parquet", OUT / "fetch_audit.json"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"缺研究数据，先运行 --fetch: {missing}")
    panel, idx, meta = build_panel()
    result = evaluate(panel, idx, meta)
    print(f"[result] selected={result['selected_candidate']['candidate_id']}")
    print(f"[result] worth_shadow_discussion={result['worth_shadow_discussion']}")
    print(f"[result] report={OUT / 'REPORT.md'}")
    return result


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="抓取本研究独立数据")
    ap.add_argument("--analyze", action="store_true", help="构建面板并执行预注册 A/B")
    ap.add_argument("--extend-history", action="store_true", help="补截至2023-12-31旧段并校验重叠后合并")
    ap.add_argument("--manifest", action="store_true", help="刷新研究源码与数据 hash manifest")
    ap.add_argument("--refresh", action="store_true", help="重抓本研究数据")
    args = ap.parse_args(argv)
    if not args.fetch and not args.analyze and not args.extend_history and not args.manifest:
        args.analyze = True
    if args.fetch:
        fetch_data(args.refresh)
    if args.extend_history:
        extend_history(args.refresh)
    if args.analyze:
        analyze()
    if args.manifest:
        write_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
