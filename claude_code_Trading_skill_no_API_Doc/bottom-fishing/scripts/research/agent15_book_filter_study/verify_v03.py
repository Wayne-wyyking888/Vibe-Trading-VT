# -*- coding: utf-8 -*-
"""Independent mechanical verification of v0.3 isolation, selection, and validation."""
from __future__ import annotations

import hashlib
import json
import pathlib

import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
FAMILIES = ("F1", "F2", "F3", "F4", "F5")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def decide(frame: pd.DataFrame, config: dict) -> pd.Series:
    active = [config[family] for family in FAMILIES if config.get(family)]
    count = sum(
        (frame[name].fillna(False).astype(bool).astype(int) for name in active),
        start=pd.Series(0, index=frame.index),
    )
    return (count >= int(config["k"])) & ~frame.v03_unknown.fillna(True).astype(bool)


def one_stats(frame: pd.DataFrame) -> dict:
    mature = frame[frame.outcome != "open"]
    n = len(mature)
    wins = int(mature.outcome.eq("win").sum())
    stops = int(mature.outcome.eq("stop").sum())
    timeouts = int(mature.outcome.eq("timeout").sum())
    return {
        "n_mature": n,
        "win": wins,
        "stop": stops,
        "timeout": timeouts,
        "win_rate": wins / n * 100 if n else None,
        "stop_rate": stops / n * 100 if n else None,
        "ev": (wins * 5 - stops * 8) / n if n else None,
    }


