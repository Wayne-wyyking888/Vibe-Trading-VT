# -*- coding: utf-8 -*-
"""Agent 1.5 v0.4: small, newly specified risk-rule study on N=5 candidates."""
from __future__ import annotations

import hashlib
import itertools
import json
import pathlib

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
PROTOCOL = HERE / "V04_PRE_REGISTRATION.md"
SIGNALS = OUT / "signals_cooldown5.csv.gz"
KLINES = OUT / "klines.csv.gz"
INDEX = OUT / "index_399006.csv.gz"
SHADOW = OUT / "shadow_event_audit.csv"

EXPECTED_HASHES = {
    SIGNALS: "9565EE0205DAC02D6421EF4594FFCAF6047EAAA94F35B6CF8E03D83B36B75ADC",
    KLINES: "E8B5881A1148DCE6E4BD808054F8353A055A11483BBCDA9993967F155019CE98",
    INDEX: "A6516F7AA07793DB6855559B0470A1CEF31ADFF853ACCDD3F0CBA32D210CC283",
}

BOOK_FLAGS = ("B1", "B2", "B3", "B4", "B5", "B6")
EXEMPTIONS = ("E1", "E2")
DEV_END = "2026-06-10"
EMBARGO_START = "2026-06-11"
EMBARGO_END = "2026-07-10"
AUDIT_START = "2026-07-13"
BOOTSTRAP_REPS = 5_000
RNG_SEED = 20260911

FOLDS = {
    "2024H1": ("2024-01-01", "2024-06-30"),
    "2024H2": ("2024-07-01", "2024-12-31"),
    "2025H1": ("2025-01-01", "2025-06-30"),
    "2025H2": ("2025-07-01", "2025-12-31"),
    "2026DEV": ("2026-01-01", DEV_END),
}

