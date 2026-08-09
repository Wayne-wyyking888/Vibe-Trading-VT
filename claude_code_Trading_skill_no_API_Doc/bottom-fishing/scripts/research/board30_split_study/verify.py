# -*- coding: utf-8 -*-
"""独立核验 board30_split_study 输出；不导入 research.py。"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pandas as pd


ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_board30_split_study")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def period(df: pd.DataFrame, name: str, bounds: dict) -> pd.DataFrame:
    if name == "discovery":
        return df[(df.d >= "2023-11-01") & (df.d <= bounds["discovery_end"])]
    if name == "validation":
        return df[(df.d >= "2025-01-01") & (df.d <= bounds["validation_end"])]
    if name == "holdout":
        return df[df.d >= "2026-01-01"]
    if name == "full":
        return df[df.d >= "2023-11-01"]
    raise KeyError(name)


def basic(df: pd.DataFrame) -> dict:
    n = len(df)
    wins = int((df.outcome == "win").sum())
    stops = int((df.outcome == "stop").sum())
    return {
        "n": n,
        "win_rate": wins / n * 100 if n else None,
        "stop_rate": stops / n * 100 if n else None,
        "ev": wins / n * 5 - stops / n * 8 if n else None,
    }


def close(a, b, tol=1e-10) -> bool:
    return a == b if a is None or b is None else abs(float(a) - float(b)) <= tol


def main() -> int:
    summary = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    freeze = json.loads((ROOT / "selection_freeze.json").read_text(encoding="utf-8"))
    grid = pd.read_csv(ROOT / "candidate_grid.csv")
    base = pd.read_csv(ROOT / "signals_unified_baseline.csv", dtype={"code": str})
    split = pd.read_csv(ROOT / "signals_split_selected.csv", dtype={"code": str})
    panel = pd.read_parquet(ROOT / "panel.parquet", columns=["code", "board", "d"])

    checks = {}
    checks["freeze_matches_summary"] = freeze["selected_candidate_id"] == summary["selected_candidate"]["candidate_id"]
    checks["grid_hash_matches_freeze"] = sha256(ROOT / "candidate_grid.csv") == freeze["candidate_grid_sha256"]
    checks["grid_has_no_holdout_or_full"] = not any(
        c.startswith("holdout_") or c.startswith("full_") for c in grid.columns
    )
    checks["only_60_00_30_panel"] = set(panel.code.astype(str).str[:2].unique()).issubset({"60", "00", "30"})
    checks["only_60_00_30_signals"] = all(
        set(df.code.astype(str).str.zfill(6).str[:2].unique()).issubset({"60", "00", "30"}) for df in (base, split)
    )
    checks["outcomes_valid"] = all(set(df.outcome.unique()).issubset({"win", "stop", "timeout"}) for df in (base, split))

    # 两份组合中 60+00 必须逐行不变。
    cols = ["code", "d", "bar_pos", "outcome", "hold_days", "entry"]
    b10 = base[base.board == "60+00"][cols].sort_values(["code", "d"]).reset_index(drop=True)
    s10 = split[split.board == "60+00"][cols].sort_values(["code", "d"]).reset_index(drop=True)
    checks["60_00_unchanged_exact"] = b10.equals(s10)

    # 最终保留信号之间不应存在 <=5 个本票交易日的间隔。
    def cooldown_ok(df: pd.DataFrame) -> bool:
        for _, group in df.sort_values(["code", "bar_pos"]).groupby("code"):
            if (group.bar_pos.diff().dropna() <= 5).any():
                return False
        return True
    checks["accepted_signals_obey_cooldown5"] = cooldown_ok(base) and cooldown_ok(split)

    bounds = summary["data_window"]
    mapping = {
        "baseline_30": lambda d: d[d.board == "30"],
        "selected_30": lambda d: d[d.board == "30"],
        "baseline_10group": lambda d: d[d.board == "60+00"],
        "baseline_all": lambda d: d,
        "split_all": lambda d: d,
    }
    source = {
        "baseline_30": base,
        "selected_30": split,
        "baseline_10group": base,
        "baseline_all": base,
        "split_all": split,
    }
    comparison_ok = True
    for p in ("discovery", "validation", "holdout", "full"):
        for label, select in mapping.items():
            got = basic(period(select(source[label]), p, bounds))
            expected = summary["comparisons"][p][label]
            for key in ("n", "win_rate", "stop_rate", "ev"):
                comparison_ok &= close(got[key], expected[key])
    checks["all_five_lines_recompute"] = bool(comparison_ok)

    audit = json.loads((ROOT / "fetch_audit.json").read_text(encoding="utf-8"))
    ext = audit.get("historical_extension", {})
    checks["qfq_overlap_exact"] = ext.get("overlap_rows", 0) > 0 and ext.get("overlap_mismatch_rows") == 0
    checks["data_reaches_2026_08_07"] = audit.get("raw_date_max") == "2026-08-07" and ext.get("merged_date_max") == "2026-08-07"
    checks["legacy_reproduction_pass"] = summary["legacy_reproduction"]["status"] == "pass"
    passed = all(checks.values())
    output = {"schema": "bottom-board30-independent-verification/v1", "passed": passed, "checks": checks}
    (ROOT / "verification.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

