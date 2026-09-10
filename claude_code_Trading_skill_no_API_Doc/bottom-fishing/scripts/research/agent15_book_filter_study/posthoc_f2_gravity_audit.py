# -*- coding: utf-8 -*-
"""Post-hoc F2 gravity sensitivity prompted by 300450 on 2026-08-27.

This script never rewrites v0.1 outputs.  It reads frozen v0.1 signal files and
writes separately named post-hoc artifacts.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pandas as pd


HERE = pathlib.Path(__file__).resolve().parent
OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
PLAN = HERE / "POSTHOC_20260827_F2_PLAN.md"
THRESHOLDS = (-0.25, -0.30, -0.35, -0.40, -0.45, -0.50, -0.60, -0.75, -1.00, -1.25)
PRIMARY_THRESHOLD = -0.40
FLAG_COLS = (
    "f1_down_volume_dominance_20",
    "f3_volume_without_progress_10",
    "f4_upthrust_failure_10",
    "f5_rebound_failure_10",
)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def dump(path: pathlib.Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def bool_col(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame[col].fillna(False).astype(bool)


def decision(frame: pd.DataFrame, gravity_threshold: float) -> pd.Series:
    f2 = (frame.f2_bullish_bars >= 11) & (frame.f2_gravity_atr <= gravity_threshold)
    red = f2.astype(int)
    for col in FLAG_COLS:
        red += bool_col(frame, col).astype(int)
    reject = f2 | bool_col(frame, "f4_upthrust_failure_10") | (red >= 2)
    return reject & ~bool_col(frame, "a15_unknown")


def metrics(frame: pd.DataFrame, reject: pd.Series | None = None) -> dict:
    selected = frame if reject is None else frame.loc[~reject]
    mature = selected[selected.outcome != "open"]
    n = len(mature)
    counts = {key: int((selected.outcome == key).sum()) for key in ("win", "stop", "timeout", "open")}
    wins, stops = counts["win"], counts["stop"]
    return {
        "n_total": len(selected),
        "n_mature": n,
        **counts,
        "win_rate": wins / n * 100 if n else None,
        "stop_rate": stops / n * 100 if n else None,
        "ev": (wins * 5 - stops * 8) / n if n else None,
    }


def compare(frame: pd.DataFrame, reject: pd.Series) -> dict:
    before = metrics(frame)
    after = metrics(frame, reject)
    removed = frame.loc[reject]
    return {
        "before": before,
        "after": after,
        "removed": metrics(removed),
        "retained_mature_pct": after["n_mature"] / before["n_mature"] * 100,
        "removed_winner_share_pct": int((removed.outcome == "win").sum()) / before["win"] * 100,
        "delta_win_rate_pp": after["win_rate"] - before["win_rate"],
        "delta_stop_rate_pp": after["stop_rate"] - before["stop_rate"],
        "delta_ev_pp": after["ev"] - before["ev"],
    }


def month_bootstrap(frame: pd.DataFrame, gravity_threshold: float, reps: int = 2000) -> dict:
    mature = frame[frame.outcome != "open"].copy()
    mature["month"] = mature.d.str[:7]
    months = sorted(mature.month.unique())
    groups = {month: mature[mature.month == month] for month in months}
    rng = np.random.default_rng(20260827)
    deltas = []
    for _ in range(reps):
        sample = pd.concat([groups[months[i]] for i in rng.integers(0, len(months), len(months))], ignore_index=True)
        comp = compare(sample, decision(sample, gravity_threshold))
        deltas.append((comp["delta_win_rate_pp"], comp["delta_stop_rate_pp"], comp["delta_ev_pp"]))
    array = np.asarray(deltas)
    return {
        "reps": reps,
        "months": len(months),
        "delta_win_rate_ci95": np.percentile(array[:, 0], [2.5, 97.5]).tolist(),
        "delta_stop_rate_ci95": np.percentile(array[:, 1], [2.5, 97.5]).tolist(),
        "delta_ev_ci95": np.percentile(array[:, 2], [2.5, 97.5]).tolist(),
    }


def stop_audit(shadow: pd.DataFrame) -> pd.DataFrame:
    cooldown = shadow.shadow_cooldown.fillna(False).astype(bool)
    stops = shadow[(~cooldown) & shadow.judge.eq("✓") & shadow.outcome.eq("stop")].copy()
    stops["v01_reject"] = decision(stops, -0.75)
    stops["posthoc_g040_reject"] = decision(stops, PRIMARY_THRESHOLD)
    keep = [
        "d", "code", "name", "hold_days", "score", "atr", "def_days", "idx_rsv",
        "a15_unknown", *FLAG_COLS,
        "f1_down_up_volume_ratio", "f2_bullish_bars", "f2_gravity_atr",
        "f3_peak_volume_ratio", "f3_efficiency", "f4_event_count", "f5_rebound", "f5_retrace",
        "a15_reasons", "v01_reject", "posthoc_g040_reject",
    ]
    return stops[keep].sort_values(["d", "code"]).reset_index(drop=True)


def render(summary: dict, stops: pd.DataFrame) -> str:
    def p(value):
        return f"{value:.3f}%"

    lines = [
        "# F2 重心 -0.40 ATR 事后全局审计",
        "",
        "**这是已知 2026-08-27 先导智能结果后的 post-hoc 检查，不是新预注册。**",
        "",
        "## 全局 N=5",
        "",
        "|规则|成熟N|W/S/T/O|胜率|雷率|EV|保留率|误杀赢家|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    base = summary["cooldown5"]["baseline"]
    lines.append(f"|无1.5|{base['n_mature']}|{base['win']}/{base['stop']}/{base['timeout']}/{base['open']}|{p(base['win_rate'])}|{p(base['stop_rate'])}|{p(base['ev'])}|100.000%|0.000%|")
    for key, label in (("v01", "v0.1 · -0.75"), ("posthoc_g040", "post-hoc · -0.40")):
        comp = summary["cooldown5"][key]
        item = comp["after"]
        lines.append(
            f"|{label}|{item['n_mature']}|{item['win']}/{item['stop']}/{item['timeout']}/{item['open']}|"
            f"{p(item['win_rate'])}|{p(item['stop_rate'])}|{p(item['ev'])}|{p(comp['retained_mature_pct'])}|{p(comp['removed_winner_share_pct'])}|"
        )
    lines += [
        "",
        "## 影子日志未冷却 ✓ 子集",
        "",
        "|规则|成熟N|W/S/T/O|胜率|雷率|EV|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("baseline", "无1.5"), ("v01", "v0.1 · -0.75"), ("posthoc_g040", "post-hoc · -0.40")):
        value = summary["shadow_check_subset"][key]
        item = value if key == "baseline" else value["after"]
        lines.append(
            f"|{label}|{item['n_mature']}|{item['win']}/{item['stop']}/{item['timeout']}/{item['open']}|"
            f"{p(item['win_rate'])}|{p(item['stop_rate'])}|{p(item['ev'])}|"
        )
    lines += [
        "",
        "## -0.40 分年增量（相对无1.5）",
        "",
        "|年份|成熟N→过滤后N|胜率变化|雷率变化|EV变化|",
        "|---|---:|---:|---:|---:|",
    ]
    for year, comp in summary["posthoc_g040_by_year"].items():
        lines.append(f"|{year}|{comp['before']['n_mature']}→{comp['after']['n_mature']}|{p(comp['delta_win_rate_pp'])}|{p(comp['delta_stop_rate_pp'])}|{p(comp['delta_ev_pp'])}|")
    boot = summary["posthoc_g040_month_bootstrap"]
    lines += [
        "",
        f"月度块自助95%：胜率 `{boot['delta_win_rate_ci95']}`；雷率 `{boot['delta_stop_rate_ci95']}`；EV `{boot['delta_ev_ci95']}`。",
        "",
        "## 全部未冷却 ✓ stop",
        "",
        "|日期|代码|名称|持有日|阳线数|重心ATR|v0.1|post-hoc -0.40|",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for _, row in stops.iterrows():
        lines.append(
            f"|{row.d}|{row.code}|{row['name']}|{int(row.hold_days)}|{int(row.f2_bullish_bars)}|{row.f2_gravity_atr:.3f}|"
            f"{'reject' if row.v01_reject else 'keep'}|{'reject' if row.posthoc_g040_reject else 'keep'}|"
        )
    lines += [
        "",
        "完整阈值邻域见 `posthoc_f2_gravity_grid.csv`。不得从邻域中事后挑最优值替代 -0.40。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    raw_path = OUT / "signals_raw_qualified.csv.gz"
    cool_path = OUT / "signals_cooldown5.csv.gz"
    shadow_path = OUT / "shadow_event_audit.csv"
    raw = pd.read_csv(raw_path, dtype={"code": str, "d": str})
    cool = pd.read_csv(cool_path, dtype={"code": str, "d": str})
    shadow = pd.read_csv(shadow_path, dtype={"code": str, "d": str})

    # Required reproduction: the exact old threshold must equal stored v0.1 decisions.
    assert decision(cool, -0.75).equals(bool_col(cool, "a15_primary_reject"))
    assert decision(raw, -0.75).equals(bool_col(raw, "a15_primary_reject"))
    stops = stop_audit(shadow)
    assert len(stops) == 5
    target = stops[(stops.code == "300450") & (stops.d == "2026-08-27")]
    assert len(target) == 1 and bool(target.iloc[0].posthoc_g040_reject)

    grid_rows = []
    for threshold in THRESHOLDS:
        reject = decision(cool, threshold)
        comp = compare(cool, reject)
        caught = int(decision(stops, threshold).sum())
        grid_rows.append({
            "f2_gravity_threshold": threshold,
            "f2_bullish_min": 11,
            "mature_after": comp["after"]["n_mature"],
            "wins_after": comp["after"]["win"],
            "stops_after": comp["after"]["stop"],
            "timeouts_after": comp["after"]["timeout"],
            "open_after": comp["after"]["open"],
            "win_rate_after": comp["after"]["win_rate"],
            "stop_rate_after": comp["after"]["stop_rate"],
            "ev_after": comp["after"]["ev"],
            "retained_mature_pct": comp["retained_mature_pct"],
            "removed_winner_share_pct": comp["removed_winner_share_pct"],
            "delta_win_rate_pp": comp["delta_win_rate_pp"],
            "delta_stop_rate_pp": comp["delta_stop_rate_pp"],
            "delta_ev_pp": comp["delta_ev_pp"],
            "caught_known_check_stops": caught,
            "known_check_stops_total": len(stops),
        })
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(OUT / "posthoc_f2_gravity_grid.csv", index=False, encoding="utf-8-sig")
    stops.to_csv(OUT / "shadow_check_stops_v01_posthoc.csv", index=False, encoding="utf-8-sig")

    posthoc_cool = compare(cool, decision(cool, PRIMARY_THRESHOLD))
    posthoc_raw = compare(raw, decision(raw, PRIMARY_THRESHOLD))
    shadow_cooldown = shadow.shadow_cooldown.fillna(False).astype(bool)
    check_subset = shadow[(~shadow_cooldown) & shadow.judge.eq("✓")].copy()
    summary = {
        "schema": "bottom-agent15-posthoc-f2-gravity/v1",
        "status": "post_hoc_not_pre_registered",
        "known_target": {"code": "300450", "d": "2026-08-27", "observed_gravity_atr": float(target.iloc[0].f2_gravity_atr)},
        "change": "F2 gravity <= -0.75 ATR to <= -0.40 ATR; bullish bars >=11 and everything else unchanged",
        "source_hashes": {
            str(path): sha256(path) for path in (PLAN, raw_path, cool_path, shadow_path, HERE / "posthoc_f2_gravity_audit.py")
        },
        "raw_qualified": {
            "baseline": metrics(raw),
            "v01": compare(raw, decision(raw, -0.75)),
            "posthoc_g040": posthoc_raw,
        },
        "cooldown5": {
            "baseline": metrics(cool),
            "v01": compare(cool, decision(cool, -0.75)),
            "posthoc_g040": posthoc_cool,
        },
        "posthoc_g040_by_year": {
            year: compare(cool[cool.d.str.startswith(year)], decision(cool[cool.d.str.startswith(year)], PRIMARY_THRESHOLD))
            for year in ("2024", "2025", "2026")
        },
        "shadow_check_subset": {
            "baseline": metrics(check_subset),
            "v01": compare(check_subset, decision(check_subset, -0.75)),
            "posthoc_g040": compare(check_subset, decision(check_subset, PRIMARY_THRESHOLD)),
        },
        "posthoc_g040_month_bootstrap": month_bootstrap(cool, PRIMARY_THRESHOLD),
        "known_check_stop_audit": {
            "total": len(stops),
            "v01_caught": int(stops.v01_reject.sum()),
            "posthoc_g040_caught": int(stops.posthoc_g040_reject.sum()),
        },
        "grid_file": str(OUT / "posthoc_f2_gravity_grid.csv"),
        "limitations": [
            "-0.40 was selected after observing 300450 on 2026-08-27",
            "2026 and all five check-mark stops are contaminated audit data, not holdout",
            "current amount-ranked universe is not point-in-time and has survivorship bias",
        ],
    }
    dump(OUT / "posthoc_f2_gravity_summary.json", summary)
    (OUT / "posthoc_f2_gravity_summary.md").write_text(render(summary, stops), encoding="utf-8")
    verification = {
        "schema": "bottom-agent15-posthoc-verification/v1",
        "passed": True,
        "checks": {
            "v01_cooldown_reproduced_exactly": True,
            "v01_raw_reproduced_exactly": True,
            "all_five_known_check_stops_audited": True,
            "posthoc_g040_catches_300450_20260827": True,
            "posthoc_status_explicit": summary["status"] == "post_hoc_not_pre_registered",
        },
    }
    dump(OUT / "posthoc_f2_verification.json", verification)
    print(render(summary, stops))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
