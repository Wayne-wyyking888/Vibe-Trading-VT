# -*- coding: utf-8 -*-
"""Agent 1.5 v0.3: shadow-only target calibration, then reverse-time validation."""
from __future__ import annotations

import csv
import gzip
import hashlib
import itertools
import json
import math
import pathlib
from functools import reduce
from operator import and_, or_

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
PROTOCOL = HERE / "V03_POSTHOC_PROTOCOL.md"
SIGNALS_ALL = OUT / "signals_all_codes.csv.gz"
KLINESS = OUT / "klines.csv.gz"
SHADOW_AUDIT = OUT / "shadow_event_audit.csv"
V02_SUMMARY = OUT / "v02_summary.json"
FAMILIES = ("F1", "F2", "F3", "F4", "F5")
GRID_COUNT = 523_864

VARIANTS = {
    "F1": {
        "F1_R070": (0.70,), "F1_R085": (0.85,), "F1_R100": (1.00,),
        "F1_R110": (1.10,), "F1_R130": (1.30,), "F1_R150": (1.50,), "F1_R180": (1.80,),
    },
    "F2": {
        "F2_B09_GPOS050": (9, 0.50), "F2_B10_GPOS050": (10, 0.50),
        "F2_B10_G000": (10, 0.00), "F2_B11_G000": (11, 0.00),
        "F2_B10_G040": (10, -0.40), "F2_B10_G075": (10, -0.75),
        "F2_B11_G025": (11, -0.25), "F2_B11_G040": (11, -0.40),
        "F2_B11_G075": (11, -0.75), "F2_B12_G075": (12, -0.75),
    },
    "F3": {
        "F3_V100_E060_R10": (1.00, 0.60, 0.10),
        "F3_V100_E035_R05": (1.00, 0.35, 0.05),
        "F3_V120_E035_R05": (1.20, 0.35, 0.05),
        "F3_V150_E035_R05": (1.50, 0.35, 0.05),
        "F3_V120_E025": (1.20, 0.25, 0.02), "F3_V150_E025": (1.50, 0.25, 0.02),
        "F3_V180_E020": (1.80, 0.20, 0.02), "F3_V180_E025": (1.80, 0.25, 0.02),
        "F3_V180_E035": (1.80, 0.35, 0.02), "F3_V220_E025": (2.20, 0.25, 0.02),
    },
    "F4": {
        "F4_W25_V100": (0.25, 1.00), "F4_W35_V100": (0.35, 1.00),
        "F4_W45_V100": (0.45, 1.00), "F4_W35_V120": (0.35, 1.20),
        "F4_W35_V150": (0.35, 1.50), "F4_W45_V120": (0.45, 1.20),
        "F4_W45_V150": (0.45, 1.50), "F4_W45_V200": (0.45, 2.00),
        "F4_W55_V150": (0.55, 1.50),
    },
    "F5": {
        "F5_R06_D01_V060": (0.06, -0.01, 0.60),
        "F5_R06_D01_V080": (0.06, -0.01, 0.80),
        "F5_R10_D01_V080": (0.10, -0.01, 0.80),
        "F5_R12_D01_V080": (0.12, -0.01, 0.80),
        "F5_R10_D02_V100": (0.10, -0.02, 1.00),
        "F5_R06_D02_V120": (0.06, -0.02, 1.20),
        "F5_R08_D02_V150": (0.08, -0.02, 1.50),
        "F5_R08_D04_V120": (0.08, -0.04, 1.20),
        "F5_R08_D04_V150": (0.08, -0.04, 1.50),
        "F5_R10_D04_V150": (0.10, -0.04, 1.50),
        "F5_R08_D06_V150": (0.08, -0.06, 1.50),
    },
}
VARIANT_NAMES = [name for family in FAMILIES for name in VARIANTS[family]]


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def dump(path: pathlib.Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def feature_row(s: pd.DataFrame, i: int, code: str, d: str) -> dict:
    result = {"code": code, "d": d, "v03_unknown": False}
    if i < 60:
        result["v03_unknown"] = True
        return result
    c, h, l, v = s.c, s.h, s.l, s.v
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_abs = float(tr.rolling(14).mean().iloc[i])
    if not np.isfinite(atr_abs) or atr_abs <= 0 or (v.iloc[i - 60:i + 1] <= 0).any():
        result["v03_unknown"] = True
        return result

    win20 = s.iloc[i - 19:i + 1]
    ret20_series = c.pct_change().iloc[i - 19:i + 1]
    up, down = ret20_series > 0, ret20_series < 0
    up_vol = float(win20.loc[up.values, "v"].sum())
    ratio = float(win20.loc[down.values, "v"].sum() / up_vol) if up_vol > 0 else np.nan
    ret20 = float(c.iloc[i] / c.iloc[i - 20] - 1)
    f1_base = bool(up.sum() >= 4 and down.sum() >= 4 and np.isfinite(ratio) and ret20 <= 0)
    result.update({"f1_ratio": ratio, "f1_ret20": ret20, "f1_up_days": int(up.sum()), "f1_down_days": int(down.sum())})
    for name, (cut,) in VARIANTS["F1"].items():
        result[name] = bool(f1_base and ratio >= cut)

    typical = (win20.h + win20.l + win20.c) / 3
    bulls = int((win20.c > win20.o).sum())
    gravity = float((typical.iloc[-5:].median() - typical.iloc[:5].median()) / atr_abs)
    result.update({"f2_bullish_bars": bulls, "f2_gravity_atr": gravity})
    for name, (bull_min, gravity_max) in VARIANTS["F2"].items():
        result[name] = bool(bulls >= bull_min and gravity <= gravity_max)

    recent10, prior50 = s.iloc[i - 9:i + 1], s.iloc[i - 59:i - 9]
    prior_median = float(prior50.v.median())
    peak_ratio = float(recent10.v.max() / prior_median) if prior_median > 0 else np.nan
    ret10 = float(c.iloc[i] / c.iloc[i - 10] - 1)
    path = float(c.pct_change().iloc[i - 9:i + 1].abs().sum())
    efficiency = abs(ret10) / path if path > 0 else 0.0
    result.update({"f3_peak_volume_ratio": peak_ratio, "f3_efficiency": efficiency, "f3_ret10": ret10})
    for name, (volume_min, efficiency_max, return_max) in VARIANTS["F3"].items():
        result[name] = bool(
            np.isfinite(peak_ratio) and peak_ratio >= volume_min
            and ret10 <= return_max and efficiency <= efficiency_max
        )

    for name, (wick_min, volume_min) in VARIANTS["F4"].items():
        hit = False
        for j in range(i - 9, i + 1):
            prev, day = s.iloc[j - 20:j], s.iloc[j]
            prior_high, prior_volume = float(prev.h.max()), float(prev.v.median())
            span = float(day.h - day.l)
            upper = float(day.h - max(day.o, day.c)) / span if span > 0 else 0.0
            vr = float(day.v / prior_volume) if prior_volume > 0 else 0.0
            if (
                day.h >= prior_high * 0.997 and day.c <= prior_high
                and upper >= wick_min and vr >= volume_min and c.iloc[i] <= day.c
            ):
                hit = True
                break
        result[name] = hit

    peak_j = int(h.iloc[i - 9:i + 1].idxmax())
    base, peak = s.iloc[max(0, peak_j - 20):peak_j], s.iloc[peak_j]
    if len(base) >= 10 and base.l.min() > 0 and base.v.median() > 0:
        rebound = float(peak.h / base.l.min() - 1)
        retrace = float(c.iloc[i] / peak.h - 1)
        peak_vr = float(peak.v / base.v.median())
    else:
        rebound, retrace, peak_vr = np.nan, np.nan, np.nan
    result.update({"f5_rebound": rebound, "f5_retrace": retrace, "f5_peak_volume_ratio": peak_vr})
    for name, (rebound_min, retrace_max, volume_min) in VARIANTS["F5"].items():
        result[name] = bool(
            np.isfinite(rebound) and rebound >= rebound_min
            and retrace <= retrace_max and peak_vr >= volume_min
        )
    return result


def build_features() -> pd.DataFrame:
    # Before the rule is selected, the global file contributes event keys only—not outcome labels.
    signals = pd.read_csv(SIGNALS_ALL, usecols=["code", "d"], dtype={"code": str, "d": str})
    shadow = pd.read_csv(SHADOW_AUDIT, usecols=["code", "d"], dtype={"code": str, "d": str})
    keys = pd.concat([signals[["code", "d"]], shadow[["code", "d"]]], ignore_index=True).drop_duplicates()
    needed = {code: set(group.d) for code, group in keys.groupby("code")}
    klines = pd.read_csv(KLINESS, dtype={"code": str, "d": str})
    rows = []
    for n, (code, raw) in enumerate(klines.groupby("code", sort=True), 1):
        if code not in needed:
            continue
        stock = raw.sort_values("d").drop_duplicates("d").reset_index(drop=True)
        positions = {d: i for i, d in enumerate(stock.d)}
        for d in sorted(needed[code]):
            if d in positions:
                rows.append(feature_row(stock, positions[d], code, d))
        if n % 100 == 0:
            print(f"[v03 features] {n}/{klines.code.nunique()} events={len(rows)}", flush=True)
    features = pd.DataFrame(rows)
    for name in VARIANT_NAMES:
        if name not in features:
            features[name] = False
        features[name] = features[name].fillna(False).astype(bool)
    features["v03_unknown"] = features.v03_unknown.fillna(True).astype(bool)
    features.to_csv(OUT / "v03_feature_matrix.csv.gz", index=False, compression="gzip")
    return features


def decision(frame: pd.DataFrame, config: dict) -> pd.Series:
    active = [config[family] for family in FAMILIES if config.get(family)]
    count = sum(
        (frame[name].fillna(False).astype(bool).astype(int) for name in active),
        start=pd.Series(0, index=frame.index),
    )
    return (count >= int(config["k"])) & ~frame.v03_unknown.fillna(True).astype(bool)


def one_stats(frame: pd.DataFrame) -> dict:
    mature = frame[frame.outcome != "open"]
    n = len(mature)
    w = int(mature.outcome.eq("win").sum())
    s = int(mature.outcome.eq("stop").sum())
    t = int(mature.outcome.eq("timeout").sum())
    return {
        "n_mature": n, "win": w, "stop": s, "timeout": t,
        "win_rate": w / n * 100 if n else None,
        "stop_rate": s / n * 100 if n else None,
        "ev": (w * 5 - s * 8) / n if n else None,
    }


def compare(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before = one_stats(frame)
    after = one_stats(frame.loc[~reject])
    removed = one_stats(frame.loc[reject])
    return {
        "before": before, "after": after, "removed": removed,
        "retained_mature_pct": after["n_mature"] / before["n_mature"] * 100 if before["n_mature"] else None,
        "removed_winner_share_pct": removed["win"] / before["win"] * 100 if before["win"] else None,
        "delta_win_rate_pp": after["win_rate"] - before["win_rate"],
        "delta_stop_rate_pp": after["stop_rate"] - before["stop_rate"],
        "delta_ev_pp": after["ev"] - before["ev"],
    }


def series_mask(values) -> int:
    mask = 0
    for i, value in enumerate(values):
        if bool(value):
            mask |= 1 << i
    return mask


def threshold_mask(masks: list[int], k: int) -> int:
    if k == 1:
        return reduce(or_, masks, 0)
    if k == len(masks):
        return reduce(and_, masks)
    result = 0
    for combo in itertools.combinations(masks, k):
        result |= reduce(and_, combo)
    return result


def bit_context(frame: pd.DataFrame, calibration: bool = False) -> dict:
    clean = frame.reset_index(drop=True)
    known = ~clean.v03_unknown.fillna(True).astype(bool)
    mature = clean.outcome.ne("open")
    context = {
        "n": len(clean),
        "known": series_mask(known),
        "mature": series_mask(mature),
        "win": series_mask(clean.outcome.eq("win")),
        "stop": series_mask(clean.outcome.eq("stop")),
        "timeout": series_mask(clean.outcome.eq("timeout")),
        "variant": {name: series_mask(clean[name].fillna(False).astype(bool) & known) for name in VARIANT_NAMES},
    }
    if calibration:
        context["check"] = series_mask(clean.judge.eq("✓"))
    else:
        context["years"] = {
            "2024": series_mask(clean.d.str.startswith("2024") & mature),
            "2025": series_mask(clean.d.str.startswith("2025") & mature),
            "2026pre": series_mask(clean.d.str.startswith("2026") & mature),
        }
    return context


def kept_counts(ctx: dict, reject: int, scope: int | None = None) -> tuple[int, int, int, int]:
    base = ctx["mature"] if scope is None else scope
    keep = base & ~reject
    return (
        keep.bit_count(), (keep & ctx["win"]).bit_count(),
        (keep & ctx["stop"]).bit_count(), (keep & ctx["timeout"]).bit_count(),
    )


def grid_search(calibration: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    """Select from shadow labels only; global outcome data is deliberately unavailable here."""
    cal_ctx = bit_context(calibration, calibration=True)
    check_mature = cal_ctx["check"] & cal_ctx["mature"]
    check_wins = cal_ctx["check"] & cal_ctx["win"]
    check_stops = cal_ctx["check"] & cal_ctx["stop"]
    cal_mature, cal_wins, cal_stops = cal_ctx["mature"], cal_ctx["win"], cal_ctx["stop"]

    fields = [
        "candidate_id", *FAMILIES, "k", "active_families",
        "check_stops_removed", "check_winners_removed", "all_stops_removed", "all_winners_removed",
        "check_mature_retained", "check_winners_retained", "check_stops_retained", "check_timeouts_retained",
        "all_mature_retained", "all_winners_retained", "all_stops_retained", "all_timeouts_retained",
        "check_ev_after", "eligible_guarded",
    ]
    eligible_rows = []
    target5_rows = []
    count = 0
    choices = [[None, *VARIANTS[family]] for family in FAMILIES]
    grid_path = OUT / "v03_grid_all.csv.gz"
    with gzip.open(grid_path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for selected in itertools.product(*choices):
            config = dict(zip(FAMILIES, selected))
            active = [name for name in selected if name]
            if not active:
                continue
            cal_masks = [cal_ctx["variant"][name] for name in active]
            for k in range(1, len(active) + 1):
                count += 1
                reject_cal = threshold_mask(cal_masks, k) & cal_ctx["known"]
                check_stops_removed = (reject_cal & check_stops).bit_count()
                check_winners_removed = (reject_cal & check_wins).bit_count()
                all_stops_removed = (reject_cal & cal_stops).bit_count()
                all_winners_removed = (reject_cal & cal_wins).bit_count()
                check_mature_retained = check_mature.bit_count() - (reject_cal & check_mature).bit_count()
                all_mature_retained = cal_mature.bit_count() - (reject_cal & cal_mature).bit_count()
                check_winners_retained = check_wins.bit_count() - check_winners_removed
                check_stops_retained = check_stops.bit_count() - check_stops_removed
                check_timeouts_retained = (
                    (check_mature & cal_ctx["timeout"] & ~reject_cal).bit_count()
                )
                all_winners_retained = cal_wins.bit_count() - all_winners_removed
                all_stops_retained = cal_stops.bit_count() - all_stops_removed
                all_timeouts_retained = (cal_mature & cal_ctx["timeout"] & ~reject_cal).bit_count()
                check_ev_after = (
                    (check_winners_retained * 5 - check_stops_retained * 8) / check_mature_retained
                    if check_mature_retained else -math.inf
                )
                row = {
                    "candidate_id": ";".join(f"{family}={config[family] or '-'}" for family in FAMILIES) + f";k={k}",
                    **config, "k": k, "active_families": len(active),
                    "check_stops_removed": check_stops_removed,
                    "check_winners_removed": check_winners_removed,
                    "all_stops_removed": all_stops_removed,
                    "all_winners_removed": all_winners_removed,
                    "check_mature_retained": check_mature_retained,
                    "check_winners_retained": check_winners_retained,
                    "check_stops_retained": check_stops_retained,
                    "check_timeouts_retained": check_timeouts_retained,
                    "all_mature_retained": all_mature_retained,
                    "all_winners_retained": all_winners_retained,
                    "all_stops_retained": all_stops_retained,
                    "all_timeouts_retained": all_timeouts_retained,
                    "check_ev_after": check_ev_after,
                }
                eligible = bool(
                    check_mature_retained >= math.ceil(check_mature.bit_count() * 0.60)
                    and check_winners_removed <= 5
                    and all_mature_retained >= math.ceil(cal_mature.bit_count() * 0.50)
                    and all_winners_retained >= math.ceil(cal_wins.bit_count() * 0.60)
                )
                row["eligible_guarded"] = eligible
                writer.writerow(row)
                if eligible:
                    eligible_rows.append(row)
                if check_stops_removed == 5:
                    target5_rows.append(row)
            if count and count % 100_000 < len(active):
                print(f"[v03 grid] candidates={count} eligible={len(eligible_rows)}", flush=True)
    if count != GRID_COUNT:
        raise AssertionError(f"grid count {count} != {GRID_COUNT}")
    eligible_frame = pd.DataFrame(eligible_rows)
    if eligible_frame.empty:
        raise RuntimeError("no guarded eligible v0.3 candidate")
    ordered = eligible_frame.sort_values(
        ["check_stops_removed", "check_winners_removed", "all_stops_removed", "all_winners_removed",
         "check_ev_after", "active_families", "k", "candidate_id"],
        ascending=[False, True, False, True, False, True, False, True],
    ).reset_index(drop=True)
    ordered.head(200).to_csv(OUT / "v03_leaderboard.csv", index=False, encoding="utf-8-sig")
    target5 = pd.DataFrame(target5_rows)
    target5_ordered = target5.sort_values(
        ["check_winners_removed", "all_stops_removed", "all_winners_removed", "check_ev_after",
         "active_families", "k", "candidate_id"],
        ascending=[True, False, True, False, True, False, True],
    ).reset_index(drop=True)
    best5 = target5_ordered.iloc[0]
    target5_diagnostics = {
        "candidates": len(target5),
        "eligible_guarded": int(target5.eligible_guarded.sum()),
        "min_check_winners_removed": int(target5.check_winners_removed.min()),
        "max_check_winners_retained": int(target5.check_winners_retained.max()),
        "allowed_check_winners_removed": 5,
        "min_all_winners_removed": int(target5.all_winners_removed.min()),
        "max_all_winners_retained": int(target5.all_winners_retained.max()),
        "required_all_winners_retained": math.ceil(cal_wins.bit_count() * 0.60),
        "max_all_mature_retained": int(target5.all_mature_retained.max()),
        "required_all_mature_retained": math.ceil(cal_mature.bit_count() * 0.50),
        "best_unguarded_candidate": {
            "candidate_id": str(best5["candidate_id"]),
            **{family: (None if pd.isna(best5[family]) else str(best5[family])) for family in FAMILIES},
            "k": int(best5["k"]),
            "active_families": int(best5["active_families"]),
            "check_winners_removed": int(best5["check_winners_removed"]),
            "check_mature_retained": int(best5["check_mature_retained"]),
            "all_stops_removed": int(best5["all_stops_removed"]),
            "all_winners_removed": int(best5["all_winners_removed"]),
            "all_mature_retained": int(best5["all_mature_retained"]),
        },
    }
    return eligible_frame, ordered.iloc[0].to_dict(), target5_diagnostics


def month_bootstrap(frame: pd.DataFrame, config: dict, reps: int = 2000) -> dict:
    mature = frame[frame.outcome != "open"].copy()
    mature["month"] = mature.d.str[:7]
    months = sorted(mature.month.unique())
    groups = {month: mature[mature.month == month] for month in months}
    rng = np.random.default_rng(20260911)
    values = []
    for _ in range(reps):
        sample = pd.concat([groups[months[i]] for i in rng.integers(0, len(months), len(months))], ignore_index=True)
        comp = compare(sample, decision(sample, config))
        values.append((comp["delta_stop_rate_pp"], comp["delta_win_rate_pp"], comp["delta_ev_pp"]))
    arr = np.asarray(values)
    return {
        "reps": reps, "months": len(months),
        "selection_contaminated": False,
        "reverse_time_validation": True,
        "delta_stop_rate_ci95": np.percentile(arr[:, 0], [2.5, 97.5]).tolist(),
        "delta_win_rate_ci95": np.percentile(arr[:, 1], [2.5, 97.5]).tolist(),
        "delta_ev_ci95": np.percentile(arr[:, 2], [2.5, 97.5]).tolist(),
    }


def validation_gate(global_results: dict, stops_filtered: int) -> dict:
    total = global_results["pre_shadow"]
    boot = global_results["pre_shadow_month_bootstrap"]
    checks = {
        "five_check_stops_filtered": stops_filtered == 5,
        "aggregate_mature_retention_ge_60pct": total["retained_mature_pct"] >= 60.0,
        "aggregate_winner_removal_le_25pct": total["removed_winner_share_pct"] <= 25.0,
        "aggregate_stop_delta_le_minus_3pp": total["delta_stop_rate_pp"] <= -3.0,
        "aggregate_win_delta_ge_plus_1pp": total["delta_win_rate_pp"] >= 1.0,
        "aggregate_ev_delta_ge_plus_0_5pp": total["delta_ev_pp"] >= 0.5,
        "bootstrap_stop_delta_upper_lt_0": boot["delta_stop_rate_ci95"][1] < 0.0,
        "bootstrap_ev_delta_lower_gt_0": boot["delta_ev_ci95"][0] > 0.0,
    }
    for key, label in (("year_2024", "2024"), ("year_2025", "2025"), ("year_2026_pre", "2026pre")):
        checks[f"{label}_stop_not_worse"] = global_results[key]["delta_stop_rate_pp"] <= 0.0
        checks[f"{label}_mature_retention_ge_50pct"] = global_results[key]["retained_mature_pct"] >= 50.0
    passed = all(checks.values())
    return {
        "checks": checks,
        "all_pass": passed,
        "decision": (
            "eligible_for_new_forward_shadow_not_production"
            if passed else "fails_v03_historical_validation_research_only"
        ),
        "next_gate_if_passed": "at_least_30_new_mature_n5_prospective_shadow_candidates",
    }


def render(summary: dict) -> str:
    def pct(x):
        return "NA" if x is None else f"{x:+.3f}pp"

    selected = summary["selected"]
    target5 = summary["grid"]["target5_feasibility"]
    lines = [
        "# Agent 1.5 v0.3：Shadow校准与逆时间验证",
        "",
        "**规则只由2026-07-14以后61笔成熟shadow裁定选择；2024—2026-07-12在规则冻结后验证。**",
        "扩展阈值受后来五个已知案例启发，因此这是逆时间验证，不是 prospective OOS 或生产证明。",
        "",
        f"- 网格：{summary['grid']['candidates']:,}；通过反全拒绝约束：{summary['grid']['eligible_guarded']:,}。",
        f"- 唯一选择：`{selected['candidate_id']}`。",
        f"- ✓雷过滤：{summary['target_cases']['stops_filtered']}/5。",
        f"- 未加门禁时有 {target5['candidates']:,} 个5/5组合；通过门禁为 {target5['eligible_guarded']} 个。",
        f"- 所有5/5组合最少误杀✓赢家 {target5['min_check_winners_removed']}（容忍上限"
        f" {target5['allowed_check_winners_removed']}）；最多保留全部裁定赢家"
        f" {target5['max_all_winners_retained']}/{target5['required_all_winners_retained']}。",
        f"- 验证裁定：`{summary['go_no_go']['decision']}`。",
        "",
        "## Shadow成熟裁定",
        "",
        "|范围|过滤前W/S/T|过滤后W/S/T|胜率变化|雷率变化|误杀赢家|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("check", "✓"), ("uncertain", "?"), ("reject", "✗"), ("all", "全部")):
        comp = summary["shadow"][key]
        b, a = comp["before"], comp["after"]
        lines.append(
            f"|{label}|{b['win']}/{b['stop']}/{b['timeout']}|{a['win']}/{a['stop']}/{a['timeout']}|"
            f"{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{comp['removed_winner_share_pct']:.2f}%|"
        )
    lines += [
        "",
        "## N=5逆时间验证（未参与选参）",
        "",
        "|范围|成熟N→保留N|胜率变化|雷率变化|EV变化|误杀赢家|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("year_2024", "2024"), ("year_2025", "2025"),
                       ("year_2026_pre", "2026至07-12"), ("pre_shadow", "合计至07-12"),
                       ("cooldown_full", "全期至09-09")):
        comp = summary["global"][key]
        lines.append(
            f"|{label}|{comp['before']['n_mature']}→{comp['after']['n_mature']}|"
            f"{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|"
            f"{pct(comp['delta_ev_pp'])}|{comp['removed_winner_share_pct']:.2f}%|"
        )
    boot = summary["global"]["pre_shadow_month_bootstrap"]
    lines += [
        "",
        f"验证集月份块bootstrap：雷率增量95% `{boot['delta_stop_rate_ci95']}`；"
        f"胜率增量95% `{boot['delta_win_rate_ci95']}`；EV增量95% `{boot['delta_ev_ci95']}`。",
        "",
        "## 冻结门禁",
        "",
    ]
    for name, passed in summary["go_no_go"]["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
    lines += [
        "",
        "即使全部门禁通过，也只能进入不少于30笔新增成熟N=5候选的前瞻shadow，不能直接转生产。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    features = build_features()
    shadow = pd.read_csv(SHADOW_AUDIT, dtype={"code": str, "d": str})
    shadow_cool = shadow.shadow_cooldown.fillna(False).astype(bool)
    calibration = shadow[
        (~shadow_cool) & shadow.judge.isin(["✓", "?", "✗"])
        & shadow.outcome.isin(["win", "stop", "timeout"])
    ].copy().merge(features, on=["code", "d"], how="inner", validate="one_to_one")
    if len(calibration) != 61 or len(calibration[calibration.judge == "✓"]) != 25:
        raise AssertionError("unexpected v0.3 shadow calibration size")

    # Freeze the winner before reading any global outcome column.
    eligible_grid, selected_row, target5_diagnostics = grid_search(calibration)
    config = {family: selected_row[family] if pd.notna(selected_row[family]) else None for family in FAMILIES}
    config.update({"k": int(selected_row["k"]), "candidate_id": str(selected_row["candidate_id"]), "rank": 1})

    signals = pd.read_csv(SIGNALS_ALL, dtype={"code": str, "d": str})
    enriched = signals.merge(features, on=["code", "d"], how="left", validate="one_to_one")
    enriched[VARIANT_NAMES] = enriched[VARIANT_NAMES].fillna(False).astype(bool)
    enriched["v03_unknown"] = enriched.v03_unknown.fillna(True).astype(bool)
    main = enriched[enriched.aggregate_eligible.fillna(False).astype(bool)].copy()
    raw = main.copy()
    cool = main[~main.cooldown5.fillna(False).astype(bool)].copy()
    pre = cool[cool.d < "2026-07-13"].copy()
    if len(pre[pre.outcome != "open"]) != 1770:
        raise AssertionError("unexpected pre-shadow mature size")

    cal_reject = decision(calibration, config)
    calibration["v03_trigger_count"] = sum(
        (calibration[config[family]].astype(int) for family in FAMILIES if config[family]),
        start=pd.Series(0, index=calibration.index),
    )
    calibration["v03_reject"] = cal_reject
    calibration["v03_keep"] = ~cal_reject
    calibration["v03_candidate_id"] = config["candidate_id"]
    calibration.to_csv(OUT / "v03_shadow_row_audit.csv", index=False, encoding="utf-8-sig")

    check_stops = calibration[(calibration.judge == "✓") & (calibration.outcome == "stop")].copy()
    check_stop_reject = decision(check_stops, config)
    case_rows = []
    for idx, row in check_stops.iterrows():
        hits = [config[f] for f in FAMILIES if config[f] and bool(row[config[f]])]
        case_rows.append({
            "d": row.d, "code": row.code, "name": row["name"],
            "filtered": bool(check_stop_reject.loc[idx]), "variant_hits": hits,
        })

    scopes = {
        "raw_full": raw,
        "cooldown_full": cool,
        "pre_shadow": pre,
        "year_2024": pre[pre.d.str.startswith("2024")],
        "year_2025": pre[pre.d.str.startswith("2025")],
        "year_2026_pre": pre[pre.d.str.startswith("2026")],
        "year_2026_full": cool[cool.d.str.startswith("2026")],
    }
    global_results = {key: compare(frame, decision(frame, config)) for key, frame in scopes.items()}
    global_results["pre_shadow_month_bootstrap"] = month_bootstrap(pre, config)
    shadow_results = {
        "check": compare(calibration[calibration.judge == "✓"], decision(calibration[calibration.judge == "✓"], config)),
        "uncertain": compare(calibration[calibration.judge == "?"], decision(calibration[calibration.judge == "?"], config)),
        "reject": compare(calibration[calibration.judge == "✗"], decision(calibration[calibration.judge == "✗"], config)),
        "all": compare(calibration, cal_reject),
    }
    gate = validation_gate(global_results, int(check_stop_reject.sum()))
    old = json.loads(V02_SUMMARY.read_text(encoding="utf-8"))
    summary = {
        "schema": "bottom-agent15-v03-posthoc-grid/v1",
        "status": "posthoc_target_calibration_then_reverse_time_validation",
        "protocol_sha256": sha256(PROTOCOL),
        "source_hashes": {str(path): sha256(path) for path in (SIGNALS_ALL, KLINESS, SHADOW_AUDIT, HERE / "v03_grid_search.py")},
        "grid": {
            "candidates": GRID_COUNT,
            "eligible_guarded": len(eligible_grid),
            "global_pre_shadow_used_for_selection": False,
            "ranking": "check stops removed max; check winners removed min; all stops removed max; all winners removed min; check EV max; active families min; k max; id lexical",
            "target5_feasibility": target5_diagnostics,
        },
        "selected": config,
        "target_cases": {"stops_total": 5, "stops_filtered": int(check_stop_reject.sum()), "rows": case_rows},
        "shadow": shadow_results,
        "global": global_results,
        "v02_benchmark": {
            "selected": old["selected"],
            "pre_shadow": old["global"]["cooldown_pre_shadow"],
            "decision": old["go_no_go"]["decision"],
        },
        "go_no_go": gate,
        "decision": gate["decision"],
        "limitations": [
            "five known check-mark stops explicitly shaped the expanded variants",
            "2024 through 2026-07-12 outcomes were opened only after the shadow-selected rule was frozen",
            "reverse-time validation is not a prospective out-of-sample test",
            "current amount-ranked universe is not point-in-time and has survivorship bias",
            "523,864 candidates create extreme multiple-testing risk",
        ],
    }
    dump(OUT / "v03_selected_candidate.json", {"selected": config, "protocol_sha256": summary["protocol_sha256"]})
    dump(OUT / "v03_summary.json", summary)
    (OUT / "v03_summary.md").write_text(render(summary), encoding="utf-8")
    print(render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
