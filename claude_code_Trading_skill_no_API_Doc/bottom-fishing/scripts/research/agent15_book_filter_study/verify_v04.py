# -*- coding: utf-8 -*-
"""Independent mechanical checks for Agent 1.5 v0.4 outputs.

This file deliberately does not import v04_rebuild.py.
"""
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
SCRIPT = HERE / "v04_rebuild.py"
SIGNALS = OUT / "signals_cooldown5.csv.gz"
ROWS = OUT / "v04_row_audit.csv.gz"
GRID = OUT / "v04_development_grid.csv"
SUMMARY = OUT / "v04_summary.json"
BOOK_FLAGS = ("B1", "B2", "B3", "B4", "B5", "B6")
EXEMPTIONS = ("E1", "E2")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def close(left, right, tolerance: float = 1e-10) -> bool:
    if left is None or right is None:
        return left is right
    return bool(np.isclose(float(left), float(right), atol=tolerance, rtol=0.0))


def stats(frame: pd.DataFrame) -> dict:
    mature = frame[frame.outcome.isin(["win", "stop", "timeout"])]
    w = int(mature.outcome.eq("win").sum())
    s = int(mature.outcome.eq("stop").sum())
    t = int(mature.outcome.eq("timeout").sum())
    n = len(mature)
    return {
        "n": n, "w": w, "s": s, "t": t,
        "win_rate": w / n * 100.0 if n else None,
        "stop_rate": s / n * 100.0 if n else None,
        "ev": (5.0 * w - 8.0 * s) / n if n else None,
    }


