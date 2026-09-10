# -*- coding: utf-8 -*-
r"""Agent 1.5 book-inspired price-volume veto study.

Research output only: C:\Trading_analysis\research\bottom_agent15_book_filter
Production skill, engine, state, and reports are read-only inputs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys
import time
import urllib.request

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2]
WEEKLY = SKILL.parent / "weekly-ashare-rank"
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
RAW_DIR = OUT / "raw_by_code"
ENGINE = SKILL / "bottom_fishing.py"
SHADOW = SKILL / "state" / "bottom_shadow_log.jsonl"
PREREG = HERE / "PRE_REGISTRATION.md"
sys.path.insert(0, str(WEEKLY))
import ashare_weekly_rank as WK  # noqa: E402


STUDY_VERSION = "agent15-book-tape-v0.1.0"
REQUESTED_START = "2024-01-01"
REQUESTED_END = "2026-09-11"
WARM_START = "2023-01-01"
COOLDOWN = 5
MAX_HOLD = 20
ALLOWED_PREFIXES = ("60", "00", "30")
TARGETS = {"300450": "先导智能", "002709": "天赐材料"}
SEGMENTS = (("2023-01-01", "2025-02-28", 900), ("2024-11-01", REQUESTED_END, 900))
TX_HOSTS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
)
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


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def json_default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pathlib.Path):
        return str(obj)
    raise TypeError(type(obj).__name__)


def json_dump(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")


def cn_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8)))


def shadow_rows() -> list[dict]:
    if not SHADOW.exists():
        return []
    return [json.loads(x) for x in SHADOW.read_text(encoding="utf-8").splitlines() if x.strip()]


def _fetch_segment(sym: str, start: str, end: str, bars: int) -> pd.DataFrame | None:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    last_error = ""
    for attempt in range(4):
        for base in TX_HOSTS:
            try:
                url = f"{base}?param={sym},day,{start},{end},{bars},qfq"
                req = urllib.request.Request(url, headers=headers)
                payload = json.loads(urllib.request.urlopen(req, timeout=20).read())
                node = payload.get("data", {}).get(sym, {})
                rows = node.get("qfqday") or node.get("day") or []
                frame = pd.DataFrame(
                    [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows],
                    columns=["d", "o", "c", "h", "l", "v"],
                )
                if len(frame):
                    return frame.drop_duplicates("d").sort_values("d").reset_index(drop=True)
                last_error = "empty"
            except Exception as exc:  # noqa: BLE001
                last_error = repr(exc)
        time.sleep(0.8 * (attempt + 1))
    print(f"[fetch] FAIL segment {sym} {start}..{end}: {last_error[:140]}", flush=True)
    return None


def _merge_segments(sym: str) -> tuple[pd.DataFrame | None, dict]:
    pieces = []
    for start, end, bars in SEGMENTS:
        frame = _fetch_segment(sym, start, end, bars)
        if frame is not None:
            pieces.append(frame)
    if not pieces:
        return None, {"overlap_rows": 0, "overlap_mismatch": None}
    overlap_rows, mismatch = 0, 0
    if len(pieces) == 2:
        overlap = pieces[0].merge(pieces[1], on="d", suffixes=("_a", "_b"))
        overlap_rows = len(overlap)
        for col in ("o", "c", "h", "l", "v"):
            mismatch += int(((overlap[f"{col}_a"] - overlap[f"{col}_b"]).abs() > 1e-8).sum())
        if overlap_rows and mismatch:
            raise RuntimeError(f"{sym} qfq overlap mismatch cells={mismatch}")
    merged = pd.concat(pieces, ignore_index=True).drop_duplicates("d", keep="last").sort_values("d")
    return merged.reset_index(drop=True), {"overlap_rows": overlap_rows, "overlap_mismatch": mismatch}


def _current_universe(refresh: bool) -> tuple[pd.DataFrame, str]:
    snapshot = OUT / "universe_snapshot.csv"
    if snapshot.exists() and not refresh:
        return pd.read_csv(snapshot, dtype={"code": str}), "frozen_snapshot"
    WK._REFRESH = bool(refresh)
    spot = WK.get_spot(600).copy()
    source = str(WK._LAST_SPOT_SRC)
    # Eastmoney pagination may be cut off after a few pages while still returning
    # a non-empty partial result.  In that case require the repository's public
    # Sina fallback to restore the requested 600-row amount-ranked snapshot.
    if len(spot) < 500:
        sina = WK._sina_spot(600)  # noqa: SLF001 - same repository data adapter
        if sina is not None and len(sina) > len(spot):
            spot = sina.copy()
            source = "sina_full_fallback"
    if source == "seed":
        raise RuntimeError("成交额前600退化为 seed，拒绝把它当主回测股票池")
    rows = []
    for _, row in spot.iterrows():
        code, name = str(row.get("代码", "")).zfill(6), str(row.get("名称", ""))
        if len(code) != 6 or not code.startswith(ALLOWED_PREFIXES):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        rows.append({
            "code": code,
            "name": name,
            "industry": str(row.get("行业", "") or ""),
            "amount": pd.to_numeric(row.get("成交额"), errors="coerce"),
            "aggregate_eligible": True,
            "forced_reason": "",
        })
    meta = pd.DataFrame(rows).drop_duplicates("code")
    if len(meta) < 300:
        raise RuntimeError(f"主股票池异常，仅 {len(meta)} 只")
    existing = set(meta.code)
    extras: dict[str, dict] = {}
    for row in shadow_rows():
        code = str(row.get("code", "")).zfill(6)
        if code not in existing and code.startswith(ALLOWED_PREFIXES):
            extras[code] = {"name": str(row.get("name", code)), "reason": "shadow_log"}
    for code, name in TARGETS.items():
        if code not in existing:
            extras.setdefault(code, {"name": name, "reason": "target_case"})
    if extras:
        add = pd.DataFrame([{
            "code": code,
            "name": info["name"],
            "industry": "",
            "amount": np.nan,
            "aggregate_eligible": False,
            "forced_reason": info["reason"],
        } for code, info in extras.items()])
        meta = pd.concat([meta, add], ignore_index=True)
    meta = meta.sort_values("code").reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    meta.to_csv(snapshot, index=False, encoding="utf-8-sig")
    return meta, source


def fetch_data(refresh: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    meta, spot_source = _current_universe(refresh)
    print(f"[fetch] universe total={len(meta)} aggregate={int(meta.aggregate_eligible.sum())} source={spot_source}", flush=True)
    index_path = OUT / "index_399006.csv.gz"
    index_frame, index_overlap = _merge_segments("sz399006")
    if index_frame is None or len(index_frame) < 300:
        raise RuntimeError("创业板指历史不足")
    index_frame.to_csv(index_path, index=False, compression="gzip")

    failures, overlap_rows, overlap_mismatch = [], 0, 0

    def fetch_code(code: str) -> tuple[str, bool, int, int]:
        path = RAW_DIR / f"{code}.csv.gz"
        overlap_path = RAW_DIR / f"{code}.overlap.json"
        if path.exists() and not refresh:
            if overlap_path.exists():
                cached_audit = json.loads(overlap_path.read_text(encoding="utf-8"))
                return code, True, int(cached_audit["overlap_rows"]), int(cached_audit["overlap_mismatch"])
            return code, True, 0, 0
        sym = ("sh" if code.startswith("60") else "sz") + code
        frame, one_audit = _merge_segments(sym)
        if frame is None or len(frame) < 90:
            return code, False, 0, 0
        frame.to_csv(path, index=False, compression="gzip")
        json_dump(overlap_path, {
            "code": code,
            "segments": SEGMENTS,
            "rows": len(frame),
            "date_min": str(frame.d.min()),
            "date_max": str(frame.d.max()),
            "overlap_rows": int(one_audit["overlap_rows"]),
            "overlap_mismatch": int(one_audit["overlap_mismatch"] or 0),
        })
        return code, True, int(one_audit["overlap_rows"]), int(one_audit["overlap_mismatch"] or 0)

    codes = meta.code.astype(str).str.zfill(6).tolist()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_code, code): code for code in codes}
        for k, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, ok, overlap, mismatch = future.result()
            if not ok:
                failures.append(code)
            overlap_rows += overlap
            overlap_mismatch += mismatch
            if k % 25 == 0:
                print(f"[fetch] {k}/{len(meta)} fail={len(failures)}", flush=True)

    frames, ok_codes = [], []
    for code in meta.code.astype(str):
        path = RAW_DIR / f"{code}.csv.gz"
        if path.exists() and code not in set(failures):
            frame = pd.read_csv(path, dtype={"d": str})
            frame["code"] = code
            frames.append(frame)
            ok_codes.append(code)
    if not frames:
        raise RuntimeError("没有可用个股日线")
    allk = pd.concat(frames, ignore_index=True).drop_duplicates(["code", "d"]).sort_values(["code", "d"])
    meta["fetch_ok"] = meta.code.isin(set(ok_codes))
    agg = meta[meta.aggregate_eligible]
    coverage = float(agg.fetch_ok.mean())
    if coverage < 0.90:
        raise RuntimeError(f"主股票池 K 线覆盖不足 {coverage:.1%}")
    allk.to_csv(OUT / "klines.csv.gz", index=False, compression="gzip")
    meta.to_csv(OUT / "universe.csv", index=False, encoding="utf-8-sig")
    effective_end = min(REQUESTED_END, str(index_frame.d.max()))
    json_dump(OUT / "fetch_audit.json", {
        "schema": "bottom-agent15-fetch/v1",
        "study_version": STUDY_VERSION,
        "fetched_at_cn": cn_now().isoformat(),
        "requested_window": [REQUESTED_START, REQUESTED_END],
        "warm_start": WARM_START,
        "effective_end": effective_end,
        "spot_source": spot_source,
        "spot_top_by_amount": 600,
        "point_in_time_universe": False,
        "qfq": True,
        "segments": SEGMENTS,
        "universe_total_n": len(meta),
        "aggregate_universe_n": int(meta.aggregate_eligible.sum()),
        "success_total_n": len(ok_codes),
        "aggregate_coverage": coverage,
        "failures": failures,
        "stock_date_min": str(allk.d.min()),
        "stock_date_max": str(allk.d.max()),
        "index_date_min": str(index_frame.d.min()),
        "index_date_max": str(index_frame.d.max()),
        "overlap_rows_checked": overlap_rows + int(index_overlap["overlap_rows"]),
        "overlap_mismatch_cells": overlap_mismatch + int(index_overlap["overlap_mismatch"] or 0),
        "engine_sha256": sha256(ENGINE),
        "shadow_sha256": sha256(SHADOW),
        "pre_registration_sha256": sha256(PREREG),
        "research_source_sha256": sha256(HERE / "research.py"),
        "hosts": TX_HOSTS,
    })
    print(f"[fetch] DONE {len(ok_codes)}只 {len(allk)}行, effective_end={effective_end}", flush=True)


def build_index(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.sort_values("d").drop_duplicates("d").reset_index(drop=True).copy()
    x["ma20"] = x.c.rolling(20).mean()
    x["i5"] = x.c.pct_change(5)
    x["defensive"] = (x.c < x.ma20) | (x.i5 < -0.02)
    run, days = 0, []
    for flag in x.defensive:
        run = run + 1 if flag else 0
        days.append(run)
    x["def_days"] = days
    lo14, hi14 = x.l.rolling(14).min(), x.h.rolling(14).max()
    x["idx_rsv"] = (x.c - lo14) / (hi14 - lo14 + 1e-9) * 100
    x["idx_chg1"] = x.c.pct_change() * 100
    return x[["d", "defensive", "def_days", "idx_rsv", "idx_chg1"]]


def agent15_features(s: pd.DataFrame, i: int, atr_abs: float) -> dict:
    blank = {
        "a15_unknown": True,
        "f1_down_volume_dominance_20": False,
        "f2_bullish_bars_gravity_down_20": False,
        "f3_volume_without_progress_10": False,
        "f4_upthrust_failure_10": False,
        "f5_rebound_failure_10": False,
        "f1_down_up_volume_ratio": np.nan,
        "f2_bullish_bars": np.nan,
        "f2_gravity_atr": np.nan,
        "f3_peak_volume_ratio": np.nan,
        "f3_efficiency": np.nan,
        "f4_event_count": np.nan,
        "f5_rebound": np.nan,
        "f5_retrace": np.nan,
        "a15_red_count": 0,
        "a15_primary_reject": False,
        "a15_strict2_reject": False,
        "a15_any1_reject": False,
        "a15_decision": "unknown",
        "a15_reasons": "",
    }
    if i < 60 or not np.isfinite(atr_abs) or atr_abs <= 0:
        return blank
    win20 = s.iloc[i - 19:i + 1]
    returns20 = s.c.pct_change().iloc[i - 19:i + 1]
    if (win20.v <= 0).any() or returns20.isna().any():
        return blank
    up, down = returns20 > 0, returns20 < 0
    up_vol, down_vol = float(win20.loc[up.values, "v"].sum()), float(win20.loc[down.values, "v"].sum())
    ratio = down_vol / up_vol if up_vol > 0 else np.nan
    ret20 = float(s.c.iloc[i] / s.c.iloc[i - 20] - 1)
    f1 = bool(up.sum() >= 4 and down.sum() >= 4 and np.isfinite(ratio) and ratio >= 1.30 and ret20 <= 0)

    typical = (win20.h + win20.l + win20.c) / 3
    bull_count = int((win20.c > win20.o).sum())
    gravity = float((typical.iloc[-5:].median() - typical.iloc[:5].median()) / atr_abs)
    f2 = bool(bull_count >= 11 and gravity <= -0.75)

    recent10 = s.iloc[i - 9:i + 1]
    prior50 = s.iloc[i - 59:i - 9]
    peak_ratio = float(recent10.v.max() / prior50.v.median()) if prior50.v.median() > 0 else np.nan
    ret10 = float(s.c.iloc[i] / s.c.iloc[i - 10] - 1)
    path = s.c.pct_change().iloc[i - 9:i + 1].abs().sum()
    efficiency = float(abs(ret10) / path) if path > 0 else 0.0
    f3 = bool(np.isfinite(peak_ratio) and peak_ratio >= 1.80 and ret10 <= 0.02 and efficiency <= 0.25)

    upthrust_events = []
    for j in range(i - 9, i + 1):
        prev = s.iloc[j - 20:j]
        day = s.iloc[j]
        prior_high, prior_v = float(prev.h.max()), float(prev.v.median())
        day_range = float(day.h - day.l)
        upper = float(day.h - max(day.o, day.c)) / day_range if day_range > 0 else 0.0
        volume_ratio = float(day.v / prior_v) if prior_v > 0 else 0.0
        if day.h >= prior_high * 0.997 and day.c <= prior_high and upper >= 0.45 and volume_ratio >= 1.50 and s.c.iloc[i] <= day.c:
            upthrust_events.append(j)
    f4 = bool(upthrust_events)

    peak_j = int(s.h.iloc[i - 9:i + 1].idxmax())
    base = s.iloc[max(0, peak_j - 20):peak_j]
    peak = s.iloc[peak_j]
    if len(base) >= 10 and base.l.min() > 0 and base.v.median() > 0:
        rebound = float(peak.h / base.l.min() - 1)
        retrace = float(s.c.iloc[i] / peak.h - 1)
        peak_v_ratio = float(peak.v / base.v.median())
    else:
        rebound, retrace, peak_v_ratio = np.nan, np.nan, np.nan
    f5 = bool(np.isfinite(rebound) and rebound >= 0.08 and retrace <= -0.04 and peak_v_ratio >= 1.50)

    flags = [f1, f2, f3, f4, f5]
    names = ["F1", "F2", "F3", "F4", "F5"]
    red_count = int(sum(flags))
    primary = bool(f2 or f4 or red_count >= 2)
    return {
        "a15_unknown": False,
        "f1_down_volume_dominance_20": f1,
        "f2_bullish_bars_gravity_down_20": f2,
        "f3_volume_without_progress_10": f3,
        "f4_upthrust_failure_10": f4,
        "f5_rebound_failure_10": f5,
        "f1_down_up_volume_ratio": ratio,
        "f2_bullish_bars": bull_count,
        "f2_gravity_atr": gravity,
        "f3_peak_volume_ratio": peak_ratio,
        "f3_efficiency": efficiency,
        "f4_event_count": len(upthrust_events),
        "f5_rebound": rebound,
        "f5_retrace": retrace,
        "a15_red_count": red_count,
        "a15_primary_reject": primary,
        "a15_strict2_reject": red_count >= 2,
        "a15_any1_reject": red_count >= 1,
        "a15_decision": "reject" if primary else "keep",
        "a15_reasons": "+".join(name for name, flag in zip(names, flags) if flag),
    }


def label_event(s: pd.DataFrame, i: int) -> dict:
    if i + 1 >= len(s):
        return {"entry": np.nan, "outcome": "open", "hold_days": np.nan}
    entry = float(s.o.iloc[i + 1])
    stop, target = entry * 0.92, entry * 1.05
    if float(s.c.iloc[i + 1]) <= stop:
        return {"entry": entry, "outcome": "stop", "hold_days": 1}
    last = min(i + 1 + MAX_HOLD, len(s) - 1)
    for j in range(i + 2, last + 1):
        if float(s.l.iloc[j]) <= stop:
            return {"entry": entry, "outcome": "stop", "hold_days": j - (i + 1)}
        if float(s.h.iloc[j]) >= target:
            return {"entry": entry, "outcome": "win", "hold_days": j - (i + 1)}
    if i + 1 + MAX_HOLD <= len(s) - 1:
        return {"entry": entry, "outcome": "timeout", "hold_days": MAX_HOLD}
    return {"entry": entry, "outcome": "open", "hold_days": len(s) - 1 - (i + 1)}


def build_stock_signals(s: pd.DataFrame, idx: pd.DataFrame, meta: dict, effective_end: str) -> pd.DataFrame:
    s = s.sort_values("d").drop_duplicates("d").reset_index(drop=True).copy()
    c, o, h, l, v = s.c, s.o, s.h, s.l, s.v
    hi60, lo60 = h.rolling(60).max(), l.rolling(60).min()
    dd60 = (c / hi60 - 1) * 100
    pos60 = (c - lo60) / (hi60 - lo60 + 1e-9) * 100
    ret = c.pct_change()
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_abs = tr.rolling(14).mean()
    atr = atr_abs / c * 100
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    rsv = (c - lo14) / (hi14 - lo14 + 1e-9) * 100
    is_low = l <= lo60 * 1.001
    days_low = np.full(len(s), np.nan)
    last_low = None
    for i, flag in enumerate(is_low.fillna(False)):
        if flag:
            last_low = i
        if last_low is not None:
            days_low[i] = i - last_low
    downstk, run = np.zeros(len(s), dtype=int), 0
    for i, value in enumerate(ret.values):
        run = run + 1 if i > 0 and np.isfinite(value) and value < 0 else 0
        downstk[i] = run
    dd250 = (c / h.rolling(250).max() - 1) * 100
    volx = v / v.rolling(20).mean()
    imap = idx.set_index("d")
    out = []
    for i in range(59, len(s)):
        d = str(s.d.iloc[i])
        if d < REQUESTED_START or d > effective_end or d not in imap.index:
            continue
        if pd.isna(dd60.iloc[i]) or not (dd60.iloc[i] <= -20 and pos60.iloc[i] <= 25):
            continue
        defensive = bool(imap.at[d, "defensive"])
        hits = {
            "defensive": defensive,
            "above_ma10": bool(c.iloc[i] > ma10.iloc[i]),
            "dif_up": bool((dif.iloc[i] - dif.iloc[i - 3]) > 0),
            "rsv_recover": bool(20 < rsv.iloc[i] <= 40),
            "dd_sweet": bool(-45 < dd60.iloc[i] <= -30),
            "above_ma5": bool(c.iloc[i] > ma5.iloc[i]),
            "gap_reclaim": bool(o.iloc[i] < c.iloc[i - 1] * 0.98 and c.iloc[i] > o.iloc[i]),
            "rsv_deep": bool(rsv.iloc[i] <= 15),
            "downstk4": bool(downstk[i] >= 4),
            "zt20": bool((ret.iloc[max(0, i - 19):i + 1] >= 0.093).any()),
            "atr_hi": bool(atr.iloc[i] >= 7),
            "fresh_low": bool(np.isfinite(days_low[i]) and days_low[i] <= 1),
        }
        score = float(sum(W[k] for k, flag in hits.items() if flag))
        stock_score = score - (W["defensive"] if defensive else 0.0)
        path_ok = (defensive and score >= 18.0) or ((not defensive) and stock_score >= 15.0)
        if not path_ok or not np.isfinite(atr.iloc[i]) or atr.iloc[i] > 4.0:
            continue
        out.append({
            "code": str(meta["code"]).zfill(6),
            "name": meta["name"],
            "aggregate_eligible": bool(meta["aggregate_eligible"]),
            "forced_reason": meta.get("forced_reason", ""),
            "d": d,
            "bar_pos": i,
            "close": float(c.iloc[i]),
            "score": round(score, 1),
            "stock_score": round(stock_score, 1),
            "atr": float(atr.iloc[i]),
            "dd60": float(dd60.iloc[i]),
            "dd250": float(dd250.iloc[i]) if np.isfinite(dd250.iloc[i]) else np.nan,
            "pos60": float(pos60.iloc[i]),
            "rsv": float(rsv.iloc[i]),
            "volx": float(volx.iloc[i]) if np.isfinite(volx.iloc[i]) else np.nan,
            "defensive": defensive,
            "def_days": int(imap.at[d, "def_days"]),
            "idx_rsv": float(imap.at[d, "idx_rsv"]),
            "idx_chg1": float(imap.at[d, "idx_chg1"]),
            **label_event(s, i),
            **agent15_features(s, i, float(atr_abs.iloc[i])),
        })
    return pd.DataFrame(out)


def add_cooldown(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.sort_values(["code", "d"]).reset_index(drop=True).copy()
    raw["cooldown5"] = False
    for _, group in raw.groupby("code", sort=False):
        last_raw_pos = None
        for idx_, row in group.iterrows():
            pos = int(row.bar_pos)
            raw.at[idx_, "cooldown5"] = last_raw_pos is not None and pos - last_raw_pos <= COOLDOWN
            last_raw_pos = pos
    return raw


def wilson(k: int, n: int) -> list[float | None]:
    if n == 0:
        return [None, None]
    z, p = 1.959963984540054, k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [(center - half) * 100, (center + half) * 100]


def stats(df: pd.DataFrame) -> dict:
    mature = df[df.outcome != "open"]
    n = len(mature)
    counts = {key: int((df.outcome == key).sum()) for key in ("win", "stop", "timeout", "open")}
    wins, stops = counts["win"], counts["stop"]
    return {
        "n_total": len(df),
        "n_mature": n,
        **counts,
        "win_rate": wins / n * 100 if n else None,
        "stop_rate": stops / n * 100 if n else None,
        "timeout_rate": counts["timeout"] / n * 100 if n else None,
        "trigger_win_rate": wins / (wins + stops) * 100 if wins + stops else None,
        "ev": (wins * 5 - stops * 8) / n if n else None,
        "win_wilson95": wilson(wins, n),
        "stop_wilson95": wilson(stops, n),
    }


def comparison(df: pd.DataFrame, reject_col: str) -> dict:
    before = stats(df)
    kept = df[~df[reject_col].astype(bool)]
    removed = df[df[reject_col].astype(bool)]
    after = stats(kept)
    mature_base = max(1, before["n_mature"])
    return {
        "before": before,
        "after": after,
        "removed": stats(removed),
        "retained_mature_pct": after["n_mature"] / mature_base * 100,
        "removed_winner_share_pct": int((removed.outcome == "win").sum()) / max(1, before["win"]) * 100,
        "delta_win_rate_pp": None if before["win_rate"] is None or after["win_rate"] is None else after["win_rate"] - before["win_rate"],
        "delta_stop_rate_pp": None if before["stop_rate"] is None or after["stop_rate"] is None else after["stop_rate"] - before["stop_rate"],
        "delta_ev_pp": None if before["ev"] is None or after["ev"] is None else after["ev"] - before["ev"],
    }


def month_block_bootstrap(df: pd.DataFrame, reject_col: str, reps: int = 2000) -> dict:
    mature = df[df.outcome != "open"].copy()
    mature["month"] = mature.d.str[:7]
    months = sorted(mature.month.unique())
    if len(months) < 3:
        return {"reps": 0, "months": len(months), "delta_ev_ci95": [None, None], "delta_stop_rate_ci95": [None, None]}
    groups = {m: mature[mature.month == m] for m in months}
    rng = np.random.default_rng(20260911)
    ev_delta, stop_delta = [], []
    for _ in range(reps):
        sample = pd.concat([groups[months[i]] for i in rng.integers(0, len(months), len(months))], ignore_index=True)
        comp = comparison(sample, reject_col)
        if comp["delta_ev_pp"] is not None and comp["delta_stop_rate_pp"] is not None:
            ev_delta.append(comp["delta_ev_pp"])
            stop_delta.append(comp["delta_stop_rate_pp"])
    return {
        "reps": len(ev_delta),
        "months": len(months),
        "delta_ev_ci95": [float(x) for x in np.percentile(ev_delta, [2.5, 97.5])],
        "delta_stop_rate_ci95": [float(x) for x in np.percentile(stop_delta, [2.5, 97.5])],
    }


def build_shadow_features(allk: pd.DataFrame) -> pd.DataFrame:
    """Compute Agent 1.5 directly on every logged event, even if today's qfq
    reconstruction no longer reproduces the old engine gate exactly."""
    events = {(str(row.get("code", "")).zfill(6), str(row.get("T", ""))) for row in shadow_rows()}
    by_code = {str(code): group.sort_values("d").drop_duplicates("d").reset_index(drop=True)
               for code, group in allk.groupby("code", sort=False)}
    rows = []
    for code, d in sorted(events):
        s = by_code.get(code)
        if s is None:
            continue
        positions = s.index[s.d.eq(d)].tolist()
        if not positions:
            continue
        i = int(positions[0])
        tr = pd.concat([s.h - s.l, (s.h - s.c.shift()).abs(), (s.l - s.c.shift()).abs()], axis=1).max(axis=1)
        atr_abs = float(tr.rolling(14).mean().iloc[i])
        rows.append({"code": code, "d": d, "a15_feature_available": True,
                     **label_event(s, i), **agent15_features(s, i, atr_abs)})
    return pd.DataFrame(rows)


def build_shadow_audit(raw_all: pd.DataFrame, allk: pd.DataFrame) -> pd.DataFrame:
    rows = shadow_rows()
    if not rows:
        return pd.DataFrame()
    shadow = pd.DataFrame(rows)
    shadow["code"] = shadow.code.astype(str).str.zfill(6)
    shadow = shadow.rename(columns={"T": "d", "cooldown": "shadow_cooldown", "outcome": "logged_outcome",
                                    "days": "logged_days", "entry": "logged_entry"})
    if "judge" not in shadow:
        shadow["judge"] = ""
    shadow["judge"] = shadow.judge.fillna("").astype(str)
    features = build_shadow_features(allk)
    joined = shadow.merge(features, on=["code", "d"], how="left")
    engine_keys = raw_all[["code", "d"]].drop_duplicates().copy()
    engine_keys["engine_reproduction_match"] = True
    joined = joined.merge(engine_keys, on=["code", "d"], how="left")
    joined["engine_reproduction_match"] = joined.engine_reproduction_match.fillna(False).astype(bool)
    joined["a15_feature_available"] = joined.a15_feature_available.fillna(False).astype(bool)
    joined["logged_outcome_match"] = joined.logged_outcome.isna() | joined.outcome.isna() | joined.logged_outcome.eq(joined.outcome)
    return joined


def shadow_summary(audit: pd.DataFrame) -> dict:
    if audit.empty:
        return {}
    uncool = audit[~audit.shadow_cooldown.fillna(False).astype(bool)].copy()
    result = {
        "rows_all": len(audit),
        "rows_uncooled": len(uncool),
        "engine_reproduction_matches": int(audit.engine_reproduction_match.sum()),
        "a15_feature_available_all": int(audit.a15_feature_available.sum()),
        "a15_feature_available_uncooled": int(uncool.a15_feature_available.sum()),
        "logged_outcome_checked": int(audit.logged_outcome.notna().sum()),
        "logged_outcome_mismatches": int((audit.logged_outcome.notna() & ~audit.logged_outcome_match).sum()),
        "by_verdict": {},
    }
    for verdict in ("✓", "?", "✗", ""):
        group = uncool[uncool.judge == verdict]
        label = verdict or "blank"
        result["by_verdict"][label] = comparison(group[group.a15_feature_available], "a15_primary_reject")
    return result


def gate_decision(cool: pd.DataFrame, full_comp: dict, annual: dict, boot: dict) -> dict:
    base = full_comp["before"]
    checks = {
        "retained_mature_at_least_60pct": full_comp["retained_mature_pct"] >= 60,
        "stop_rate_improves_at_least_3pp": full_comp["delta_stop_rate_pp"] is not None and full_comp["delta_stop_rate_pp"] <= -3,
        "ev_improves_at_least_0p50pp": full_comp["delta_ev_pp"] is not None and full_comp["delta_ev_pp"] >= 0.50,
        "removed_winners_at_most_25pct": full_comp["removed_winner_share_pct"] <= 25,
    }
    nonnegative_ev_years, annual_stop_ok = 0, True
    for year, comp in annual.items():
        if comp["delta_ev_pp"] is not None and comp["delta_ev_pp"] >= 0:
            nonnegative_ev_years += 1
        if comp["delta_stop_rate_pp"] is not None and comp["delta_stop_rate_pp"] > 2:
            annual_stop_ok = False
    checks["at_least_two_years_nonnegative_ev_delta"] = nonnegative_ev_years >= 2
    checks["no_year_stop_rate_worse_over_2pp"] = annual_stop_ok
    checks["bootstrap_ev_lower_above_zero"] = boot["delta_ev_ci95"][0] is not None and boot["delta_ev_ci95"][0] > 0
    checks["bootstrap_stop_upper_below_zero"] = boot["delta_stop_rate_ci95"][1] is not None and boot["delta_stop_rate_ci95"][1] < 0
    passed = bool(all(checks.values()))
    return {
        "passed_all": passed,
        "decision": "eligible_for_forward_shadow_only" if passed else "insufficient_evidence_do_not_add_to_production",
        "checks": checks,
        "baseline_mature_n": base["n_mature"],
    }


def source_manifest() -> dict:
    paths = [ENGINE, SHADOW, PREREG, HERE / "research.py", HERE / "verify.py"]
    paths += [p for p in OUT.glob("*") if p.is_file() and p.name != "SOURCE_MANIFEST.json"]
    return {
        "schema": "bottom-agent15-source-manifest/v1",
        "created_at_cn": cn_now().isoformat(),
        "files": [{"path": str(p), "size": p.stat().st_size, "sha256": sha256(p)} for p in sorted(paths, key=lambda x: str(x))],
    }


def render_markdown(summary: dict) -> str:
    def pct(x):
        return "NA" if x is None else f"{x:.2f}%"

    lines = [
        "# Agent 1.5 价量否决层回测",
        "",
        f"- 数据有效截止：{summary['data_window']['effective_end']}（请求到 {REQUESTED_END}）",
        f"- 主股票池：{summary['data_window']['aggregate_universe_n']} 只当前成交额快照；不是点时点全 A 股票池。",
        f"- 生产建议：`{summary['go_no_go']['decision']}`。",
        "",
        "## 主规则结果",
        "",
        "|口径|状态|总N|win|stop|timeout|open|成熟胜率|成熟雷率|EV/笔|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("raw_qualified", "原始过线/未施加冷却"), ("cooldown5", "N=5 后推荐候选")):
        comp = summary["primary"][key]["full"]
        for state, item in (("无1.5", comp["before"]), ("加1.5后", comp["after"])):
            lines.append(
                f"|{label}|{state}|{item['n_total']}|{item['win']}|{item['stop']}|{item['timeout']}|{item['open']}|"
                f"{pct(item['win_rate'])}|{pct(item['stop_rate'])}|{pct(item['ev'])}|"
            )
    lines += [
        "",
        "### 主规则增量",
        "",
        "|口径|成熟 N|过滤后 N|保留率|胜率变化|雷率变化|EV变化|误杀赢家占比|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("raw_qualified", "原始过线/未施加冷却"), ("cooldown5", "N=5 后推荐候选")):
        comp = summary["primary"][key]["full"]
        lines.append(
            f"|{label}|{comp['before']['n_mature']}|{comp['after']['n_mature']}|{pct(comp['retained_mature_pct'])}|"
            f"{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{pct(comp['delta_ev_pp'])}|{pct(comp['removed_winner_share_pct'])}|"
        )
    lines += ["", "### N=5 分年", "", "|年份|基线N|过滤后N|胜率变化|雷率变化|EV变化|", "|---|---:|---:|---:|---:|---:|"]
    for year, comp in summary["primary"]["cooldown5"]["annual"].items():
        lines.append(f"|{year}|{comp['before']['n_mature']}|{comp['after']['n_mature']}|{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{pct(comp['delta_ev_pp'])}|")
    boot = summary["primary"]["cooldown5"]["bootstrap_month"]
    lines += [
        "",
        f"月度块自助 95%：EV 增量 `{boot['delta_ev_ci95']}`，雷率增量 `{boot['delta_stop_rate_ci95']}`。",
        "",
        "## 影子日志：未冷却行按 Agent② verdict",
        "",
        "|verdict|无1.5 W/S/T/O|加1.5 W/S/T/O|成熟N变化|胜率变化|雷率变化|EV变化|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for verdict, comp in summary["shadow"]["by_verdict"].items():
        b, a = comp["before"], comp["after"]
        lines.append(
            f"|{verdict}|{b['win']}/{b['stop']}/{b['timeout']}/{b['open']}|{a['win']}/{a['stop']}/{a['timeout']}/{a['open']}|"
            f"{b['n_mature']}→{a['n_mature']}|{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{pct(comp['delta_ev_pp'])}|"
        )
    lines += ["", "## 点名案例（预先固定）", "", "|日期|代码|名称|原 verdict|结果|F1–F5|1.5|", "|---|---|---|---|---|---|---|"]
    for row in summary["target_cases"]:
        flags = "+".join([f"F{i}" for i in range(1, 6) if row.get(f"f{i}")]) or "无"
        lines.append(f"|{row['d']}|{row['code']}|{row['name']}|{row['judge'] or 'blank'}|{row['outcome']}|{flags}|{row['a15_decision']}|")
    lines += [
        "",
        "## 解释边界",
        "",
        "这些规则只能描述日线价量异常，不能识别真实庄家、操纵意图、盘口撤单或席位行为。2026 点名案例已在冻结前暴露，影子 verdict 样本也很短；结果无论好坏都不能当成独立盲测。",
    ]
    return "\n".join(lines) + "\n"


def analyze() -> None:
    required = [OUT / "klines.csv.gz", OUT / "index_399006.csv.gz", OUT / "universe.csv", OUT / "fetch_audit.json"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"先运行 fetch，缺少 {missing}")
    audit = json.loads((OUT / "fetch_audit.json").read_text(encoding="utf-8"))
    if audit["engine_sha256"] != sha256(ENGINE) or audit["pre_registration_sha256"] != sha256(PREREG):
        raise RuntimeError("引擎或预注册在 fetch 后发生变化，拒绝分析；请另开版本")
    effective_end = audit["effective_end"]
    allk = pd.read_csv(OUT / "klines.csv.gz", dtype={"code": str, "d": str})
    idx = build_index(pd.read_csv(OUT / "index_399006.csv.gz", dtype={"d": str}))
    meta = pd.read_csv(OUT / "universe.csv", dtype={"code": str}).set_index("code")
    frames = []
    for k, (code, group) in enumerate(allk.groupby("code", sort=True)):
        if code not in meta.index:
            continue
        row = meta.loc[code].to_dict()
        row["code"] = code
        signals = build_stock_signals(group, idx, row, effective_end)
        if len(signals):
            frames.append(signals)
        if (k + 1) % 100 == 0:
            print(f"[panel] {k+1}/{allk.code.nunique()} signals={sum(len(x) for x in frames)}", flush=True)
    if not frames:
        raise RuntimeError("没有重建出引擎过线")
    raw_all = add_cooldown(pd.concat(frames, ignore_index=True))
    raw_all.to_csv(OUT / "signals_all_codes.csv.gz", index=False, compression="gzip")
    main_raw = raw_all[raw_all.aggregate_eligible].sort_values(["d", "code"]).reset_index(drop=True)
    main_cool = main_raw[~main_raw.cooldown5].copy().reset_index(drop=True)
    main_raw.to_csv(OUT / "signals_raw_qualified.csv.gz", index=False, compression="gzip")
    main_cool.to_csv(OUT / "signals_cooldown5.csv.gz", index=False, compression="gzip")

    shadow_audit = build_shadow_audit(raw_all, allk)
    shadow_audit.to_csv(OUT / "shadow_event_audit.csv", index=False, encoding="utf-8-sig")
    primary = {}
    for scope, frame in (("raw_qualified", main_raw), ("cooldown5", main_cool)):
        annual = {year: comparison(frame[frame.d.str.startswith(year)], "a15_primary_reject") for year in ("2024", "2025", "2026")}
        primary[scope] = {
            "full": comparison(frame, "a15_primary_reject"),
            "annual": annual,
            "bootstrap_month": month_block_bootstrap(frame, "a15_primary_reject"),
        }
    sensitivity = {
        name: comparison(main_cool, col)
        for name, col in (("strict_2plus", "a15_strict2_reject"), ("any_1plus", "a15_any1_reject"))
    }
    shadow = shadow_summary(shadow_audit)
    targets = []
    case_rows = shadow_audit[(~shadow_audit.shadow_cooldown.fillna(False).astype(bool)) & shadow_audit.code.isin(TARGETS) & shadow_audit.a15_feature_available]
    for _, row in case_rows.sort_values(["d", "code"]).iterrows():
        targets.append({
            "d": row.d, "code": row.code, "name": row.get("name", TARGETS.get(row.code, "")),
            "judge": row.judge, "outcome": row.outcome, "a15_decision": row.a15_decision,
            "a15_reasons": row.a15_reasons,
            **{f"f{i}": (bool(row[f"f{i}_{suffix}"]) if pd.notna(row[f"f{i}_{suffix}"]) else False) for i, suffix in enumerate([
                "down_volume_dominance_20", "bullish_bars_gravity_down_20", "volume_without_progress_10",
                "upthrust_failure_10", "rebound_failure_10"], 1)},
        })
    gate = gate_decision(main_cool, primary["cooldown5"]["full"], primary["cooldown5"]["annual"], primary["cooldown5"]["bootstrap_month"])
    summary = {
        "schema": "bottom-agent15-study/v1",
        "study_version": STUDY_VERSION,
        "generated_at_cn": cn_now().isoformat(),
        "data_window": {
            "requested": [REQUESTED_START, REQUESTED_END],
            "effective_end": effective_end,
            "aggregate_universe_n": audit["aggregate_universe_n"],
            "point_in_time_universe": False,
        },
        "engine": {
            "sha256": sha256(ENGINE),
            "raw_gate": "defensive total>=18 OR nondefensive stock>=15; ATR<=4",
            "cooldown": "N=5 stock bars; every raw qualified signal refreshes clock",
        },
        "agent15": {
            "pre_registration_sha256": sha256(PREREG),
            "primary": "F2 OR F4 OR red_count>=2; unknown keeps",
            "sensitivity_only": ["strict_2plus", "any_1plus"],
        },
        "label": "T+1 open; buy-day close<=-8 stop; then daily stop-first -8 vs +5 through 20 bars; timeout/open separate",
        "primary": primary,
        "sensitivity_cooldown5": sensitivity,
        "shadow": shadow,
        "target_cases": targets,
        "go_no_go": gate,
        "limitations": [
            "current top-by-amount universe is not point-in-time and has survivorship/liquidity-snapshot bias",
            "daily OHLCV proxies cannot identify a dealer, manipulation intent, order cancellations, or seats",
            "2026 named failures were known before freeze and are not a blind holdout",
            "shadow verdict history starts in 2026-07 and missing verdicts remain blank",
        ],
    }
    json_dump(OUT / "summary.json", summary)
    (OUT / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    json_dump(OUT / "SOURCE_MANIFEST.json", source_manifest())
    print(render_markdown(summary), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("fetch", "analyze", "all"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.command in ("fetch", "all"):
        fetch_data(args.refresh)
    if args.command in ("analyze", "all"):
        analyze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