TARGET_CASES = {
    ("2026-08-07", "300450"),
    ("2026-08-12", "000066"),
    ("2026-08-26", "300124"),
    ("2026-08-27", "300450"),
    ("2026-08-28", "002709"),
}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def dump(path: pathlib.Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_inputs() -> dict[str, str]:
    found = {str(path): sha256(path) for path in EXPECTED_HASHES}
    errors = [
        f"{path}: expected={expected} found={found[str(path)]}"
        for path, expected in EXPECTED_HASHES.items()
        if found[str(path)] != expected
    ]
    if errors:
        raise RuntimeError("frozen input hash mismatch\n" + "\n".join(errors))
    return found


def feature_row(stock: pd.DataFrame, i: int, idx_ret20: dict[str, float]) -> dict:
    row = stock.iloc[i]
    code, date = str(row.code).zfill(6), str(row.d)
    result = {"code": code, "d": date, "v04_unknown": False}
    if i < 120 or date not in idx_ret20 or not np.isfinite(idx_ret20[date]):
        result["v04_unknown"] = True
        return result

    sample = stock.iloc[i - 120:i + 1]
    if (sample.v <= 0).any():
        result["v04_unknown"] = True
        return result

    o, h, l, c, v = stock.o, stock.h, stock.l, stock.c, stock.v
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_abs = float(tr.rolling(14).mean().iloc[i])
    if not np.isfinite(atr_abs) or atr_abs <= 0:
        result["v04_unknown"] = True
        return result

    # B1: a compact platform breaks on volume and remains unreclaimed at T.
    platform_break_count = 0
    platform_widths = []
    platform_break_volume_ratios = []
    for j in range(i - 9, i + 1):
        prior = stock.iloc[j - 20:j]
        support = float(prior.l.min())
        width = float((prior.h.max() - prior.l.min()) / prior.c.median())
        volume_ratio = float(v.iloc[j] / prior.v.median())
        if width <= 0.15 and c.iloc[j] <= support * 0.99 and volume_ratio >= 1.20 and c.iloc[i] <= support:
            platform_break_count += 1
            platform_widths.append(width)
            platform_break_volume_ratios.append(volume_ratio)
    b1 = platform_break_count > 0

    # B2: upthrust/lightning-rod event whose event close has not been recovered.
    upthrust_count = 0
    upthrust_wicks = []
    upthrust_volume_ratios = []
    for j in range(i - 9, i + 1):
        prior = stock.iloc[j - 20:j]
        day_range = float(h.iloc[j] - l.iloc[j])
        upper_wick = float(h.iloc[j] - max(o.iloc[j], c.iloc[j])) / day_range if day_range > 0 else 0.0
        prior_high = float(prior.h.max())
        volume_ratio = float(v.iloc[j] / prior.v.median())
        if (
            h.iloc[j] >= prior_high * 0.997
            and c.iloc[j] <= prior_high
            and upper_wick >= 0.45
            and volume_ratio >= 1.50
            and c.iloc[i] <= c.iloc[j]
        ):
            upthrust_count += 1
            upthrust_wicks.append(upper_wick)
            upthrust_volume_ratios.append(volume_ratio)
    b2 = upthrust_count > 0

    # Shared 20-day volume-at-price primitives for B4.
    win20 = stock.iloc[i - 19:i + 1]
    typical20 = (win20.h + win20.l + win20.c) / 3.0
    v_sum = float(win20.v.sum())
    vwtp20 = float((typical20 * win20.v).sum() / v_sum)
    vwtp_gap_atr = float((c.iloc[i] - vwtp20) / atr_abs)
    overhead_share = float(win20.loc[typical20 > c.iloc[i], "v"].sum() / v_sum)

    # B3: several volume-backed bearish bars plus a fresh MA5/MA10 death cross.
    prior20 = stock.iloc[i - 24:i - 4]
    recent5 = stock.iloc[i - 4:i + 1]
    ret5 = float(c.iloc[i] / c.iloc[i - 5] - 1.0)
    bearish = recent5.c < recent5.o
    bearish_count = int(bearish.sum())
    bearish_volume_ratio = (
        float(recent5.loc[bearish, "v"].median() / prior20.v.median()) if bearish_count else np.nan
    )
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    death_cross_count = sum(
        bool(ma5.iloc[j - 1] >= ma10.iloc[j - 1] and ma5.iloc[j] < ma10.iloc[j])
        for j in range(i - 4, i + 1)
    )
    b3 = bool(
        ret5 <= -0.06
        and bearish_count >= 2
        and np.isfinite(bearish_volume_ratio)
        and bearish_volume_ratio >= 1.20
        and death_cross_count > 0
        and ma5.iloc[i] < ma10.iloc[i]
    )

    # B4: prior markup, subsequent deep drawdown, overhead supply and lower structure.
    peak_j = int(h.iloc[i - 119:i + 1].idxmax())
    peak_price = float(h.iloc[peak_j])
    pre_peak = stock.iloc[i - 119:peak_j + 1]
    prior_trough = float(pre_peak.l.min())
    markup120 = float(peak_price / prior_trough - 1.0)
    drawdown_from_peak = float(c.iloc[i] / peak_price - 1.0)
    previous10 = stock.iloc[i - 19:i - 9]
    recent10 = stock.iloc[i - 9:i + 1]
    high_ratio = float(recent10.h.max() / previous10.h.max())
    low_ratio = float(recent10.l.min() / previous10.l.min())
    b4 = bool(
        markup120 >= 0.30
        and drawdown_from_peak <= -0.20
        and vwtp_gap_atr <= -0.75
        and overhead_share >= 0.65
        and high_ratio <= 0.99
        and low_ratio <= 0.99
    )

    # B5: an index-driven washout should not leave persistent extreme relative weakness.
    ret20 = float(c.iloc[i] / c.iloc[i - 20] - 1.0)
    relative20 = ret20 - float(idx_ret20[date])
    b5 = bool(relative20 <= -0.08 and ret20 <= 0.0)

    # B6: repeated high opens that finish bearish on volume and remain unrecovered.
    failed_high_open_closes = []
    failed_high_open_volume_ratios = []
    for j in range(i - 9, i + 1):
        prior = stock.iloc[j - 20:j]
        gap = float(o.iloc[j] / c.iloc[j - 1] - 1.0)
        day_range = float(h.iloc[j] - l.iloc[j])
        upper_wick = float(h.iloc[j] - max(o.iloc[j], c.iloc[j])) / day_range if day_range > 0 else 0.0
        volume_ratio = float(v.iloc[j] / prior.v.median())
        if (
            gap >= 0.015
            and c.iloc[j] < o.iloc[j]
            and c.iloc[j] <= c.iloc[j - 1]
            and volume_ratio >= 1.20
            and upper_wick >= 0.30
        ):
            failed_high_open_closes.append(float(c.iloc[j]))
            failed_high_open_volume_ratios.append(volume_ratio)
    failed_high_open_count = len(failed_high_open_closes)
    b6 = bool(
        failed_high_open_count >= 2
        and c.iloc[i] <= float(np.median(failed_high_open_closes))
    )

    # E1/E2: recovered bearish traps or recovered lower probes force fail-open.
    e1_count = 0
    e2_count = 0
    for j in range(i - 9, i + 1):
        prior = stock.iloc[j - 20:j]
        support = float(prior.l.min())
        day_range = float(h.iloc[j] - l.iloc[j])
        lower_wick = float(min(o.iloc[j], c.iloc[j]) - l.iloc[j]) / day_range if day_range > 0 else 0.0
        volume_ratio = float(v.iloc[j] / prior.v.median())
        if l.iloc[j] <= support * 0.997 and c.iloc[i] >= support * 1.005 and c.iloc[i] >= c.iloc[j]:
            e1_count += 1
        if (
            l.iloc[j] <= support * 1.003
            and lower_wick >= 0.45
            and volume_ratio >= 1.20
            and c.iloc[i] >= c.iloc[j]
        ):
            e2_count += 1
    e1 = e1_count > 0
    e2 = e2_count > 0

    result.update({
        "B1": bool(b1), "B2": bool(b2), "B3": bool(b3),
        "B4": bool(b4), "B5": bool(b5), "B6": bool(b6),
        "E1": bool(e1), "E2": bool(e2),
        "b1_platform_break_count": platform_break_count,
        "b1_min_platform_width": min(platform_widths) if platform_widths else np.nan,
        "b1_max_break_volume_ratio": max(platform_break_volume_ratios) if platform_break_volume_ratios else np.nan,
        "b2_upthrust_count": upthrust_count,
        "b2_max_upper_wick": max(upthrust_wicks) if upthrust_wicks else np.nan,
        "b2_max_volume_ratio": max(upthrust_volume_ratios) if upthrust_volume_ratios else np.nan,
        "b3_ret5": ret5,
        "b3_bearish_count": bearish_count,
        "b3_bearish_volume_ratio": bearish_volume_ratio,
        "b3_death_cross_count": death_cross_count,
        "b4_markup120": markup120,
        "b4_drawdown_from_peak": drawdown_from_peak,
        "b4_vwtp_gap_atr": vwtp_gap_atr,
        "b4_overhead_volume_share": overhead_share,
        "b4_high_ratio": high_ratio,
        "b4_low_ratio": low_ratio,
        "b5_stock_ret20": ret20,
        "b5_index_ret20": float(idx_ret20[date]),
        "b5_relative20": relative20,
        "b6_failed_high_open_count": failed_high_open_count,
        "b6_max_volume_ratio": max(failed_high_open_volume_ratios) if failed_high_open_volume_ratios else np.nan,
        "e1_recovered_trap_count": e1_count,
        "e2_recovered_probe_count": e2_count,
    })
    return result


def build_features(signals: pd.DataFrame) -> pd.DataFrame:
    index = pd.read_csv(INDEX, dtype={"d": str}).sort_values("d").drop_duplicates("d")
    index["ret20"] = index.c / index.c.shift(20) - 1.0
    idx_ret20 = dict(zip(index.d, index.ret20))

    keys = {code: set(group.d) for code, group in signals.groupby("code")}
    klines = pd.read_csv(KLINES, dtype={"code": str, "d": str})
    rows = []
    total = int(klines.code.nunique())
    for n, (code, raw) in enumerate(klines.groupby("code", sort=True), 1):
        code = str(code).zfill(6)
        if code not in keys:
            continue
        stock = raw.sort_values("d").drop_duplicates("d").reset_index(drop=True)
        stock["code"] = code
        positions = {date: i for i, date in enumerate(stock.d)}
        for date in sorted(keys[code]):
            if date in positions:
                rows.append(feature_row(stock, positions[date], idx_ret20))
        if n % 100 == 0:
            print(f"[v04 features] {n}/{total} rows={len(rows)}", flush=True)

    features = pd.DataFrame(rows)
    for flag in (*BOOK_FLAGS, *EXEMPTIONS):
        if flag not in features:
            features[flag] = False
        features[flag] = features[flag].fillna(False).astype(bool)
    features["v04_unknown"] = features.v04_unknown.fillna(True).astype(bool)
    if features.duplicated(["code", "d"]).any():
        raise AssertionError("duplicate v0.4 feature keys")
    features.to_csv(OUT / "v04_feature_matrix.csv.gz", index=False, compression="gzip")
    return features


def candidates() -> list[dict]:
    rows = [{"candidate_id": "KEEP_ALL", "kind": "null", "members": [], "complexity": 0}]
    rows.extend(
        {
            "candidate_id": f"{left}_AND_{right}",
            "kind": "pair_and",
            "members": [left, right],
            "complexity": 2,
        }
        for left, right in itertools.combinations(BOOK_FLAGS, 2)
    )
    rows.extend([
        {"candidate_id": "COUNT_GE_2", "kind": "count", "members": list(BOOK_FLAGS), "k": 2, "complexity": 6},
        {"candidate_id": "COUNT_GE_3", "kind": "count", "members": list(BOOK_FLAGS), "k": 3, "complexity": 6},
    ])
    if len(rows) != 18:
        raise AssertionError(f"unexpected candidate count {len(rows)}")
    return rows


def decision(frame: pd.DataFrame, candidate: dict) -> pd.Series:
    known = ~frame.v04_unknown.fillna(True).astype(bool)
    no_exemption = ~frame[list(EXEMPTIONS)].fillna(False).astype(bool).any(axis=1)
    if candidate["kind"] == "null":
        return pd.Series(False, index=frame.index)
    if candidate["kind"] == "pair_and":
        left, right = candidate["members"]
        return (
            frame[left].fillna(False).astype(bool)
            & frame[right].fillna(False).astype(bool)
            & known
            & no_exemption
        )
    if candidate["kind"] == "count":
        count = frame[candidate["members"]].fillna(False).astype(bool).sum(axis=1)
        return count.ge(int(candidate["k"])) & known & no_exemption
    raise ValueError(candidate)


def one_stats(frame: pd.DataFrame) -> dict:
    mature = frame[frame.outcome.isin(["win", "stop", "timeout"])]
    counts = {label: int(frame.outcome.eq(label).sum()) for label in ("win", "stop", "timeout", "open")}
    n = len(mature)
    return {
        "n_total": len(frame), "n_mature": n, **counts,
        "win_rate": counts["win"] / n * 100.0 if n else None,
        "stop_rate": counts["stop"] / n * 100.0 if n else None,
        "ev": (counts["win"] * 5.0 - counts["stop"] * 8.0) / n if n else None,
    }


def compare(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before = one_stats(frame)
    after = one_stats(frame.loc[~reject])
    removed = one_stats(frame.loc[reject])
    return {
        "before": before,
        "after": after,
        "removed": removed,
        "retained_mature_pct": after["n_mature"] / before["n_mature"] * 100.0 if before["n_mature"] else None,
        "stop_capture_pct": removed["stop"] / before["stop"] * 100.0 if before["stop"] else None,
        "winner_removed_pct": removed["win"] / before["win"] * 100.0 if before["win"] else None,
        "delta_win_rate_pp": after["win_rate"] - before["win_rate"] if before["n_mature"] and after["n_mature"] else None,
        "delta_stop_rate_pp": after["stop_rate"] - before["stop_rate"] if before["n_mature"] and after["n_mature"] else None,
        "delta_ev_pp": after["ev"] - before["ev"] if before["n_mature"] and after["n_mature"] else None,
    }


def fold_frame(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return frame[(frame.d >= start) & (frame.d <= end)]


def candidate_metrics(development: pd.DataFrame, candidate: dict) -> dict:
    overall = compare(development, decision(development, candidate))
    folds = {
        name: compare(part := fold_frame(development, start, end), decision(part, candidate))
        for name, (start, end) in FOLDS.items()
    }
    stop_deltas = [value["delta_stop_rate_pp"] for value in folds.values()]
    ev_deltas = [value["delta_ev_pp"] for value in folds.values()]
    checks = {
        "retention_ge_70": overall["retained_mature_pct"] >= 70.0,
        "winner_removed_le_20": overall["winner_removed_pct"] <= 20.0,
        "capture_advantage_ge_5": overall["stop_capture_pct"] - overall["winner_removed_pct"] >= 5.0,
        "pooled_stop_delta_le_m1": overall["delta_stop_rate_pp"] <= -1.0,
        "pooled_ev_delta_ge_p010": overall["delta_ev_pp"] >= 0.10,
        "stop_nonworse_at_least_3_folds": sum(value <= 0.0 for value in stop_deltas) >= 3,
        "ev_nonworse_at_least_3_folds": sum(value >= 0.0 for value in ev_deltas) >= 3,
        "worst_fold_stop_delta_le_p1": max(stop_deltas) <= 1.0,
        "worst_fold_ev_delta_ge_m015": min(ev_deltas) >= -0.15,
    }
    return {
        "candidate": candidate,
        "overall": overall,
        "folds": folds,
        "checks": checks,
        "eligible": candidate["kind"] != "null" and all(checks.values()),
        "gate_pass_count": sum(checks.values()),
        "worst_fold_stop_delta_pp": max(stop_deltas),
        "worst_fold_ev_delta_pp": min(ev_deltas),
    }


def ranking_key(value: dict) -> tuple:
    overall = value["overall"]
    candidate = value["candidate"]
    return (
        value["worst_fold_stop_delta_pp"],
        -value["worst_fold_ev_delta_pp"],
        overall["delta_stop_rate_pp"],
        -overall["delta_win_rate_pp"],
        -overall["delta_ev_pp"],
        -overall["stop_capture_pct"],
        overall["winner_removed_pct"],
        candidate["complexity"],
        candidate["candidate_id"],
    )


def select_candidate(development: pd.DataFrame) -> tuple[dict, list[dict], dict]:
    metrics = [candidate_metrics(development, candidate) for candidate in candidates()]
    eligible = sorted((value for value in metrics if value["eligible"]), key=ranking_key)
    if eligible:
        selected = eligible[0]
    else:
        selected = next(value for value in metrics if value["candidate"]["kind"] == "null")
    challengers = sorted(
        (value for value in metrics if value["candidate"]["kind"] != "null"),
        key=lambda value: (-value["gate_pass_count"], *ranking_key(value)),
    )
    nonzero = [value for value in challengers if value["overall"]["removed"]["n_mature"] > 0]
    return selected, metrics, nonzero[0] if nonzero else challengers[0]


def month_bootstrap(frame: pd.DataFrame, candidate: dict) -> dict:
    mature = frame[frame.outcome.isin(["win", "stop", "timeout"])].copy()
    mature["month"] = mature.d.str[:7]
    months = sorted(mature.month.unique())
    groups = {month: mature[mature.month == month] for month in months}
    rng = np.random.default_rng(RNG_SEED)
    values = []
    for _ in range(BOOTSTRAP_REPS):
        sample = pd.concat(
            [groups[months[i]] for i in rng.integers(0, len(months), len(months))],
            ignore_index=True,
        )
        result = compare(sample, decision(sample, candidate))
        values.append([
            result["delta_stop_rate_pp"],
            result["delta_win_rate_pp"],
            result["delta_ev_pp"],
        ])
    array = np.asarray(values)
    return {
        "reps": BOOTSTRAP_REPS,
        "seed": RNG_SEED,
        "months": len(months),
        "delta_stop_rate_ci95": np.percentile(array[:, 0], [2.5, 97.5]).tolist(),
        "delta_win_rate_ci95": np.percentile(array[:, 1], [2.5, 97.5]).tolist(),
        "delta_ev_ci95": np.percentile(array[:, 2], [2.5, 97.5]).tolist(),
        "warning": "post-selection ordinary month-block interval; not multiplicity-corrected",
    }


def compact_grid(metrics: list[dict]) -> pd.DataFrame:
    rows = []
    for value in metrics:
        candidate, overall = value["candidate"], value["overall"]
        row = {
            "candidate_id": candidate["candidate_id"],
            "kind": candidate["kind"],
            "members": "+".join(candidate["members"]),
            "complexity": candidate["complexity"],
            "eligible": value["eligible"],
            "gate_pass_count": value["gate_pass_count"],
            "retained_mature_pct": overall["retained_mature_pct"],
            "stop_capture_pct": overall["stop_capture_pct"],
            "winner_removed_pct": overall["winner_removed_pct"],
            "delta_win_rate_pp": overall["delta_win_rate_pp"],
            "delta_stop_rate_pp": overall["delta_stop_rate_pp"],
            "delta_ev_pp": overall["delta_ev_pp"],
            "worst_fold_stop_delta_pp": value["worst_fold_stop_delta_pp"],
            "worst_fold_ev_delta_pp": value["worst_fold_ev_delta_pp"],
        }
        for name, result in value["folds"].items():
            row[f"{name}_stop_delta_pp"] = result["delta_stop_rate_pp"]
            row[f"{name}_ev_delta_pp"] = result["delta_ev_pp"]
        row.update({f"check_{name}": passed for name, passed in value["checks"].items()})
        rows.append(row)
    return pd.DataFrame(rows)


def split_summary(frame: pd.DataFrame, candidate: dict) -> dict:
    return {
        "all": compare(frame, decision(frame, candidate)),
        "by_month": {
            month: compare(part, decision(part, candidate))
            for month, part in frame.groupby(frame.d.str[:7], sort=True)
        },
    }


def judge_summary(frame: pd.DataFrame, candidate: dict) -> dict:
    if not SHADOW.exists():
        return {}
    shadow = pd.read_csv(SHADOW, dtype={"code": str, "d": str})
    shadow["code"] = shadow.code.str.zfill(6)
    columns = ["code", "d", "judge", "shadow_cooldown"]
    merged = frame.merge(shadow[columns], on=["code", "d"], how="left", validate="one_to_one")
    return {
        judge: compare(part, decision(part, candidate))
        for judge, part in merged[merged.judge.isin(["✓", "?", "✗"])].groupby("judge", sort=True)
    }


def target_case_summary(frame: pd.DataFrame, candidate: dict) -> list[dict]:
    subset = frame[
        frame.apply(lambda row: (str(row.d), str(row.code).zfill(6)) in TARGET_CASES, axis=1)
    ].copy()
    reject = decision(subset, candidate)
    rows = []
    for idx, row in subset.iterrows():
        rows.append({
            "d": str(row.d), "code": str(row.code).zfill(6), "name": str(row["name"]),
            "outcome": str(row.outcome), "filtered": bool(reject.loc[idx]),
            "book_hits": [flag for flag in BOOK_FLAGS if bool(row[flag])],
            "exemption_hits": [flag for flag in EXEMPTIONS if bool(row[flag])],
        })
    return sorted(rows, key=lambda value: (value["d"], value["code"]))


def format_comp(result: dict) -> str:
    before, after = result["before"], result["after"]
    return (
        f"{before['n_mature']}->{after['n_mature']} | "
        f"{before['win']}/{before['stop']}/{before['timeout']} -> "
        f"{after['win']}/{after['stop']}/{after['timeout']} | "
        f"win {result['delta_win_rate_pp']:+.3f}pp | "
        f"stop {result['delta_stop_rate_pp']:+.3f}pp | EV {result['delta_ev_pp']:+.3f}pp"
    )


def render(summary: dict) -> str:
    selected = summary["selected"]
    challenger = summary["best_challenger"]
    lines = [
        "# Agent 1.5 v0.4 重建结果",
        "",
        f"状态：**{summary['decision']}**。生产文件未修改。",
        "",
        "## 结论",
        "",
        f"- 正式选择：`{selected['candidate']['candidate_id']}`；开发期合格非空候选 "
        f"{summary['selection']['eligible_non_null_count']}/{summary['selection']['non_null_count']}。",
        f"- 最接近门禁的非空挑战者：`{challenger['candidate']['candidate_id']}`，通过 "
        f"{challenger['gate_pass_count']}/9 项。",
        "- 2026-07-13以后只能称为已污染的锁定审计，不是干净OOS；真正前瞻OOS从冻结后且T>2026-09-09开始。",
        "",
        "## 开发期（N=5未冷却候选）",
        "",
        f"总计：{format_comp(selected['overall'])}",
        "",
        "|时间块|成熟N前->后|W/S/T前|W/S/T后|胜率变化|雷率变化|EV变化|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in selected["folds"].items():
        before, after = result["before"], result["after"]
        lines.append(
            f"|{name}|{before['n_mature']}->{after['n_mature']}|"
            f"{before['win']}/{before['stop']}/{before['timeout']}|"
            f"{after['win']}/{after['stop']}/{after['timeout']}|"
            f"{result['delta_win_rate_pp']:+.3f}pp|{result['delta_stop_rate_pp']:+.3f}pp|"
            f"{result['delta_ev_pp']:+.3f}pp|"
        )
    boot = summary["development_bootstrap"]
    lines.extend([
        "",
        f"月份块bootstrap（5,000次，选择后普通区间）：雷率 `{boot['delta_stop_rate_ci95']}`，"
        f"胜率 `{boot['delta_win_rate_ci95']}`，EV `{boot['delta_ev_ci95']}`。",
        "",
        "## 2026-07-13以后污染锁定审计",
        "",
        f"全部：{format_comp(summary['locked_audit']['all'])}",
        f"open={summary['locked_audit']['all']['before']['open']}，不进入胜率/雷率分母。",
        "",
        "## 五个已知打勾暴雷（只审计，不选型）",
        "",
        "|T日|代码|股票|B命中|豁免|v0.4过滤|",
        "|---|---:|---|---|---|",
    ])
    for row in summary["known_check_stops"]:
        lines.append(
            f"|{row['d']}|{row['code']}|{row['name']}|{'+'.join(row['book_hits']) or '-'}|"
            f"{'+'.join(row['exemption_hits']) or '-'}|{'是' if row['filtered'] else '否'}|"
        )
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- v0.4 使用书籍章节映射的 B1—B6、E1/E2 和18条小候选集，不沿用 F1—F5 网格或五雷优先排序。",
        "- 07-13以后数据虽未进入程序选型，但研究者此前看过该窗口，不能恢复成纯OOS。",
        "- 当前股票池不是历史point-in-time；普通bootstrap也未校正选择步骤和多重检验。",
        "- 只有非空规则通过开发门禁，才有资格原样进入未来shadow；任何历史审计都不能直接转生产。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes = verify_inputs()
    signals = pd.read_csv(SIGNALS, dtype={"code": str, "d": str})
    signals["code"] = signals.code.str.zfill(6)
    if signals.cooldown5.fillna(False).astype(bool).any():
        raise AssertionError("signals_cooldown5 contains cooled rows")
    if not signals.aggregate_eligible.fillna(False).astype(bool).all():
        raise AssertionError("signals_cooldown5 contains non-primary-universe rows")

    features = build_features(signals)
    frame = signals.merge(features, on=["code", "d"], how="left", validate="one_to_one")
    if len(frame) != len(signals):
        raise AssertionError("feature merge changed signal count")
    frame[list(BOOK_FLAGS) + list(EXEMPTIONS)] = frame[list(BOOK_FLAGS) + list(EXEMPTIONS)].fillna(False).astype(bool)
    frame["v04_unknown"] = frame.v04_unknown.fillna(True).astype(bool)

    development = frame[frame.d <= DEV_END].copy()
    embargo = frame[(frame.d >= EMBARGO_START) & (frame.d <= EMBARGO_END)].copy()
    locked_audit = frame[frame.d >= AUDIT_START].copy()
    if len(development) != 1703 or len(embargo) != 67 or len(locked_audit) != 92:
        raise AssertionError(
            f"unexpected split sizes dev={len(development)} embargo={len(embargo)} audit={len(locked_audit)}"
        )

    selected, metrics, best_challenger = select_candidate(development)
    grid = compact_grid(metrics)
    grid.to_csv(OUT / "v04_development_grid.csv", index=False, encoding="utf-8-sig")

    selected_candidate = selected["candidate"]
    frame["v04_reject"] = decision(frame, selected_candidate)
    frame["v04_decision"] = np.where(frame.v04_unknown, "unknown_keep", np.where(frame.v04_reject, "reject", "keep"))
    frame["v04_candidate_id"] = selected_candidate["candidate_id"]
    frame["v04_split"] = np.select(
        [frame.d <= DEV_END, (frame.d >= EMBARGO_START) & (frame.d <= EMBARGO_END), frame.d >= AUDIT_START],
        ["development", "embargo", "locked_audit"],
        default="unassigned",
    )
    frame.to_csv(OUT / "v04_row_audit.csv.gz", index=False, compression="gzip")

    locked = split_summary(locked_audit, selected_candidate)
    locked["by_judge"] = judge_summary(locked_audit, selected_candidate)
    known = target_case_summary(locked_audit, selected_candidate)
    if len(known) != 5:
        raise AssertionError(f"expected five known cases, found {len(known)}")

    eligible_count = sum(value["eligible"] for value in metrics)
    decision_name = (
        "eligible_for_prospective_shadow_not_production"
        if selected_candidate["kind"] != "null"
        else "keep_all_no_stable_technical_filter"
    )
    summary = {
        "schema": "bottom-agent15-v04-rebuild/v1",
        "decision": decision_name,
        "protocol_sha256": sha256(PROTOCOL),
        "source_hashes": {
            **input_hashes,
            str(HERE / "v04_rebuild.py"): sha256(HERE / "v04_rebuild.py"),
        },
        "scope": {
            "pipeline": "engine absolute gate -> N=5 rotating gate -> cooldown=false -> agent1.5",
            "development_end": DEV_END,
            "embargo": [EMBARGO_START, EMBARGO_END],
            "locked_audit_start": AUDIT_START,
            "true_prospective_oos": "new events with T>2026-09-09 after protocol/rule freeze",
            "locked_audit_is_clean_oos": False,
        },
        "sample_sizes": {
            "development": len(development),
            "embargo": len(embargo),
            "locked_audit_total": len(locked_audit),
            "locked_audit_mature": int(locked_audit.outcome.isin(["win", "stop", "timeout"]).sum()),
            "locked_audit_open": int(locked_audit.outcome.eq("open").sum()),
        },
        "selection": {
            "candidate_count": len(metrics),
            "non_null_count": len(metrics) - 1,
            "eligible_non_null_count": eligible_count,
            "holdout_outcomes_used_for_selection": False,
            "null_fallback": True,
        },
        "selected": selected,
        "best_challenger": best_challenger,
        "development_bootstrap": month_bootstrap(development, selected_candidate),
        "embargo_descriptive_only": compare(embargo, decision(embargo, selected_candidate)),
        "locked_audit": locked,
        "known_check_stops": known,
        "limitations": [
            "post-2026-07-12 data were previously seen during v0.2/v0.3 research, so the locked audit is contaminated",
            "the current amount-ranked universe is not point-in-time and has survivorship/liquidity bias",
            "ordinary post-selection bootstrap intervals are not multiplicity-corrected",
            "technical proxies do not identify market manipulation or dealer intent",
        ],
    }
    dump(
        OUT / "v04_selected_candidate.json",
        {
            "selected": selected_candidate,
            "decision": decision_name,
            "protocol_sha256": summary["protocol_sha256"],
            "script_sha256": summary["source_hashes"][str(HERE / "v04_rebuild.py")],
        },
    )
    dump(OUT / "v04_summary.json", summary)
    markdown = render(summary)
    (OUT / "v04_summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