def compare(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before = one_stats(frame)
    after = one_stats(frame.loc[~reject])
    removed = one_stats(frame.loc[reject])
    return {
        "before": before,
        "after": after,
        "removed": removed,
        "retained_mature_pct": after["n_mature"] / before["n_mature"] * 100,
        "removed_winner_share_pct": removed["win"] / before["win"] * 100,
        "delta_win_rate_pp": after["win_rate"] - before["win_rate"],
        "delta_stop_rate_pp": after["stop_rate"] - before["stop_rate"],
        "delta_ev_pp": after["ev"] - before["ev"],
    }


def close(left, right, tolerance: float = 1e-10) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def same_comparison(got: dict, expected: dict) -> bool:
    count_keys = ("n_mature", "win", "stop", "timeout")
    rate_keys = ("win_rate", "stop_rate", "ev")
    for group in ("before", "after", "removed"):
        if any(got[group][key] != expected[group][key] for key in count_keys):
            return False
        if any(not close(got[group][key], expected[group][key]) for key in rate_keys):
            return False
    return all(
        close(got[key], expected[key])
        for key in ("retained_mature_pct", "removed_winner_share_pct", "delta_win_rate_pp",
                    "delta_stop_rate_pp", "delta_ev_pp")
    )


def main() -> int:
    summary = json.loads((OUT / "v03_summary.json").read_text(encoding="utf-8"))
    selected = summary["selected"]
    grid = pd.read_csv(OUT / "v03_grid_all.csv.gz", low_memory=False)
    features = pd.read_csv(OUT / "v03_feature_matrix.csv.gz", dtype={"code": str, "d": str})
    signals = pd.read_csv(OUT / "signals_all_codes.csv.gz", dtype={"code": str, "d": str})
    shadow = pd.read_csv(OUT / "shadow_event_audit.csv", dtype={"code": str, "d": str})
    audit = pd.read_csv(OUT / "v03_shadow_row_audit.csv", dtype={"code": str, "d": str})

    checks = {}
    checks["protocol_hash"] = sha256(HERE / "V03_POSTHOC_PROTOCOL.md") == summary["protocol_sha256"]
    checks["source_hashes"] = all(
        sha256(pathlib.Path(path)) == digest for path, digest in summary["source_hashes"].items()
    )
    checks["grid_count_523864"] = len(grid) == 523_864
    forbidden = ("global", "pre_shadow", "year", "2024", "2025", "2026", "bootstrap")
    checks["grid_has_no_validation_metrics"] = not any(
        any(token in column.lower() for token in forbidden) for column in grid.columns
    )

    eligible = grid[grid.eligible_guarded.astype(bool)]
    ranked = eligible.sort_values(
        ["check_stops_removed", "check_winners_removed", "all_stops_removed", "all_winners_removed",
         "check_ev_after", "active_families", "k", "candidate_id"],
        ascending=[False, True, False, True, False, True, False, True],
    )
    checks["selected_is_exact_shadow_rank1"] = (
        not ranked.empty and ranked.iloc[0].candidate_id == selected["candidate_id"]
    )
    target5 = grid[grid.check_stops_removed.eq(5)]
    checks["target5_feasibility_recomputes"] = (
        len(target5) == summary["grid"]["target5_feasibility"]["candidates"]
        and int(target5.eligible_guarded.astype(bool).sum())
        == summary["grid"]["target5_feasibility"]["eligible_guarded"]
        and int(target5.check_winners_removed.min()) == 5
    )

    shadow_cool = shadow.shadow_cooldown.fillna(False).astype(bool)
    calibration = shadow[
        (~shadow_cool) & shadow.judge.isin(["✓", "?", "✗"])
        & shadow.outcome.isin(["win", "stop", "timeout"])
    ].merge(features, on=["code", "d"], how="inner", validate="one_to_one")
    checks["calibration_is_disjoint_and_expected_size"] = (
        len(calibration) == 61
        and len(calibration[calibration.judge == "✓"]) == 25
        and calibration.d.min() > "2026-07-12"
    )
    required_audit = {"v03_trigger_count", "v03_reject", "v03_keep", "v03_candidate_id"}
    checks["shadow_row_audit_recomputes"] = (
        required_audit.issubset(audit.columns)
        and audit.v03_candidate_id.nunique() == 1
        and audit.v03_candidate_id.iloc[0] == selected["candidate_id"]
        and (audit.v03_keep.astype(bool) == ~audit.v03_reject.astype(bool)).all()
        and (audit.v03_reject.astype(bool) == decide(audit, selected)).all()
    )
    check_stops = calibration[(calibration.judge == "✓") & calibration.outcome.eq("stop")]
    checks["selected_filters_all_five_check_stops"] = len(check_stops) == 5 and decide(check_stops, selected).all()
    check_wins = calibration[(calibration.judge == "✓") & calibration.outcome.eq("win")]
    checks["selected_removes_exactly_five_check_winners"] = int(decide(check_wins, selected).sum()) == 5

    enriched = signals.merge(features, on=["code", "d"], how="left", validate="one_to_one")
    main = enriched[enriched.aggregate_eligible.fillna(False).astype(bool)]
    cool = main[~main.cooldown5.fillna(False).astype(bool)]
    pre = cool[cool.d < "2026-07-13"]
    checks["validation_scope_is_expected_and_disjoint"] = (
        len(pre[pre.outcome != "open"]) == 1770
        and pre.d.max() <= "2026-07-12"
        and calibration.d.min() > pre.d.max()
    )
    scopes = {
        "pre_shadow": pre,
        "year_2024": pre[pre.d.str.startswith("2024")],
        "year_2025": pre[pre.d.str.startswith("2025")],
        "year_2026_pre": pre[pre.d.str.startswith("2026")],
    }
    checks["validation_headlines_recompute"] = all(
        same_comparison(compare(frame, decide(frame, selected)), summary["global"][key])
        for key, frame in scopes.items()
    )

    expected_gate = {
        "five_check_stops_filtered": True,
        "aggregate_mature_retention_ge_60pct": summary["global"]["pre_shadow"]["retained_mature_pct"] >= 60.0,
        "aggregate_winner_removal_le_25pct": summary["global"]["pre_shadow"]["removed_winner_share_pct"] <= 25.0,
        "aggregate_stop_delta_le_minus_3pp": summary["global"]["pre_shadow"]["delta_stop_rate_pp"] <= -3.0,
        "aggregate_win_delta_ge_plus_1pp": summary["global"]["pre_shadow"]["delta_win_rate_pp"] >= 1.0,
        "aggregate_ev_delta_ge_plus_0_5pp": summary["global"]["pre_shadow"]["delta_ev_pp"] >= 0.5,
        "bootstrap_stop_delta_upper_lt_0": summary["global"]["pre_shadow_month_bootstrap"]["delta_stop_rate_ci95"][1] < 0.0,
        "bootstrap_ev_delta_lower_gt_0": summary["global"]["pre_shadow_month_bootstrap"]["delta_ev_ci95"][0] > 0.0,
    }
    for key, label in (("year_2024", "2024"), ("year_2025", "2025"), ("year_2026_pre", "2026pre")):
        expected_gate[f"{label}_stop_not_worse"] = summary["global"][key]["delta_stop_rate_pp"] <= 0.0
        expected_gate[f"{label}_mature_retention_ge_50pct"] = summary["global"][key]["retained_mature_pct"] >= 50.0
    checks["gate_and_failure_decision_recompute"] = (
        expected_gate == summary["go_no_go"]["checks"]
        and not all(expected_gate.values())
        and summary["go_no_go"]["decision"] == "fails_v03_historical_validation_research_only"
    )

    checks = {name: bool(value) for name, value in checks.items()}
    result = {
        "schema": "bottom-agent15-v03-verification/v1",
        "passed": all(checks.values()),
        "checks": checks,
    }
    (OUT / "v03_verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
