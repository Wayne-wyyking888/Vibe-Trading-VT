# -*- coding: utf-8 -*-
"""Agent 1.5 v0.2: select on judged shadow rows, then open global history."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import pathlib

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2]
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
PROTOCOL = HERE / "V02_CALIBRATION_PROTOCOL.md"
SIGNALS_ALL = OUT / "signals_all_codes.csv.gz"
SIGNALS_RAW = OUT / "signals_raw_qualified.csv.gz"
SIGNALS_COOL = OUT / "signals_cooldown5.csv.gz"
KLINES = OUT / "klines.csv.gz"
SHADOW_AUDIT = OUT / "shadow_event_audit.csv"
FAMILIES = ("F1", "F2", "F3", "F4", "F5")

VARIANTS = {
    "F1": {
        "F1_R110": (1.10,), "F1_R130": (1.30,), "F1_R150": (1.50,), "F1_R180": (1.80,),
    },
    "F2": {
        "F2_B10_G040": (10, -0.40), "F2_B10_G075": (10, -0.75),
        "F2_B11_G025": (11, -0.25), "F2_B11_G040": (11, -0.40),
        "F2_B11_G075": (11, -0.75), "F2_B12_G075": (12, -0.75),
    },
    "F3": {
        "F3_V120_E025": (1.20, 0.25), "F3_V150_E025": (1.50, 0.25),
        "F3_V180_E020": (1.80, 0.20), "F3_V180_E025": (1.80, 0.25),
        "F3_V180_E035": (1.80, 0.35), "F3_V220_E025": (2.20, 0.25),
    },
    "F4": {
        "F4_W35_V120": (0.35, 1.20), "F4_W35_V150": (0.35, 1.50),
        "F4_W45_V120": (0.45, 1.20), "F4_W45_V150": (0.45, 1.50),
        "F4_W45_V200": (0.45, 2.00), "F4_W55_V150": (0.55, 1.50),
    },
    "F5": {
        "F5_R06_D02_V120": (0.06, -0.02, 1.20), "F5_R08_D02_V150": (0.08, -0.02, 1.50),
        "F5_R08_D04_V120": (0.08, -0.04, 1.20), "F5_R08_D04_V150": (0.08, -0.04, 1.50),
        "F5_R10_D04_V150": (0.10, -0.04, 1.50), "F5_R08_D06_V150": (0.08, -0.06, 1.50),
    },
}


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def dump(path: pathlib.Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def event_feature_row(s: pd.DataFrame, i: int, code: str, d: str) -> dict:
    result = {"code": code, "d": d, "v02_unknown": False}
    if i < 60:
        result["v02_unknown"] = True
        return result
    c, o, h, l, v = s.c, s.o, s.h, s.l, s.v
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_abs = float(tr.rolling(14).mean().iloc[i])
    if not np.isfinite(atr_abs) or atr_abs <= 0 or (v.iloc[i - 60:i + 1] <= 0).any():
        result["v02_unknown"] = True
        return result

    # F1 primitives and variants.
    win20 = s.iloc[i - 19:i + 1]
    ret20_series = c.pct_change().iloc[i - 19:i + 1]
    up, down = ret20_series > 0, ret20_series < 0
    up_vol = float(win20.loc[up.values, "v"].sum())
    ratio = float(win20.loc[down.values, "v"].sum() / up_vol) if up_vol > 0 else np.nan
    ret20 = float(c.iloc[i] / c.iloc[i - 20] - 1)
    f1_base = bool(up.sum() >= 4 and down.sum() >= 4 and np.isfinite(ratio) and ret20 <= 0)
    for name, (cut,) in VARIANTS["F1"].items():
        result[name] = bool(f1_base and ratio >= cut)

    # F2 primitives and variants.
    typical = (win20.h + win20.l + win20.c) / 3
    bulls = int((win20.c > win20.o).sum())
    gravity = float((typical.iloc[-5:].median() - typical.iloc[:5].median()) / atr_abs)
    for name, (bull_min, gravity_max) in VARIANTS["F2"].items():
        result[name] = bool(bulls >= bull_min and gravity <= gravity_max)

    # F3 primitives and variants.
    recent10, prior50 = s.iloc[i - 9:i + 1], s.iloc[i - 59:i - 9]
    prior_median = float(prior50.v.median())
    peak_ratio = float(recent10.v.max() / prior_median) if prior_median > 0 else np.nan
    ret10 = float(c.iloc[i] / c.iloc[i - 10] - 1)
    path = float(c.pct_change().iloc[i - 9:i + 1].abs().sum())
    efficiency = abs(ret10) / path if path > 0 else 0.0
    for name, (volume_min, efficiency_max) in VARIANTS["F3"].items():
        result[name] = bool(np.isfinite(peak_ratio) and peak_ratio >= volume_min and ret10 <= 0.02 and efficiency <= efficiency_max)

    # F4 variants.
    for name, (wick_min, volume_min) in VARIANTS["F4"].items():
        hit = False
        for j in range(i - 9, i + 1):
            prev, day = s.iloc[j - 20:j], s.iloc[j]
            prior_high, prior_volume = float(prev.h.max()), float(prev.v.median())
            span = float(day.h - day.l)
            upper = float(day.h - max(day.o, day.c)) / span if span > 0 else 0.0
            vr = float(day.v / prior_volume) if prior_volume > 0 else 0.0
            if day.h >= prior_high * 0.997 and day.c <= prior_high and upper >= wick_min and vr >= volume_min and c.iloc[i] <= day.c:
                hit = True
                break
        result[name] = hit

    # F5 primitives and variants.
    peak_j = int(h.iloc[i - 9:i + 1].idxmax())
    base, peak = s.iloc[max(0, peak_j - 20):peak_j], s.iloc[peak_j]
    if len(base) >= 10 and base.l.min() > 0 and base.v.median() > 0:
        rebound = float(peak.h / base.l.min() - 1)
        retrace = float(c.iloc[i] / peak.h - 1)
        peak_vr = float(peak.v / base.v.median())
    else:
        rebound, retrace, peak_vr = np.nan, np.nan, np.nan
    for name, (rebound_min, retrace_max, volume_min) in VARIANTS["F5"].items():
        result[name] = bool(np.isfinite(rebound) and rebound >= rebound_min and retrace <= retrace_max and peak_vr >= volume_min)
    return result


def build_feature_matrix() -> pd.DataFrame:
    all_signals = pd.read_csv(SIGNALS_ALL, dtype={"code": str, "d": str})
    shadow = pd.read_csv(SHADOW_AUDIT, dtype={"code": str, "d": str})
    keys = pd.concat([all_signals[["code", "d"]], shadow[["code", "d"]]], ignore_index=True).drop_duplicates()
    needed = {code: set(group.d) for code, group in keys.groupby("code")}
    klines = pd.read_csv(KLINES, dtype={"code": str, "d": str})
    rows = []
    for k, (code, raw) in enumerate(klines.groupby("code", sort=True), 1):
        if code not in needed:
            continue
        s = raw.sort_values("d").drop_duplicates("d").reset_index(drop=True)
        pos = {d: i for i, d in enumerate(s.d)}
        for d in sorted(needed[code]):
            if d in pos:
                rows.append(event_feature_row(s, pos[d], code, d))
        if k % 100 == 0:
            print(f"[v02 features] {k}/{klines.code.nunique()} events={len(rows)}", flush=True)
    features = pd.DataFrame(rows)
    for family in FAMILIES:
        for name in VARIANTS[family]:
            if name not in features:
                features[name] = False
            features[name] = features[name].fillna(False).astype(bool)
    features["v02_unknown"] = features.v02_unknown.fillna(True).astype(bool)
    features.to_csv(OUT / "v02_feature_matrix.csv.gz", index=False, compression="gzip")
    return features


def decision(frame: pd.DataFrame, config: dict) -> pd.Series:
    active = [config[family] for family in FAMILIES if config.get(family)]
    count = sum((frame[name].fillna(False).astype(bool).astype(int) for name in active), start=pd.Series(0, index=frame.index))
    return (count >= int(config["k"])) & ~frame.v02_unknown.fillna(True).astype(bool)


def outcome_counts(frame: pd.DataFrame, reject: pd.Series | None = None) -> dict:
    chosen = frame if reject is None else frame.loc[~reject]
    mature = chosen[chosen.outcome != "open"]
    n = len(mature)
    count = {key: int((chosen.outcome == key).sum()) for key in ("win", "stop", "timeout", "open")}
    return {
        "n_total": len(chosen), "n_mature": n, **count,
        "win_rate": count["win"] / n * 100 if n else None,
        "stop_rate": count["stop"] / n * 100 if n else None,
        "ev": (count["win"] * 5 - count["stop"] * 8) / n if n else None,
    }


def compare(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before, after = outcome_counts(frame), outcome_counts(frame, reject)
    removed = frame.loc[reject]
    return {
        "before": before, "after": after, "removed": outcome_counts(removed),
        "retained_mature_pct": after["n_mature"] / before["n_mature"] * 100 if before["n_mature"] else None,
        "removed_winner_share_pct": int((removed.outcome == "win").sum()) / before["win"] * 100 if before["win"] else None,
        "delta_win_rate_pp": after["win_rate"] - before["win_rate"] if after["win_rate"] is not None else None,
        "delta_stop_rate_pp": after["stop_rate"] - before["stop_rate"] if after["stop_rate"] is not None else None,
        "delta_ev_pp": after["ev"] - before["ev"] if after["ev"] is not None else None,
    }


def calibration_metrics(frame: pd.DataFrame, reject: pd.Series) -> dict:
    checks = frame[frame.judge == "✓"]
    check_reject = reject.loc[checks.index]
    return {
        "cal_n": len(frame),
        "check_n": len(checks),
        "check_stops_removed": int((check_reject & checks.outcome.eq("stop")).sum()),
        "check_winners_removed": int((check_reject & checks.outcome.eq("win")).sum()),
        "all_stops_removed": int((reject & frame.outcome.eq("stop")).sum()),
        "all_winners_removed": int((reject & frame.outcome.eq("win")).sum()),
        "check_mature_retained": int((~check_reject).sum()),
        "all_mature_retained": int((~reject).sum()),
        "check_ev_after": outcome_counts(checks, check_reject)["ev"],
    }


def eligible(m: dict, frame: pd.DataFrame) -> bool:
    checks = frame[frame.judge == "✓"]
    check_wins = int((checks.outcome == "win").sum())
    all_wins = int((frame.outcome == "win").sum())
    return bool(
        m["check_mature_retained"] / len(checks) >= 0.60
        and (check_wins - m["check_winners_removed"]) / check_wins >= 0.75
        and m["all_mature_retained"] / len(frame) >= 0.60
        and (all_wins - m["all_winners_removed"]) / all_wins >= 0.75
        and m["check_stops_removed"] >= 1
    )


def grid_search(calibration: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    choices = [[None, *VARIANTS[family].keys()] for family in FAMILIES]
    for selected in itertools.product(*choices):
        config = dict(zip(FAMILIES, selected))
        active = [name for name in selected if name]
        if not active:
            continue
        counts = sum((calibration[name].astype(int) for name in active), start=pd.Series(0, index=calibration.index))
        for k in range(1, len(active) + 1):
            reject = (counts >= k) & ~calibration.v02_unknown
            m = calibration_metrics(calibration, reject)
            candidate_id = ";".join(f"{family}={config[family] or '-'}" for family in FAMILIES) + f";k={k}"
            rows.append({
                "candidate_id": candidate_id, **config, "k": k, "active_families": len(active),
                **m, "eligible": eligible(m, calibration),
            })
    grid = pd.DataFrame(rows)
    if len(grid) != 50764:
        raise AssertionError(f"grid count {len(grid)} != 50764")
    ordered = grid[grid.eligible].sort_values(
        ["check_stops_removed", "check_winners_removed", "all_stops_removed", "all_winners_removed",
         "check_ev_after", "active_families", "k", "candidate_id"],
        ascending=[False, True, False, True, False, True, False, True],
    )
    if ordered.empty:
        raise RuntimeError("no eligible v0.2 candidate")
    selected = ordered.iloc[0].to_dict()
    grid.to_csv(OUT / "v02_grid_all.csv.gz", index=False, compression="gzip")
    ordered.head(200).to_csv(OUT / "v02_shadow_leaderboard.csv", index=False, encoding="utf-8-sig")
    return grid, selected


def month_bootstrap(frame: pd.DataFrame, config: dict, reps: int = 2000) -> dict:
    mature = frame[frame.outcome != "open"].copy()
    mature["month"] = mature.d.str[:7]
    months = sorted(mature.month.unique())
    groups = {month: mature[mature.month == month] for month in months}
    rng = np.random.default_rng(20260911)
    deltas = []
    for _ in range(reps):
        sample = pd.concat([groups[months[i]] for i in rng.integers(0, len(months), len(months))], ignore_index=True)
        comp = compare(sample, decision(sample, config))
        deltas.append((comp["delta_stop_rate_pp"], comp["delta_ev_pp"]))
    values = np.asarray(deltas)
    return {
        "reps": reps, "months": len(months),
        "delta_stop_rate_ci95": np.percentile(values[:, 0], [2.5, 97.5]).tolist(),
        "delta_ev_ci95": np.percentile(values[:, 1], [2.5, 97.5]).tolist(),
    }


def render(summary: dict) -> str:
    def pct(x):
        return "NA" if x is None else f"{x:.3f}%"

    s = summary["selected"]
    lines = [
        "# Agent 1.5 v0.2：shadow 网格调参与全局回测",
        "",
        "**v0.2 使用已知 shadow 裁定与结果做训练；不是盲测，也不是生产规则。**",
        "",
        f"- 网格：{summary['grid']['candidates']} 个，满足保留约束 {summary['grid']['eligible']} 个。",
        f"- 唯一选择：`{s['candidate_id']}`。",
        f"- shadow 有裁定成熟样本：{summary['calibration']['all']['before']['n_mature']}；其中 ✓ {summary['calibration']['check']['before']['n_mature']}。",
        f"- 最终裁定：`{summary['go_no_go']['decision']}`。",
        "",
        "## Shadow 调参集",
        "",
        "|范围|无v0.2 W/S/T|加v0.2 W/S/T|胜率变化|雷率变化|EV变化|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("check", "✓"), ("all", "✓/?/✗全部")):
        comp = summary["calibration"][key]
        b, a = comp["before"], comp["after"]
        lines.append(f"|{label}|{b['win']}/{b['stop']}/{b['timeout']}|{a['win']}/{a['stop']}/{a['timeout']}|{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{pct(comp['delta_ev_pp'])}|")
    lines += [
        "",
        "## 唯一候选打开全局后的结果",
        "",
        "|范围|成熟N→保留N|胜率变化|雷率变化|EV变化|误杀赢家|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("raw_full", "原始过线全期"), ("cooldown_full", "N=5全期"),
                       ("cooldown_pre_shadow", "N=5且T<2026-07-13"), ("year_2024", "2024"),
                       ("year_2025", "2025"), ("year_2026", "2026")):
        comp = summary["global"][key]
        lines.append(f"|{label}|{comp['before']['n_mature']}→{comp['after']['n_mature']}|{pct(comp['delta_win_rate_pp'])}|{pct(comp['delta_stop_rate_pp'])}|{pct(comp['delta_ev_pp'])}|{pct(comp['removed_winner_share_pct'])}|")
    boot = summary["global"]["pre_shadow_month_bootstrap"]
    lines += [
        "",
        f"pre-shadow 月份块自助95%：雷率增量 `{boot['delta_stop_rate_ci95']}`；EV增量 `{boot['delta_ev_ci95']}`。",
        "",
        "## 解释",
        "",
        "候选只按 shadow 调参集排序，全局字段从未进入网格文件或选择顺序。若 pre-shadow 门槛失败，说明该规则只拟合了少量已知裁定样本；不得改选排行榜中全局表现较好的候选。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    features = build_feature_matrix()
    shadow = pd.read_csv(SHADOW_AUDIT, dtype={"code": str, "d": str})
    shadow_cool = shadow.shadow_cooldown.fillna(False).astype(bool)
    calibration = shadow[(~shadow_cool) & shadow.judge.isin(["✓", "?", "✗"]) & shadow.outcome.isin(["win", "stop", "timeout"])].copy()
    calibration = calibration.merge(features, on=["code", "d"], how="inner", validate="one_to_one")
    if len(calibration) != 61 or len(calibration[calibration.judge == "✓"]) != 25:
        raise AssertionError(f"unexpected calibration size all={len(calibration)} check={len(calibration[calibration.judge == '✓'])}")
    grid, selected_row = grid_search(calibration)
    config = {family: selected_row.get(family) if pd.notna(selected_row.get(family)) else None for family in FAMILIES}
    config["k"] = int(selected_row["k"])
    config["candidate_id"] = str(selected_row["candidate_id"])
    selected_variants = [config[family] for family in FAMILIES if config[family]]
    calibration["v02_trigger_count"] = sum(
        (calibration[name].astype(int) for name in selected_variants),
        start=pd.Series(0, index=calibration.index),
    )
    calibration["v02_reject"] = decision(calibration, config)
    calibration["v02_keep"] = ~calibration.v02_reject
    calibration["v02_candidate_id"] = config["candidate_id"]
    calibration.to_csv(OUT / "v02_shadow_calibration_rows.csv", index=False, encoding="utf-8-sig")

    all_signals = pd.read_csv(SIGNALS_ALL, dtype={"code": str, "d": str})
    enriched = all_signals.merge(features, on=["code", "d"], how="left", validate="one_to_one")
    variant_cols = [name for family in FAMILIES for name in VARIANTS[family]]
    enriched[variant_cols] = enriched[variant_cols].fillna(False).astype(bool)
    enriched["v02_unknown"] = enriched.v02_unknown.fillna(True).astype(bool)
    main = enriched[enriched.aggregate_eligible.fillna(False).astype(bool)].copy()
    cool = main[~main.cooldown5.fillna(False).astype(bool)].copy()
    raw = main.copy()
    pre_shadow = cool[cool.d < "2026-07-13"].copy()
    checks = calibration[calibration.judge == "✓"].copy()
    cal_comp_all = compare(calibration, calibration.v02_reject)
    cal_comp_check = compare(checks, decision(checks, config))
    global_results = {
        "raw_full": compare(raw, decision(raw, config)),
        "cooldown_full": compare(cool, decision(cool, config)),
        "cooldown_pre_shadow": compare(pre_shadow, decision(pre_shadow, config)),
        "year_2024": compare(cool[cool.d.str.startswith("2024")], decision(cool[cool.d.str.startswith("2024")], config)),
        "year_2025": compare(cool[cool.d.str.startswith("2025")], decision(cool[cool.d.str.startswith("2025")], config)),
        "year_2026": compare(cool[cool.d.str.startswith("2026")], decision(cool[cool.d.str.startswith("2026")], config)),
        "pre_shadow_month_bootstrap": month_bootstrap(pre_shadow, config),
    }
    pre = global_results["cooldown_pre_shadow"]
    y24, y25 = global_results["year_2024"], global_results["year_2025"]
    boot = global_results["pre_shadow_month_bootstrap"]
    gate_checks = {
        "pre_shadow_retained_mature_at_least_60pct": pre["retained_mature_pct"] >= 60,
        "pre_shadow_stop_rate_down_at_least_3pp": pre["delta_stop_rate_pp"] <= -3,
        "pre_shadow_ev_up_at_least_0p50pp": pre["delta_ev_pp"] >= 0.50,
        "pre_shadow_removed_winners_at_most_25pct": pre["removed_winner_share_pct"] <= 25,
        "2024_stop_not_worse_over_2pp": y24["delta_stop_rate_pp"] <= 2,
        "2025_stop_not_worse_over_2pp": y25["delta_stop_rate_pp"] <= 2,
        "bootstrap_ev_lower_above_zero": boot["delta_ev_ci95"][0] > 0,
        "bootstrap_stop_upper_below_zero": boot["delta_stop_rate_ci95"][1] < 0,
    }
    passed = bool(all(gate_checks.values()))
    summary = {
        "schema": "bottom-agent15-v02-shadow-grid/v1",
        "status": "post_hoc_shadow_calibration",
        "protocol_sha256": sha256(PROTOCOL),
        "source_hashes": {str(path): sha256(path) for path in (SIGNALS_ALL, SIGNALS_RAW, SIGNALS_COOL, KLINES, SHADOW_AUDIT, HERE / "v02_shadow_grid_search.py")},
        "grid": {"candidates": len(grid), "eligible": int(grid.eligible.sum()), "global_fields_used_for_selection": False},
        "selected": {**config, "calibration_rank": 1},
        "calibration": {"check": cal_comp_check, "all": cal_comp_all},
        "global": global_results,
        "go_no_go": {
            "passed_all": passed,
            "decision": "eligible_for_new_forward_shadow_only" if passed else "shadow_overfit_do_not_add_to_production",
            "checks": gate_checks,
        },
        "limitations": [
            "only 61 mature judged calibration rows and only five check-mark stops",
            "global pre-shadow test is disjoint but reverse-time, not a prospective holdout",
            "current amount-ranked universe is not point-in-time and has survivorship bias",
            "50,764 candidates create severe multiple-testing risk even with retention constraints",
        ],
    }
    dump(OUT / "v02_summary.json", summary)
    dump(OUT / "v02_selected_candidate.json", {"selected": summary["selected"], "protocol_sha256": summary["protocol_sha256"]})
    (OUT / "v02_summary.md").write_text(render(summary), encoding="utf-8")
    print(render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