def comparison(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before = stats(frame)
    after = stats(frame.loc[~reject])
    removed = stats(frame.loc[reject])
    return {
        "retained": after["n"] / before["n"] * 100.0,
        "stop_capture": removed["s"] / before["s"] * 100.0,
        "winner_removed": removed["w"] / before["w"] * 100.0,
        "d_win": after["win_rate"] - before["win_rate"],
        "d_stop": after["stop_rate"] - before["stop_rate"],
        "d_ev": after["ev"] - before["ev"],
    }


def candidate_list() -> list[dict]:
    values = [{"id": "KEEP_ALL", "kind": "null", "members": []}]
    values.extend(
        {"id": f"{left}_AND_{right}", "kind": "pair", "members": [left, right]}
        for left, right in itertools.combinations(BOOK_FLAGS, 2)
    )
    values.extend([
        {"id": "COUNT_GE_2", "kind": "count", "members": list(BOOK_FLAGS), "k": 2},
        {"id": "COUNT_GE_3", "kind": "count", "members": list(BOOK_FLAGS), "k": 3},
    ])
    return values


def reject_for(frame: pd.DataFrame, candidate: dict) -> pd.Series:
    known = ~frame.v04_unknown.astype(bool)
    no_exemption = ~frame[list(EXEMPTIONS)].astype(bool).any(axis=1)
    if candidate["kind"] == "null":
        return pd.Series(False, index=frame.index)
    if candidate["kind"] == "pair":
        left, right = candidate["members"]
        return frame[left].astype(bool) & frame[right].astype(bool) & known & no_exemption
    count = frame[list(BOOK_FLAGS)].astype(bool).sum(axis=1)
    return count.ge(candidate["k"]) & known & no_exemption


def run() -> dict:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    rows = pd.read_csv(ROWS, dtype={"code": str, "d": str})
    rows["code"] = rows.code.str.zfill(6)
    signals = pd.read_csv(SIGNALS, dtype={"code": str, "d": str})
    signals["code"] = signals.code.str.zfill(6)
    grid = pd.read_csv(GRID)
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add("protocol_hash", summary["protocol_sha256"] == sha256(PROTOCOL), sha256(PROTOCOL))
    add(
        "script_hash",
        summary["source_hashes"][str(SCRIPT)] == sha256(SCRIPT),
        sha256(SCRIPT),
    )
    source_hash_ok = all(
        summary["source_hashes"][str(path)] == sha256(path)
        for path in (SIGNALS, OUT / "klines.csv.gz", OUT / "index_399006.csv.gz")
    )
    add("frozen_source_hashes", source_hash_ok, "signals/klines/index")
    add("row_keys_unique", not rows.duplicated(["code", "d"]).any(), f"rows={len(rows)}")
    same_keys = set(map(tuple, rows[["code", "d"]].to_numpy())) == set(map(tuple, signals[["code", "d"]].to_numpy()))
    add("row_keys_match_n5_source", same_keys, f"rows={len(rows)} signals={len(signals)}")
    n5_ok = not signals.cooldown5.fillna(False).astype(bool).any() and signals.aggregate_eligible.fillna(False).astype(bool).all()
    add("source_is_uncooled_primary_line", n5_ok, "cooldown5=false and aggregate_eligible=true")

    split_sizes = rows.v04_split.value_counts().to_dict()
    split_ok = split_sizes.get("development") == 1703 and split_sizes.get("embargo") == 67 and split_sizes.get("locked_audit") == 92
    add("purged_split_sizes", split_ok, str(split_sizes))
    audit = rows[rows.v04_split.eq("locked_audit")]
    mature_audit = int(audit.outcome.isin(["win", "stop", "timeout"]).sum())
    open_audit = int(audit.outcome.eq("open").sum())
    add("audit_maturity_counts", mature_audit == 67 and open_audit == 25, f"mature={mature_audit} open={open_audit}")

    primitive_ok = (
        rows.B1.eq(rows.b1_platform_break_count.gt(0)).all()
        and rows.B2.eq(rows.b2_upthrust_count.gt(0)).all()
        and rows.B3.eq(
            rows.b3_ret5.le(-0.06)
            & rows.b3_bearish_count.ge(2)
            & rows.b3_bearish_volume_ratio.ge(1.20)
            & rows.b3_death_cross_count.gt(0)
        ).all()
        and rows.B4.eq(
            rows.b4_markup120.ge(0.30)
            & rows.b4_drawdown_from_peak.le(-0.20)
            & rows.b4_vwtp_gap_atr.le(-0.75)
            & rows.b4_overhead_volume_share.ge(0.65)
            & rows.b4_high_ratio.le(0.99)
            & rows.b4_low_ratio.le(0.99)
        ).all()
        and rows.B5.eq(rows.b5_relative20.le(-0.08) & rows.b5_stock_ret20.le(0.0)).all()
        and rows.E1.eq(rows.e1_recovered_trap_count.gt(0)).all()
        and rows.E2.eq(rows.e2_recovered_probe_count.gt(0)).all()
    )
    add("stored_primitives_rebuild_flags", primitive_ok, "B1-B5,E1,E2")

    candidates = candidate_list()
    add("candidate_count", len(candidates) == 18 and len(grid) == 18, f"expected=18 list={len(candidates)} grid={len(grid)}")
    development = rows[rows.v04_split.eq("development")]
    grid_ok = True
    eligible_count = 0
    for candidate in candidates:
        reject = reject_for(development, candidate)
        comp = comparison(development, reject)
        stored = grid[grid.candidate_id.eq(candidate["id"])]
        if len(stored) != 1:
            grid_ok = False
            continue
        stored = stored.iloc[0]
        for key, column in (
            ("retained", "retained_mature_pct"),
            ("stop_capture", "stop_capture_pct"),
            ("winner_removed", "winner_removed_pct"),
            ("d_win", "delta_win_rate_pp"),
            ("d_stop", "delta_stop_rate_pp"),
            ("d_ev", "delta_ev_pp"),
        ):
            grid_ok &= close(comp[key], stored[column])
        eligible_count += int(bool(stored.eligible))
    add("grid_metrics_recomputed", grid_ok, "18 candidates")
    add("no_non_null_eligible", eligible_count == 0, f"eligible={eligible_count}")

    selected_id = summary["selected"]["candidate"]["candidate_id"]
    add("null_selected", selected_id == "KEEP_ALL", selected_id)
    add("selected_row_decisions", not rows.v04_reject.astype(bool).any(), f"rejects={int(rows.v04_reject.sum())}")
    known = summary["known_check_stops"]
    add("known_cases_audited", len(known) == 5, f"cases={len(known)}")
    add("known_cases_not_used_as_success", all(not item["filtered"] for item in known), "filtered=0/5")

    result = {
        "schema": "bottom-agent15-v04-verification/v1",
        "passed": sum(item["passed"] for item in checks),
        "total": len(checks),
        "all_passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    (OUT / "v04_verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["all_passed"] else 1)

