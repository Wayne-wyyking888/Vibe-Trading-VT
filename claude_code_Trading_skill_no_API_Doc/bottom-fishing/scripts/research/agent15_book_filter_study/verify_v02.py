# -*- coding: utf-8 -*-
"""Independent mechanical verification of v0.2 selection and headline stats."""
from __future__ import annotations

import hashlib
import json
import pathlib

import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
FAMILIES = ("F1", "F2", "F3", "F4", "F5")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def decide(frame: pd.DataFrame, config: dict) -> pd.Series:
    active = [config[f] for f in FAMILIES if config.get(f)]
    count = sum((frame[name].fillna(False).astype(bool).astype(int) for name in active), start=pd.Series(0, index=frame.index))
    return (count >= int(config["k"])) & ~frame.v02_unknown.fillna(True).astype(bool)


def basic(frame: pd.DataFrame, reject: pd.Series) -> dict:
    chosen = frame.loc[~reject]
    mature = chosen[chosen.outcome != "open"]
    n = len(mature)
    w, s = int((mature.outcome == "win").sum()), int((mature.outcome == "stop").sum())
    return {"n_mature": n, "win_rate": w / n * 100, "stop_rate": s / n * 100, "ev": (w * 5 - s * 8) / n}


def close(a, b, tol=1e-10):
    return abs(float(a) - float(b)) <= tol


def main() -> int:
    summary = json.loads((OUT / "v02_summary.json").read_text(encoding="utf-8"))
    selected = summary["selected"]
    grid = pd.read_csv(OUT / "v02_grid_all.csv.gz")
    features = pd.read_csv(OUT / "v02_feature_matrix.csv.gz", dtype={"code": str, "d": str})
    signals = pd.read_csv(OUT / "signals_all_codes.csv.gz", dtype={"code": str, "d": str})
    shadow = pd.read_csv(OUT / "shadow_event_audit.csv", dtype={"code": str, "d": str})
    calibration_audit = pd.read_csv(OUT / "v02_shadow_calibration_rows.csv", dtype={"code": str, "d": str})
    checks = {}
    checks["protocol_hash"] = sha256(HERE / "V02_CALIBRATION_PROTOCOL.md") == summary["protocol_sha256"]
    checks["grid_count_50764"] = len(grid) == 50764
    checks["grid_has_no_global_metrics"] = not any("global" in col or "2024" in col or "2025" in col or "2026" in col for col in grid.columns)
    order = grid[grid.eligible.astype(bool)].sort_values(
        ["check_stops_removed", "check_winners_removed", "all_stops_removed", "all_winners_removed",
         "check_ev_after", "active_families", "k", "candidate_id"],
        ascending=[False, True, False, True, False, True, False, True],
    )
    checks["selected_is_exact_calibration_rank1"] = not order.empty and order.iloc[0].candidate_id == selected["candidate_id"]
    shadow_cool = shadow.shadow_cooldown.fillna(False).astype(bool)
    cal = shadow[(~shadow_cool) & shadow.judge.isin(["✓", "?", "✗"]) & shadow.outcome.isin(["win", "stop", "timeout"])]
    cal = cal.merge(features, on=["code", "d"], how="inner", validate="one_to_one")
    checks["calibration_sizes"] = len(cal) == 61 and len(cal[cal.judge == "✓"]) == 25
    required_audit_columns = {"v02_trigger_count", "v02_reject", "v02_keep", "v02_candidate_id"}
    checks["calibration_has_selected_row_audit"] = (
        required_audit_columns.issubset(calibration_audit.columns)
        and calibration_audit.v02_candidate_id.nunique() == 1
        and calibration_audit.v02_candidate_id.iloc[0] == selected["candidate_id"]
        and (calibration_audit.v02_keep.astype(bool) == ~calibration_audit.v02_reject.astype(bool)).all()
        and (calibration_audit.v02_reject.astype(bool) == decide(calibration_audit, selected)).all()
    )
    enriched = signals.merge(features, on=["code", "d"], how="left", validate="one_to_one")
    main = enriched[enriched.aggregate_eligible.fillna(False).astype(bool)]
    cool = main[~main.cooldown5.fillna(False).astype(bool)]
    pre = cool[cool.d < "2026-07-13"]
    got = basic(pre, decide(pre, selected))
    expected = summary["global"]["cooldown_pre_shadow"]["after"]
    checks["pre_shadow_headline_recomputes"] = all(close(got[key], expected[key]) for key in got)
    checks["selected_catches_at_least_one_check_stop"] = int((decide(cal, selected) & cal.judge.eq("✓") & cal.outcome.eq("stop")).sum()) >= 1
    checks = {key: bool(value) for key, value in checks.items()}
    result = {"schema": "bottom-agent15-v02-verification/v1", "passed": all(checks.values()), "checks": checks}
    (OUT / "v02_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
