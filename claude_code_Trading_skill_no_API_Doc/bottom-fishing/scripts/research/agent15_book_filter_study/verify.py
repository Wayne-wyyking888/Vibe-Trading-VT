# -*- coding: utf-8 -*-
"""Independent mechanical checks for Agent 1.5 outputs; does not import research.py."""
from __future__ import annotations

import hashlib
import json
import pathlib

import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parents[2]
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def basic(df: pd.DataFrame) -> dict:
    mature = df[df.outcome != "open"]
    n = len(mature)
    wins = int((mature.outcome == "win").sum())
    stops = int((mature.outcome == "stop").sum())
    return {
        "n_mature": n,
        "win_rate": wins / n * 100 if n else None,
        "stop_rate": stops / n * 100 if n else None,
        "ev": (wins * 5 - stops * 8) / n if n else None,
    }


def close(a, b, tol=1e-9) -> bool:
    return a == b if a is None or b is None else abs(float(a) - float(b)) <= tol


def main() -> int:
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    fetch = json.loads((OUT / "fetch_audit.json").read_text(encoding="utf-8"))
    raw = pd.read_csv(OUT / "signals_raw_qualified.csv.gz", dtype={"code": str, "d": str})
    cool = pd.read_csv(OUT / "signals_cooldown5.csv.gz", dtype={"code": str, "d": str})
    shadow = pd.read_csv(OUT / "shadow_event_audit.csv", dtype={"code": str, "d": str})
    checks = {}
    checks["engine_hash_frozen"] = sha256(SKILL / "bottom_fishing.py") == fetch["engine_sha256"] == summary["engine"]["sha256"]
    checks["prereg_hash_frozen"] = sha256(HERE / "PRE_REGISTRATION.md") == fetch["pre_registration_sha256"] == summary["agent15"]["pre_registration_sha256"]
    checks["research_source_hash_frozen"] = sha256(HERE / "research.py") == fetch["research_source_sha256"]
    checks["qfq_overlap_exact"] = fetch["overlap_rows_checked"] > 0 and fetch["overlap_mismatch_cells"] == 0
    checks["requested_start_obeyed"] = raw.d.min() >= "2024-01-01"
    checks["effective_end_obeyed"] = raw.d.max() <= summary["data_window"]["effective_end"] <= "2026-09-11"
    checks["outcomes_valid"] = set(raw.outcome.unique()).issubset({"win", "stop", "timeout", "open"})
    checks["cool_is_exact_raw_subset"] = set(zip(cool.code, cool.d)) == set(zip(raw.loc[~raw.cooldown5.astype(bool), "code"], raw.loc[~raw.cooldown5.astype(bool), "d"]))
    flags = [
        "f1_down_volume_dominance_20", "f2_bullish_bars_gravity_down_20", "f3_volume_without_progress_10",
        "f4_upthrust_failure_10", "f5_rebound_failure_10",
    ]
    red = raw[flags].astype(bool).sum(axis=1)
    expected_primary = raw[flags[1]].astype(bool) | raw[flags[3]].astype(bool) | (red >= 2)
    expected_primary &= ~raw.a15_unknown.astype(bool)
    checks["primary_formula_exact"] = expected_primary.equals(raw.a15_primary_reject.astype(bool))
    cooldown_ok = True
    for _, group in raw.sort_values(["code", "bar_pos"]).groupby("code"):
        last = None
        for _, row in group.iterrows():
            expected = last is not None and int(row.bar_pos) - last <= 5
            cooldown_ok &= bool(row.cooldown5) == expected
            last = int(row.bar_pos)
    checks["cooldown_refreshes_on_every_raw_signal"] = cooldown_ok
    stats_ok = True
    for key, frame in (("raw_qualified", raw), ("cooldown5", cool)):
        expected = summary["primary"][key]["full"]
        for label, subset in (("before", frame), ("after", frame[~frame.a15_primary_reject.astype(bool)])):
            got = basic(subset)
            for metric in ("n_mature", "win_rate", "stop_rate", "ev"):
                stats_ok &= close(got[metric], expected[label][metric])
    checks["headline_stats_recompute"] = stats_ok
    checked = shadow[shadow.logged_outcome.notna() & shadow.a15_feature_available.astype(bool)]
    checks["logged_outcomes_reproduce"] = len(checked) > 0 and checked.logged_outcome.eq(checked.outcome).all()
    target = shadow[(~shadow.shadow_cooldown.astype(bool)) & shadow.code.isin(["300450", "002709"])]
    checks["named_cases_present"] = {"300450", "002709"}.issubset(set(target.code))
    checks["source_manifest_exists"] = (OUT / "SOURCE_MANIFEST.json").exists()
    checks = {key: bool(value) for key, value in checks.items()}
    passed = bool(all(checks.values()))
    result = {"schema": "bottom-agent15-independent-verification/v1", "passed": passed, "checks": checks}
    (OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
