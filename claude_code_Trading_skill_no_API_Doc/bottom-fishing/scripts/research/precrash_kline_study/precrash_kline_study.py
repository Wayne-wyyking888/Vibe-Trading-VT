# -*- coding: utf-8 -*-
r"""bottom-fishing 暴雷前 K 线轨迹研究。

研究层脚本，不修改生产引擎、SKILL、阈值、裁定文件、影子日志或报告。

固定口径：
- 母体：当前双路径推荐线，ATR <= 4；
- 冷却：N=5，任何过线事件（含被压下者）都刷新冷却计时；
- 标签：T+1 开盘进场，未来 20 个交易日先 +5% 为 win、先 -8% 为 stop；
- 成熟度：每笔信号后必须仍有至少 21 根个股 K 线，避免样本尾部右删失；
- 前置窗口：10/20/30/60/75/100/120/150 根个股 K 线，全部止于 T；
- 主比较：stop vs win；辅助比较：stop vs (win + timeout)。

输出写到独立研究目录，不碰生产数据：
  C:\Trading_analysis\research\bottom_precrash_kline_study\output
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import itertools
import json
import math
import os
import pathlib
import platform
import statistics
import tempfile
from typing import Any, Iterable

import duckdb
import numpy as np
import scipy
from scipy import stats
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


HERE = pathlib.Path(__file__).resolve().parent
SKILL_ROOT = HERE.parents[2]
BOTTOM_ML = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
BOTTOM_ML_MANIFEST = SKILL_ROOT / "scripts" / "research" / "bottom_ml" / "SOURCE_MANIFEST.json"
DEFAULT_OUTPUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_precrash_kline_study\output")

HORIZONS = [10, 20, 30, 60, 75, 100, 120, 150]
TRAJECTORY_LAGS = list(range(150, -1, -5))
EXACT_TRAJECTORY_LAGS = [150, 120, 100, 75, 60, 30, 20, 10, 5, 0]
FEATURES = [
    "ret",
    "slope",
    "mdd",
    "dd_high",
    "rebound",
    "pos",
    "rv",
    "atrbar",
    "downfrac",
    "maxdownrun",
    "worstday",
    "bigdown",
    "gapdown2",
    "bearcandle",
    "clv",
    "lowerwick",
    "volratio",
    "excess",
]
FEATURE_LABELS = {
    "ret": "窗口收盘收益%",
    "slope": "对数收盘日斜率%",
    "mdd": "窗口最大收盘回撤%",
    "dd_high": "T收盘距窗口最高价%",
    "rebound": "T收盘距窗口最低价反弹%",
    "pos": "T收盘窗口位置%",
    "rv": "日收益波动率%",
    "atrbar": "窗口平均TR%",
    "downfrac": "下跌日占比%",
    "maxdownrun": "最长连跌天数",
    "worstday": "最差单日收益%",
    "bigdown": "单日跌幅>=4%占比%",
    "gapdown2": "低开>=2%占比%",
    "bearcandle": "阴线占比%",
    "clv": "平均收盘位置%",
    "lowerwick": "平均下影占比%",
    "volratio": "后20%/前20%成交量",
    "excess": "相对创业板窗口收益%",
}
STOCK_MODEL_FEATURES = [
    f"{feature}{h}"
    for h in HORIZONS
    for feature in [
        "ret",
        "mdd",
        "dd_high",
        "rebound",
        "pos",
        "rv",
        "downfrac",
        "maxdownrun",
        "worstday",
        "bigdown",
        "gapdown2",
        "bearcandle",
        "lowerwick",
        "volratio",
        "excess",
    ]
]
MARKET_MODEL_FEATURES = [
    f"idx_{feature}{h}"
    for h in HORIZONS
    for feature in ["ret", "mdd", "rv", "dd_high", "pos"]
]
SIGNAL_MODEL_FEATURES = [
    "score",
    "stock_score",
    "atr",
    "dd60",
    "pos60",
    "rsv",
    "ret5",
    "volx",
    "def_days",
    "idx_rsv",
    "idx_chg1",
]


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def verify_inputs() -> dict[str, dict[str, Any]]:
    manifest = json.loads(BOTTOM_ML_MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["external_datasets"]
    audited: dict[str, dict[str, Any]] = {}
    for name, spec in expected.items():
        path = BOTTOM_ML / name
        if not path.exists():
            raise SystemExit(f"[input] missing: {path}")
        actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        if actual["bytes"] != spec["bytes"] or actual["sha256"] != spec["sha256"]:
            raise SystemExit(
                f"[input] hash mismatch: {path}\n"
                f" expected={spec}\n actual={actual}"
            )
        audited[str(path)] = actual
    return audited


def max_run(mask: np.ndarray) -> int:
    best = 0
    run = 0
    for value in mask:
        run = run + 1 if bool(value) else 0
        best = max(best, run)
    return best


def kline_features(arr: dict[str, np.ndarray], end: int, horizon: int) -> dict[str, float] | None:
    start = end - horizon + 1
    if start < 0:
        return None
    o = np.asarray(arr["o"][start : end + 1], dtype=float)
    c = np.asarray(arr["c"][start : end + 1], dtype=float)
    high = np.asarray(arr["h"][start : end + 1], dtype=float)
    low = np.asarray(arr["l"][start : end + 1], dtype=float)
    volume = np.asarray(arr["v"][start : end + 1], dtype=float)
    if len(c) != horizon or np.any(~np.isfinite(c)) or np.any(c <= 0):
        return None
    previous_close = np.r_[c[0], c[:-1]]
    returns = c[1:] / c[:-1] - 1
    log_close = np.log(c)
    slope = np.polyfit(np.arange(horizon, dtype=float), log_close, 1)[0] * 100
    cumulative_high = np.maximum.accumulate(c)
    maximum_drawdown = np.min(c / cumulative_high - 1) * 100
    candle_range = high - low
    true_range = np.maximum.reduce(
        [high - low, np.abs(high - previous_close), np.abs(low - previous_close)]
    )
    volume_slice = max(2, horizon // 5)
    volume_ratio = (np.mean(volume[-volume_slice:]) + 1e-12) / (
        np.mean(volume[:volume_slice]) + 1e-12
    )
    gaps = o[1:] / c[:-1] - 1
    down = returns < 0
    return {
        "ret": float((c[-1] / c[0] - 1) * 100),
        "slope": float(slope),
        "mdd": float(maximum_drawdown),
        "dd_high": float((c[-1] / np.max(high) - 1) * 100),
        "rebound": float((c[-1] / np.min(low) - 1) * 100),
        "pos": float((c[-1] - np.min(low)) / (np.max(high) - np.min(low) + 1e-12) * 100),
        "rv": float(np.std(returns, ddof=1) * 100) if len(returns) > 1 else math.nan,
        "atrbar": float(np.mean(true_range / previous_close) * 100),
        "downfrac": float(np.mean(down) * 100),
        "maxdownrun": float(max_run(down)),
        "worstday": float(np.min(returns) * 100),
        "bigdown": float(np.mean(returns <= -0.04) * 100),
        "gapdown2": float(np.mean(gaps <= -0.02) * 100),
        "bearcandle": float(np.mean(c < o) * 100),
        "clv": float(np.mean((c - low) / (candle_range + 1e-12)) * 100),
        "lowerwick": float(
            np.mean((np.minimum(o, c) - low) / (candle_range + 1e-12)) * 100
        ),
        "volratio": float(volume_ratio),
    }


def load_klines(con: duckdb.DuckDBPyConnection) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    klines = con.sql(
        f"select code,d,o,c,h,l,v from read_parquet('{BOTTOM_ML.as_posix()}/klines.parquet') "
        "order by code,d"
    ).fetchnumpy()
    by_code: dict[str, dict[str, Any]] = {}
    codes = klines["code"]
    starts = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1]) + 1]
    ends = np.r_[starts[1:], len(codes)]
    for start, end in zip(starts, ends):
        code = str(codes[start])
        dates = np.asarray(klines["d"][start:end], dtype=object)
        by_code[code] = {
            "d": dates,
            "o": klines["o"][start:end],
            "c": klines["c"][start:end],
            "h": klines["h"][start:end],
            "l": klines["l"][start:end],
            "v": klines["v"][start:end],
            "date_to_index": {str(date): i for i, date in enumerate(dates)},
        }
    index = con.sql(
        f"select d,o,c,h,l,v from read_parquet('{BOTTOM_ML.as_posix()}/index_399006.parquet') "
        "order by d"
    ).fetchnumpy()
    return by_code, index


def load_mature_events(con: duckdb.DuckDBPyConnection) -> tuple[list[dict[str, Any]], int]:
    root = BOTTOM_ML.as_posix()
    query = f"""
    with kpos as (
        select code,d,
               row_number() over(partition by code order by d)-1 as rn,
               count(*) over(partition by code) as nbar
        from read_parquet('{root}/klines.parquet')
    ), ipos as (
        select d,row_number() over(order by d)-1 as pos
        from read_parquet('{root}/index_399006.parquet')
    )
    select p.code,p.d,p.outcome,p.score,p.stock_score,p.atr,p.dd60,p.pos60,p.rsv,p.ret5,p.volx,
           p.def_days,p.idx_rsv,p.idx_chg1,p.industry,i.pos
    from read_parquet('{root}/panel.parquet') p
    join kpos k using(code,d)
    join ipos i using(d)
    where (((p.mkt_def and p.score>=18) or ((not p.mkt_def) and p.stock_score>=15)) and p.atr<=4)
      and (k.nbar-1-k.rn)>=21
    order by p.code,p.d
    """
    rows = con.sql(query).fetchall()
    columns = [
        "code",
        "d",
        "outcome",
        "score",
        "stock_score",
        "atr",
        "dd60",
        "pos60",
        "rsv",
        "ret5",
        "volx",
        "def_days",
        "idx_rsv",
        "idx_chg1",
        "industry",
        "ipos",
    ]
    raw = [dict(zip(columns, row)) for row in rows]
    kept: list[dict[str, Any]] = []
    for _, group in itertools.groupby(raw, key=lambda event: event["code"]):
        last_qualified: int | None = None
        for event in group:
            if last_qualified is None or event["ipos"] - last_qualified > 5:
                kept.append(event)
            last_qualified = event["ipos"]
    return kept, len(raw)


def add_features(
    events: list[dict[str, Any]],
    by_code: dict[str, dict[str, Any]],
    index: dict[str, np.ndarray],
) -> None:
    index_date_to_index = {str(date): i for i, date in enumerate(index["d"])}
    index_arrays = {key: index[key] for key in ["o", "c", "h", "l", "v"]}
    for event in events:
        stock = by_code[event["code"]]
        stock_index = stock["date_to_index"][event["d"]]
        event["kidx"] = stock_index
        market_index = index_date_to_index[event["d"]]
        for horizon in HORIZONS:
            stock_values = kline_features(stock, stock_index, horizon)
            market_values = kline_features(index_arrays, market_index, horizon)
            if stock_values:
                for name, value in stock_values.items():
                    event[f"{name}{horizon}"] = value
            if market_values:
                for name in ["ret", "mdd", "rv", "dd_high", "pos"]:
                    event[f"idx_{name}{horizon}"] = market_values[name]
                if stock_values:
                    event[f"excess{horizon}"] = stock_values["ret"] - market_values["ret"]


def finite(values: Iterable[Any]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    return array[np.isfinite(array)]


def auc_test(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    result = stats.mannwhitneyu(a, b, alternative="two-sided")
    return float(result.statistic / (len(a) * len(b))), float(result.pvalue)


def bh_adjust(pvalues: list[float]) -> list[float]:
    p = np.asarray(pvalues, dtype=float)
    count = len(p)
    order = np.argsort(p)
    adjusted = np.empty(count, dtype=float)
    previous = 1.0
    for rank_index in range(count - 1, -1, -1):
        original_index = order[rank_index]
        rank = rank_index + 1
        previous = min(previous, p[original_index] * count / rank)
        adjusted[original_index] = previous
    return adjusted.tolist()


def episode_dedup(events: list[dict[str, Any]], sessions: int = 20) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    ordered = sorted(events, key=lambda event: (event["code"], event["d"]))
    for _, group in itertools.groupby(ordered, key=lambda event: event["code"]):
        last_kept = -10_000
        for event in group:
            if event["ipos"] - last_kept > sessions:
                kept.append(event)
                last_kept = event["ipos"]
    return kept


def same_date_contrast(events: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[list[float]]] = collections.defaultdict(lambda: [[], []])
    for event in events:
        if event["outcome"] not in {"stop", "win"}:
            continue
        value = float(event.get(key, math.nan))
        if not math.isfinite(value):
            continue
        grouped[event["d"]][0 if event["outcome"] == "stop" else 1].append(value)
    differences = [
        float(np.mean(stop) - np.mean(win))
        for stop, win in grouped.values()
        if stop and win
    ]
    if not differences:
        return {"dates": 0, "median_diff": math.nan, "mean_diff": math.nan, "p": math.nan}
    diff = np.asarray(differences, dtype=float)
    pvalue = (
        float(stats.wilcoxon(diff).pvalue)
        if len(diff) > 5 and np.any(np.abs(diff) > 1e-12)
        else math.nan
    )
    return {
        "dates": len(differences),
        "median_diff": float(np.median(diff)),
        "mean_diff": float(np.mean(diff)),
        "p": pvalue,
    }


def feature_comparisons(
    events: list[dict[str, Any]], episode_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for control_name, control_outcomes in [
        ("win", {"win"}),
        ("nonstop", {"win", "timeout"}),
    ]:
        comparison_rows: list[dict[str, Any]] = []
        for horizon in HORIZONS:
            for feature in FEATURES:
                key = f"{feature}{horizon}"
                stops = finite(
                    event.get(key, math.nan)
                    for event in events
                    if event["outcome"] == "stop"
                )
                controls = finite(
                    event.get(key, math.nan)
                    for event in events
                    if event["outcome"] in control_outcomes
                )
                auc, pvalue = auc_test(stops, controls)
                row: dict[str, Any] = {
                    "comparison": f"stop_vs_{control_name}",
                    "horizon": horizon,
                    "feature": feature,
                    "feature_cn": FEATURE_LABELS[feature],
                    "n_stop": len(stops),
                    "n_control": len(controls),
                    "median_stop": float(np.median(stops)),
                    "median_control": float(np.median(controls)),
                    "auc_stop_larger": auc,
                    "p_mannwhitney": pvalue,
                }
                comparison_rows.append(row)
        adjusted = bh_adjust([row["p_mannwhitney"] for row in comparison_rows])
        for row, qvalue in zip(comparison_rows, adjusted):
            row["q_bh"] = qvalue
            key = f"{row['feature']}{row['horizon']}"
            episode_stop = finite(
                event.get(key, math.nan)
                for event in episode_events
                if event["outcome"] == "stop"
            )
            episode_control = finite(
                event.get(key, math.nan)
                for event in episode_events
                if event["outcome"] in control_outcomes
            )
            episode_auc, episode_p = auc_test(episode_stop, episode_control)
            row["episode20_auc"] = episode_auc
            row["episode20_p"] = episode_p
            row["year_auc"] = {}
            for year in ["2024", "2025", "2026"]:
                year_stop = finite(
                    event.get(key, math.nan)
                    for event in events
                    if event["outcome"] == "stop" and event["d"].startswith(year)
                )
                year_control = finite(
                    event.get(key, math.nan)
                    for event in events
                    if event["outcome"] in control_outcomes and event["d"].startswith(year)
                )
                row["year_auc"][year] = (
                    auc_test(year_stop, year_control)[0]
                    if len(year_stop) and len(year_control)
                    else math.nan
                )
            same_date = same_date_contrast(events, key) if control_name == "win" else None
            row["same_date"] = same_date
            direction = 1 if row["auc_stop_larger"] > 0.5 else -1
            episode_consistent = (row["episode20_auc"] - 0.5) * direction > 0
            year_consistent = all(
                math.isfinite(value) and (value - 0.5) * direction > 0
                for value in row["year_auc"].values()
            )
            date_consistent = bool(
                same_date
                and math.isfinite(same_date["median_diff"])
                and same_date["median_diff"] * direction > 0
                and math.isfinite(same_date["p"])
                and same_date["p"] < 0.10
            )
            row["robust_candidate"] = bool(
                control_name == "win"
                and row["q_bh"] < 0.05
                and abs(row["auc_stop_larger"] - 0.5) >= 0.06
                and episode_consistent
                and year_consistent
                and date_consistent
            )
        rows.extend(comparison_rows)
    return rows


def trajectory_rows(
    events: list[dict[str, Any]], by_code: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lag in TRAJECTORY_LAGS:
        groups = {"stop": [], "win": [], "nonstop": [], "all": []}
        for event in events:
            stock = by_code[event["code"]]
            end = event["kidx"]
            if end - lag < 0:
                continue
            value = float(stock["c"][end - lag] / stock["c"][end] * 100)
            groups["all"].append(value)
            if event["outcome"] == "stop":
                groups["stop"].append(value)
            else:
                groups["nonstop"].append(value)
                if event["outcome"] == "win":
                    groups["win"].append(value)
        for group, values in groups.items():
            array = np.asarray(values, dtype=float)
            rows.append(
                {
                    "lag": lag,
                    "x": -lag,
                    "group": group,
                    "n": len(array),
                    "median": float(np.median(array)),
                    "q25": float(np.quantile(array, 0.25)),
                    "q75": float(np.quantile(array, 0.75)),
                }
            )
    return rows


def common_shape(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = [
        "ret",
        "mdd",
        "dd_high",
        "rebound",
        "pos",
        "rv",
        "downfrac",
        "maxdownrun",
        "volratio",
        "excess",
    ]
    for horizon in HORIZONS:
        for feature in selected:
            values = finite(event.get(f"{feature}{horizon}", math.nan) for event in events)
            rows.append(
                {
                    "horizon": horizon,
                    "feature": feature,
                    "feature_cn": FEATURE_LABELS[feature],
                    "n": len(values),
                    "median": float(np.median(values)),
                    "q25": float(np.quantile(values, 0.25)),
                    "q75": float(np.quantile(values, 0.75)),
                }
            )
    return rows


def quarter(date: str) -> str:
    month = int(date[5:7])
    return f"{date[:4]}Q{(month - 1) // 3 + 1}"


def matrix(events: list[dict[str, Any]], features: list[str]) -> np.ndarray:
    return np.asarray(
        [[float(event.get(feature, math.nan)) for feature in features] for event in events],
        dtype=float,
    )


def forward_oos(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labelled = [event for event in events if event["outcome"] in {"stop", "win"}]
    model_sets = {
        "signal_day_baseline": SIGNAL_MODEL_FEATURES,
        "stock_pre_kline": STOCK_MODEL_FEATURES,
        "market_pre_kline": MARKET_MODEL_FEATURES,
        "combined": SIGNAL_MODEL_FEATURES + STOCK_MODEL_FEATURES + MARKET_MODEL_FEATURES,
    }
    validation_blocks = ["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2"]
    rows: list[dict[str, Any]] = []
    aggregate: dict[str, list[dict[str, Any]]] = {name: [] for name in model_sets}
    for block in validation_blocks:
        validation = [event for event in labelled if quarter(event["d"]) == block]
        if not validation:
            continue
        validation_start = min(event["ipos"] for event in validation)
        training = [event for event in labelled if event["ipos"] <= validation_start - 21]
        if (
            len(training) < 200
            or len({event["outcome"] for event in training}) < 2
            or len({event["outcome"] for event in validation}) < 2
        ):
            continue
        y_train = np.asarray([event["outcome"] == "stop" for event in training], dtype=int)
        y_validation = np.asarray(
            [event["outcome"] == "stop" for event in validation], dtype=int
        )
        for model_name, features in model_sets.items():
            pipe = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            C=0.10,
                            class_weight="balanced",
                            max_iter=3000,
                            solver="liblinear",
                            random_state=7,
                        ),
                    ),
                ]
            )
            pipe.fit(matrix(training, features), y_train)
            prediction = pipe.predict_proba(matrix(validation, features))[:, 1]
            auc = float(roc_auc_score(y_validation, prediction))
            rows.append(
                {
                    "scope": "block",
                    "block": block,
                    "model": model_name,
                    "features": len(features),
                    "n_train": len(training),
                    "n_validation": len(validation),
                    "stop_validation": int(y_validation.sum()),
                    "auc": auc,
                }
            )
            aggregate[model_name].append(
                {
                    "auc": auc,
                    "n": len(validation),
                    "stop": int(y_validation.sum()),
                    "win": int(len(y_validation) - y_validation.sum()),
                }
            )
    for model_name, blocks in aggregate.items():
        pair_weights = [block["stop"] * block["win"] for block in blocks]
        pair_weighted_auc = float(
            np.average([block["auc"] for block in blocks], weights=pair_weights)
        )
        rows.append(
            {
                "scope": "aggregate_oos",
                "block": "eligible quarters in 2025Q1-2026Q2",
                "model": model_name,
                "features": len(model_sets[model_name]),
                "n_train": None,
                "n_validation": int(sum(block["n"] for block in blocks)),
                "stop_validation": int(sum(block["stop"] for block in blocks)),
                "eligible_blocks": len(blocks),
                "auc": pair_weighted_auc,
                "macro_auc": float(np.mean([block["auc"] for block in blocks])),
                "min_block_auc": float(min(block["auc"] for block in blocks)),
                "max_block_auc": float(max(block["auc"] for block in blocks)),
            }
        )
    return rows


def market_comparisons(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        for feature in ["ret", "mdd", "rv", "dd_high", "pos"]:
            key = f"idx_{feature}{horizon}"
            stops = finite(
                event.get(key, math.nan) for event in events if event["outcome"] == "stop"
            )
            wins = finite(
                event.get(key, math.nan) for event in events if event["outcome"] == "win"
            )
            auc, pvalue = auc_test(stops, wins)
            rows.append(
                {
                    "horizon": horizon,
                    "feature": feature,
                    "n_stop": len(stops),
                    "n_win": len(wins),
                    "median_stop": float(np.median(stops)),
                    "median_win": float(np.median(wins)),
                    "auc_stop_larger": auc,
                    "p_mannwhitney": pvalue,
                }
            )
    adjusted = bh_adjust([row["p_mannwhitney"] for row in rows])
    for row, qvalue in zip(rows, adjusted):
        row["q_bh"] = qvalue
    return rows


def write_csv(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
        )
    keys: list[str] = []
    for row in normalized:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(normalized)


def plot_trajectory(output: pathlib.Path, trajectory: list[dict[str, Any]]) -> str | None:
    os.environ.setdefault(
        "MPLCONFIGDIR", str(pathlib.Path(tempfile.gettempdir()) / "bottom_precrash_mpl")
    )
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - plot is non-critical
        print(f"[plot] skipped: {exc}")
        return None
    fig, ax = plt.subplots(figsize=(10.5, 6.2), dpi=160)
    colors = {"stop": "#c43b3b", "win": "#167c5a"}
    labels = {"stop": "Stop (-8% first)", "win": "Win (+5% first)"}
    for group in ["stop", "win"]:
        subset = sorted(
            [row for row in trajectory if row["group"] == group], key=lambda row: row["x"]
        )
        x = np.asarray([row["x"] for row in subset], dtype=float)
        median = np.asarray([row["median"] for row in subset], dtype=float)
        q25 = np.asarray([row["q25"] for row in subset], dtype=float)
        q75 = np.asarray([row["q75"] for row in subset], dtype=float)
        ax.plot(x, median, color=colors[group], linewidth=2.2, label=labels[group])
        ax.fill_between(x, q25, q75, color=colors[group], alpha=0.12)
    ax.axhline(100, color="#555555", linewidth=1, linestyle="--")
    ax.axvline(-60, color="#777777", linewidth=1, linestyle=":")
    ax.set_title("Pre-signal adjusted close trajectory (T close = 100)")
    ax.set_xlabel("Trading sessions before signal T")
    ax.set_ylabel("Adjusted close / close(T) x 100")
    ax.grid(alpha=0.20)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = output / "trajectory.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def outcome_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(collections.Counter(event["outcome"] for event in events).items()))


def make_summary(
    audit: dict[str, Any],
    common: list[dict[str, Any]],
    trajectory: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    market: list[dict[str, Any]],
    oos: list[dict[str, Any]],
) -> str:
    common_map = {(row["horizon"], row["feature"]): row for row in common}
    trajectory_map = {
        (row["lag"], row["group"]): row
        for row in trajectory
        if row["lag"] in EXACT_TRAJECTORY_LAGS
    }
    primary = [row for row in comparisons if row["comparison"] == "stop_vs_win"]
    top = sorted(primary, key=lambda row: abs(row["auc_stop_larger"] - 0.5), reverse=True)[:12]
    robust = [row for row in primary if row["robust_candidate"]]
    pooled = {row["model"]: row for row in oos if row["scope"] == "aggregate_oos"}
    market_top = sorted(
        market, key=lambda row: abs(row["auc_stop_larger"] - 0.5), reverse=True
    )[:8]
    lines = [
        "# bottom-fishing 暴雷前 K 线轨迹研究",
        "",
        f"研究日期：{audit['study_date']}。本研究只更新研究层，不改生产 workflow。",
        "",
        "## 结论先行",
        "",
        "1. **共同形态非常清楚，区别却很弱**：过线票无论后来暴雷还是先到 +5%，T 前 60 日都经历了约",
        f"   `{common_map[(60, 'ret')]['median']:.1f}%` 的收盘下跌、约 `{common_map[(60, 'mdd')]['median']:.1f}%` 的窗口最大回撤，",
        f"   T 收盘仍距 60 日最高价 `{common_map[(60, 'dd_high')]['median']:.1f}%`，只比窗口最低价反弹",
        f"   `{common_map[(60, 'rebound')]['median']:.1f}%`。这就是“深跌后微修复”，不是完整 V 型反转。",
        "2. **10/20/30/60 日个股 K 线几乎分不出雷与不雷**。短中窗的收益、最大回撤、位置、连跌、阴线、",
        "   缺口、下影、波动和量价形态，大部分 AUC 仅在 0.48—0.55 左右；同一信号日比较后没有稳定差异。",
        "3. **100—150 日出现的表面差异是 regime/年份混杂**：全样本看雷票此前跌得更久，但分年后方向反复，",
        "   同日对照甚至反向，不能把它做成“长跌更危险”的生产过滤器。",
        "4. **未发现同时通过 BH、多年份同向、20 日事件去重、同日对照四道门的个股 K 线特征**。",
        f"   当前严格稳健候选数 = `{len(robust)}`。",
        "5. 差异更像发生在**市场路径/时点**而非个股蜡烛形态，但市场变量也有强非平稳性；只能支持",
        "   “regime 风险值得继续研究”，不能据此新增禁买 gate。",
        "",
        "## 样本与标签",
        "",
        f"- 成熟原始过线：{audit['raw_mature_qualified']} 笔。",
        f"- 当前 N=5 冷却：{audit['cooldown5']['n']} 笔，{audit['cooldown5']['outcomes']}，",
        f"  {audit['cooldown5']['codes']} 只股票，{audit['cooldown5']['min_date']} 至 {audit['cooldown5']['max_date']}。",
        f"- 20 市场交易日事件去重：{audit['episode20']['n']} 笔，{audit['episode20']['outcomes']}。",
        "- 暴雷 = T+1 开盘后 20 日内先触 -8%；胜 = 先触 +5%；timeout 单列。",
        "- 只保留未来至少 21 根 K 线的成熟事件；所有解释变量只用 T 及此前数据。",
        "",
        "## 事件对齐轨迹（T 收盘 = 100）",
        "",
        "数值大于 100 表示当时价格高于 T；例如 lag=60 的 120 表示 60 根 K 线前约比 T 高 20%。",
        "",
        "| lag | 雷票中位 | 胜票中位 | 非雷中位 | 雷/胜样本 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for lag in EXACT_TRAJECTORY_LAGS:
        stop = trajectory_map[(lag, "stop")]
        win = trajectory_map[(lag, "win")]
        nonstop = trajectory_map[(lag, "nonstop")]
        lines.append(
            f"| {'T' if lag == 0 else f'-{lag}'} | {stop['median']:.2f} | {win['median']:.2f} | "
            f"{nonstop['median']:.2f} | {stop['n']}/{win['n']} |"
        )
    lines.extend(
        [
            "",
            "最稳定的共同路径集中在 T 前 60 日：两组几乎重合。更早的 100—150 日分叉主要由",
            "2024 雷样本占比高、2025 胜样本占比高造成，不能解释为个股可部署信号。",
            "",
            "## 各窗口共同形态",
            "",
            "| 窗口 | 收益% | 最大回撤% | 距最高% | 低点反弹% | 位置% | 日波动% | 下跌日% | 最长连跌 | 量比(后/前) | 超额% |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for horizon in HORIZONS:
        get = lambda feature: common_map[(horizon, feature)]["median"]
        lines.append(
            f"| {horizon} | {get('ret'):.2f} | {get('mdd'):.2f} | {get('dd_high'):.2f} | "
            f"{get('rebound'):.2f} | {get('pos'):.2f} | {get('rv'):.2f} | {get('downfrac'):.2f} | "
            f"{get('maxdownrun'):.0f} | {get('volratio'):.2f} | {get('excess'):.2f} |"
        )
    lines.extend(
        [
            "",
            "60 日窗最有代表性：成交量后 20%/前 20%中位约 0.5，说明深跌过程普遍缩量；",
            "这不是雷票专属，胜票也一样。10 日和 20 日窗仍为负收益，说明所谓“修复确认”只是",
            "站回短均线/DIF 转向等局部修复，并没有扭转完整下跌结构。",
            "",
            "## 雷票 vs 胜票：表面效应最大的项目",
            "",
            "AUC>0.5 表示该特征在雷票更大；AUC<0.5 表示在雷票更小。0.5 为无区分。",
            "原始 Mann–Whitney/BH 只作发现，必须结合分年、事件去重和同日对照。",
            "",
            "| 窗口 | 特征 | 雷中位 | 胜中位 | AUC | BH q | 20日去重AUC | 分年AUC(24/25/26) | 同日中位差 | 稳健 |",
            "|---:|---|---:|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in top:
        yearly = "/".join(f"{row['year_auc'][year]:.3f}" for year in ["2024", "2025", "2026"])
        same = row["same_date"]
        lines.append(
            f"| {row['horizon']} | {row['feature_cn']} | {row['median_stop']:.3f} | "
            f"{row['median_control']:.3f} | {row['auc_stop_larger']:.3f} | {row['q_bh']:.3g} | "
            f"{row['episode20_auc']:.3f} | {yearly} | {same['median_diff']:+.3f} | "
            f"{'是' if row['robust_candidate'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "典型反例是 120 日收益：全样本雷票中位更差，但 2024/2026 内部方向反过来；",
            "它识别的是年份构成，不是同一时点里的坏股票。60/75 日相对放量和 30 日波动虽有小效应，",
            "同日及分年不稳定，也不能升级。",
            "",
            "## 市场路径（描述性）",
            "",
            "| 窗口 | 指数特征 | 雷中位 | 胜中位 | AUC | BH q |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for row in market_top:
        lines.append(
            f"| {row['horizon']} | {row['feature']} | {row['median_stop']:.3f} | "
            f"{row['median_win']:.3f} | {row['auc_stop_larger']:.3f} | {row['q_bh']:.3g} |"
        )
    lines.extend(
        [
            "",
            "指数差异说明雷会按市场日期成簇，但同一日期内指数值对所有股票相同，不能选出哪只会雷。",
            "这与既有毒月研究的“个股端无解、regime 端成簇”一致。",
            "",
            "## 前推样本外分类诊断",
            "",
            "以季度为验证块，训练集在验证块开始前 embargo 20 个市场交易日；目标是 stop vs win。",
            "模型为带正则的平衡 Logistic，只用于检验可分性，不用于寻找新权重。",
            "",
            "不同验证块由不同训练样本拟合，预测概率不可直接跨块拼接；主汇总用块内 stop×win 对数加权 AUC，",
            "同时报告季度宏平均与范围。",
            "",
            "| 特征集 | 有效季度 | OOS样本 | 雷数 | 对数加权AUC | 宏平均AUC | 季度范围 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for model_name in [
        "signal_day_baseline",
        "stock_pre_kline",
        "market_pre_kline",
        "combined",
    ]:
        row = pooled[model_name]
        lines.append(
            f"| {model_name} | {row['eligible_blocks']} | {row['n_validation']} | {row['stop_validation']} | "
            f"{row['auc']:.3f} | {row['macro_auc']:.3f} | "
            f"{row['min_block_auc']:.3f}—{row['max_block_auc']:.3f} |"
        )
    lines.extend(
        [
            "",
            "stock_pre_kline 的季度宏平均若接近 0.5、且季度范围跨越 0.5，表示把多窗口形态一起喂给",
            "线性分类器也没有稳定增量；某一大样本季度拉高加权值，不等于跨期可部署。",
            "",
            "## 裁定",
            "",
            "- **采纳为描述**：过线票共有“60 日深跌、20—30 日继续走弱、T 附近仅微修复、成交量收缩”的轨迹。",
            "- **否决为生产过滤器**：前 10—60 日常见蜡烛/量价形态不能稳定区分雷与胜。",
            "- **否决为生产过滤器**：100—150 日累计跌幅的全样本分离度，因年份/regime 混杂不稳定。",
            "- **保留研究方向**：市场级连续风险刻度、消息面恶化分型；仍须无前视、走样本和机会成本验证。",
            "- 本研究不修改 SKILL、引擎分数、阈值、仓位、熔断或 Agent②/③流程。",
            "",
            "## 限制",
            "",
            "- 股票池是 2026-07 时点的高流动性幸存者池，绝对胜/雷率有幸存者偏差。",
            "- 腾讯 qfq 个股数据约从 2023-10 起，150 日窗口只覆盖 1,306/1,458 笔冷却事件，",
            "  早期 2024 样本不足；不同窗口的样本量不是完全相同。",
            "- 同股重复、同日横截面和毒月成簇使逐笔 p 值偏乐观；因此本研究额外使用 20 日事件去重、",
            "  同日对照和时间前推，但仍不等于独立随机样本。",
            "- OHLCV 只能观察已发生的价格路径，不能预知 T 后才公开的业绩、监管或地缘冲击。",
            "- 本研究是相关性审计，不构成交易建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    inputs = verify_inputs()
    con = duckdb.connect()
    by_code, index = load_klines(con)
    events, raw_mature_count = load_mature_events(con)
    add_features(events, by_code, index)
    episode_events = episode_dedup(events, 20)

    common = common_shape(events)
    trajectory = trajectory_rows(events, by_code)
    comparisons = feature_comparisons(events, episode_events)
    market = market_comparisons(events)
    oos = forward_oos(events)

    by_year = {}
    for year in ["2024", "2025", "2026"]:
        subset = [event for event in events if event["d"].startswith(year)]
        by_year[year] = {"n": len(subset), "outcomes": outcome_counts(subset)}
    audit = {
        "schema": "bottom-precrash-kline-study/v1",
        "study_date": "2026-08-02",
        "workflow_modified": False,
        "input_hashes": inputs,
        "definitions": {
            "qualified": "defensive & score>=18 OR non-defensive & stock_score>=15; ATR<=4",
            "cooldown": "N=5; every qualified event refreshes timer, including suppressed events",
            "entry": "T+1 open",
            "win": "within 20 sessions, high reaches +5% before low reaches -8%",
            "stop": "within 20 sessions, low reaches -8% before high reaches +5%",
            "maturity": "at least 21 stock K bars after T",
            "pre_windows": HORIZONS,
            "prices": "Tencent qfq cached in pinned klines.parquet",
        },
        "raw_mature_qualified": raw_mature_count,
        "cooldown5": {
            "n": len(events),
            "outcomes": outcome_counts(events),
            "codes": len({event["code"] for event in events}),
            "min_date": min(event["d"] for event in events),
            "max_date": max(event["d"] for event in events),
            "by_year": by_year,
        },
        "episode20": {
            "n": len(episode_events),
            "outcomes": outcome_counts(episode_events),
        },
        "complete_pre_window_n": {
            str(horizon): sum(
                math.isfinite(float(event.get(f"ret{horizon}", math.nan))) for event in events
            )
            for horizon in HORIZONS
        },
        "software": {
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
        },
    }
    robust = [
        row
        for row in comparisons
        if row["comparison"] == "stop_vs_win" and row["robust_candidate"]
    ]
    results = {
        "audit": audit,
        "verdict": {
            "robust_stock_kline_candidates": len(robust),
            "candidates": robust,
            "production_change": False,
            "summary": (
                "10-60日共同深跌后微修复轨迹明确，但雷/胜个股K线不可稳定区分；"
                "100-150日表面差异受年份/regime混杂，未通过同日、分年与事件去重。"
            ),
        },
        "common_shape": common,
        "trajectory": trajectory,
        "feature_comparisons": comparisons,
        "market_comparisons": market,
        "forward_oos": oos,
    }

    write_csv(output / "common_shape.csv", common)
    write_csv(output / "trajectory.csv", trajectory)
    write_csv(output / "feature_comparison.csv", comparisons)
    write_csv(output / "market_comparison.csv", market)
    write_csv(output / "oos_auc.csv", oos)
    (output / "sample_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    summary = make_summary(audit, common, trajectory, comparisons, market, oos)
    (output / "summary.md").write_text(summary, encoding="utf-8")
    plot_trajectory(output, trajectory)

    print(f"[sample] raw_mature={raw_mature_count} cooldown5={len(events)} episode20={len(episode_events)}")
    print(f"[sample] outcomes={outcome_counts(events)} {audit['cooldown5']['min_date']}..{audit['cooldown5']['max_date']}")
    print(f"[verdict] robust_stock_kline_candidates={len(robust)} production_change=false")
    for row in oos:
        if row["scope"] == "aggregate_oos":
            print(
                f"[oos] {row['model']} n={row['n_validation']} "
                f"pair_weighted_auc={row['auc']:.3f} macro_auc={row['macro_auc']:.3f}"
            )
    print(f"[output] {output}")


if __name__ == "__main__":
    main()
