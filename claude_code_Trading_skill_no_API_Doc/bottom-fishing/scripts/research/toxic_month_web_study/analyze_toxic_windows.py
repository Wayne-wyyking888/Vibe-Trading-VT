# -*- coding: utf-8 -*-
"""复算 bottom-fishing 毒月的真实 5 交易日集中窗口（隔离研究）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


DATA_ROOT = Path(r"C:\Trading_analysis\research\bottom_ml")
MANIFEST = Path(
    r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc"
    r"\bottom-fishing\scripts\research\bottom_ml\SOURCE_MANIFEST.json"
)
OUTPUT_ROOT = Path(
    r"C:\Trading_analysis\research\bottom_toxic_month_web_study\output"
)

TH_TOTAL = 18.0
TH_STOCK = 15.0
TH_ATR = 4.0
MAX_HOLD = 20
TARGET_PCT = 5.0
STOP_PCT = -8.0
TOXIC_MIN_N = 15
TOXIC_STOP_RATE = 0.30
BORDER_MONTH = "2024-07"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().upper()


def verify_inputs() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks: dict[str, Any] = {}
    for name, spec in manifest["external_datasets"].items():
        path = DATA_ROOT / name
        actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        actual["ok"] = (
            actual["bytes"] == spec["bytes"]
            and actual["sha256"] == spec["sha256"]
        )
        checks[name] = actual
        if not actual["ok"]:
            raise RuntimeError(f"输入数据 hash/size 不匹配：{name}")
    return checks


def qualify(panel: pd.DataFrame) -> pd.DataFrame:
    mask = (
        (
            (panel["mkt_def"] & (panel["score"] >= TH_TOTAL))
            | (~panel["mkt_def"] & (panel["stock_score"] >= TH_STOCK))
        )
        & (panel["atr"] <= TH_ATR)
    )
    out = panel.loc[mask].copy()
    out["d"] = out["d"].astype(str)
    out["month"] = out["d"].str[:7]
    return out.sort_values(["d", "code"]).reset_index(drop=True)


def attach_path_dates(
    signals: pd.DataFrame, klines: pd.DataFrame
) -> tuple[pd.DataFrame, int]:
    work = signals.copy()
    work["entry_date"] = ""
    work["resolve_date"] = ""
    work["derived_outcome"] = ""
    mismatch = 0

    for code, rows in work.groupby("code"):
        k = (
            klines.loc[klines["code"] == code]
            .sort_values("d")
            .reset_index(drop=True)
        )
        loc = {str(d): i for i, d in enumerate(k["d"].astype(str))}
        o = k["o"].to_numpy(float)
        c = k["c"].to_numpy(float)
        h = k["h"].to_numpy(float)
        low = k["l"].to_numpy(float)
        dates = k["d"].astype(str).to_numpy()

        for row_i, row in rows.iterrows():
            signal_i = loc[str(row["d"])]
            entry_i = signal_i + 1
            entry = o[entry_i]
            stop = entry * (1 + STOP_PCT / 100)
            target = entry * (1 + TARGET_PCT / 100)
            outcome = ""
            resolve_i: int | None = None

            if c[entry_i] <= stop:
                outcome, resolve_i = "stop", entry_i
            else:
                end_i = min(entry_i + MAX_HOLD, len(k) - 1)
                for i in range(entry_i + 1, end_i + 1):
                    if low[i] <= stop:
                        outcome, resolve_i = "stop", i
                        break
                    if h[i] >= target:
                        outcome, resolve_i = "win", i
                        break
                if not outcome and entry_i + MAX_HOLD <= len(k) - 1:
                    outcome, resolve_i = "timeout", entry_i + MAX_HOLD

            if not outcome or resolve_i is None:
                raise RuntimeError(f"成熟样本未结算：{code} {row['d']}")
            work.at[row_i, "entry_date"] = dates[entry_i]
            work.at[row_i, "resolve_date"] = dates[resolve_i]
            work.at[row_i, "derived_outcome"] = outcome
            mismatch += int(outcome != row["outcome"])
    return work, mismatch


def densest_five_sessions(
    stops: pd.DataFrame, date_col: str, calendar: list[str]
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for i in range(len(calendar) - 4):
        sessions = calendar[i : i + 5]
        selected = stops[stops[date_col].isin(sessions)]
        candidate = {
            "count": int(len(selected)),
            "window_start": sessions[0],
            "window_end": sessions[-1],
            "event_date_start": (
                str(selected[date_col].min()) if len(selected) else ""
            ),
            "event_date_end": (
                str(selected[date_col].max()) if len(selected) else ""
            ),
        }
        if best is None or candidate["count"] > best["count"]:
            best = candidate
    assert best is not None
    return best


def compounded_return(index_panel: pd.DataFrame, start: str, end: str) -> float:
    x = index_panel[index_panel["d"].astype(str).between(start, end)]
    return float(((1 + x["idx_chg1"].astype(float) / 100).prod() - 1) * 100)


def concentration_rows(
    stops: pd.DataFrame,
    month: str,
    window_type: str,
    date_col: str,
    window: dict[str, Any],
) -> list[dict[str, Any]]:
    selected = stops[
        stops[date_col].between(window["window_start"], window["window_end"])
    ]
    industry_counts = selected["industry"].value_counts()
    code_counts = selected["code"].value_counts()
    hhi = float((industry_counts / len(selected)).pow(2).sum()) if len(selected) else 0
    rows = []
    for rank, (industry, count) in enumerate(industry_counts.head(10).items(), 1):
        rows.append(
            {
                "month": month,
                "window_type": window_type,
                "rank": rank,
                "industry": industry,
                "industry_stop_rows": int(count),
                "window_stop_rows": int(len(selected)),
                "industry_share": float(count / len(selected)),
                "industry_hhi": hhi,
                "unique_codes": int(selected["code"].nunique()),
                "top_code": str(code_counts.index[0]) if len(code_counts) else "",
                "top_code_rows": int(code_counts.iloc[0]) if len(code_counts) else 0,
            }
        )
    return rows


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    checks = verify_inputs()
    panel = pd.read_parquet(DATA_ROOT / "panel.parquet")
    index_panel = pd.read_parquet(DATA_ROOT / "index_panel.parquet")
    klines = pd.read_parquet(DATA_ROOT / "klines.parquet")
    calendar = index_panel["d"].astype(str).tolist()
    mature_cutoff = calendar[-(MAX_HOLD + 2)]

    signals = qualify(panel)
    signals = signals[signals["d"] <= mature_cutoff].copy().reset_index(drop=True)
    signals, mismatch = attach_path_dates(signals, klines)
    if mismatch:
        raise RuntimeError(f"路径标签复算不一致：{mismatch}")

    month_stats = (
        signals.groupby("month")["outcome"]
        .agg(
            n="size",
            stops=lambda x: int((x == "stop").sum()),
            wins=lambda x: int((x == "win").sum()),
            timeouts=lambda x: int((x == "timeout").sum()),
        )
        .reset_index()
    )
    month_stats["stop_rate"] = month_stats["stops"] / month_stats["n"]
    strict = month_stats[
        (month_stats["n"] >= TOXIC_MIN_N)
        & (month_stats["stop_rate"] >= TOXIC_STOP_RATE)
    ]["month"].tolist()
    study_months = sorted(set(strict + [BORDER_MONTH]))

    metric_rows: list[dict[str, Any]] = []
    industry_rows: list[dict[str, Any]] = []
    for month in study_months:
        stat = month_stats.loc[month_stats["month"] == month].iloc[0]
        stops = signals[
            (signals["month"] == month) & (signals["outcome"] == "stop")
        ].copy()
        signal_window = densest_five_sessions(stops, "d", calendar)
        resolve_window = densest_five_sessions(stops, "resolve_date", calendar)
        first_signal = signal_window["window_start"]
        last_resolve = resolve_window["window_end"]
        code_counts = stops["code"].value_counts()
        row = {
            "month": month,
            "status": "strict_toxic" if month in strict else "legacy_border",
            "n": int(stat["n"]),
            "stops": int(stat["stops"]),
            "wins": int(stat["wins"]),
            "timeouts": int(stat["timeouts"]),
            "stop_rate": float(stat["stop_rate"]),
            "unique_stop_codes": int(stops["code"].nunique()),
            "repeat_stop_row_share": float(1 - stops["code"].nunique() / len(stops)),
            "top_stop_code": str(code_counts.index[0]),
            "top_stop_code_rows": int(code_counts.iloc[0]),
            "signal_window_start": signal_window["window_start"],
            "signal_window_end": signal_window["window_end"],
            "signal_window_stops": signal_window["count"],
            "signal_window_stop_share": float(signal_window["count"] / len(stops)),
            "signal_window_idx_return_pct": compounded_return(
                index_panel, signal_window["window_start"], signal_window["window_end"]
            ),
            "resolve_window_start": resolve_window["window_start"],
            "resolve_window_end": resolve_window["window_end"],
            "resolve_window_stops": resolve_window["count"],
            "resolve_window_stop_share": float(resolve_window["count"] / len(stops)),
            "resolve_window_idx_return_pct": compounded_return(
                index_panel, resolve_window["window_start"], resolve_window["window_end"]
            ),
            "signal_to_resolve_idx_return_pct": compounded_return(
                index_panel, first_signal, last_resolve
            ),
        }
        metric_rows.append(row)
        industry_rows += concentration_rows(
            stops, month, "signal", "d", signal_window
        )
        industry_rows += concentration_rows(
            stops, month, "resolve", "resolve_date", resolve_window
        )

    metrics = pd.DataFrame(metric_rows)
    industries = pd.DataFrame(industry_rows)
    metrics.to_csv(
        OUTPUT_ROOT / "toxic_window_metrics.csv", index=False, encoding="utf-8-sig"
    )
    industries.to_csv(
        OUTPUT_ROOT / "industry_concentration.csv",
        index=False,
        encoding="utf-8-sig",
    )
    metadata = {
        "schema": "bottom-toxic-month-web-study/v1",
        "mature_signal_cutoff": mature_cutoff,
        "strict_toxic_months": strict,
        "border_month": BORDER_MONTH,
        "input_checks": checks,
        "production_workflow_modified": False,
    }
    (OUTPUT_ROOT / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"wrote={OUTPUT_ROOT} strict={len(strict)} study={len(study_months)} "
        f"mature_cutoff={mature_cutoff}"
    )


if __name__ == "__main__":
    main()
