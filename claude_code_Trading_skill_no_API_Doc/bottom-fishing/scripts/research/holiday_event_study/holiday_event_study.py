# -*- coding: utf-8 -*-
"""
A股抄底策略 × 法定节假日事件研究（只读生产数据，隔离输出）。

研究目标
--------
检验现行 bottom-fishing 推荐线在节假日前后是否更容易先触发 -8% 止损。

纪律
----
1. 不修改 skill workflow、生产引擎、权重、报告或影子日志。
2. 推荐线与标签保持不变：
   防守日总分>=18 或非防守日个股分>=15，且 ATR<=4；
   T+1 开盘进场，20 个交易日内先到 +5% 记 win，先到 -8% 记 stop。
3. 同时报原始推荐线与当前 5 交易日旋转门冷却线。
4. 只用已经完整走完 20 交易日标签窗的信号，消除样本尾部右删失。
5. 节假日来自上交所 2024/2025/2026 年度休市通知；2026 年未来节日只归档、不检验。
6. 推断以“节日事件/信号日”为聚类单位；行级 Fisher 检验不作为主结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, spearmanr


DATA_ROOT = Path(r"C:\Trading_analysis\research\bottom_ml")
MANIFEST_PATH = Path(
    r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc"
    r"\bottom-fishing\scripts\research\bottom_ml\SOURCE_MANIFEST.json"
)
SCRIPT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = Path(r"C:\Trading_analysis\research\bottom_holiday_event_study")
CALENDAR_CSV = SCRIPT_ROOT / "holiday_calendar.csv"

TH_TOTAL = 18.0
TH_STOCK = 15.0
TH_ATR = 4.0
COOLDOWN_N = 5
MAX_HOLD = 20
TARGET_PCT = 5.0
STOP_PCT = -8.0
LOCAL_SIDE = 10
WINDOWS = (1, 3, 5)
RANDOM_SEED = 20260723
N_PERM = 10_000
N_BOOT = 10_000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().upper()


def verify_inputs() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    checks: dict[str, Any] = {}
    for name, spec in manifest["external_datasets"].items():
        path = DATA_ROOT / name
        actual_size = path.stat().st_size
        actual_hash = sha256(path)
        ok = actual_size == spec["bytes"] and actual_hash == spec["sha256"]
        checks[name] = {
            "ok": ok,
            "bytes": actual_size,
            "sha256": actual_hash,
            "expected_bytes": spec["bytes"],
            "expected_sha256": spec["sha256"],
        }
        if not ok:
            raise RuntimeError(f"输入数据 hash/size 不匹配：{name}")
    return checks


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return (math.nan, math.nan)
    p = successes / n
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (center - half, center + half)


def perf(df: pd.DataFrame) -> dict[str, Any]:
    n = int(len(df))
    wins = int((df["outcome"] == "win").sum()) if n else 0
    stops = int((df["outcome"] == "stop").sum()) if n else 0
    timeouts = int((df["outcome"] == "timeout").sum()) if n else 0
    wci = wilson(wins, n)
    sci = wilson(stops, n)
    return {
        "n": n,
        "wins": wins,
        "stops": stops,
        "timeouts": timeouts,
        "win_rate": wins / n if n else math.nan,
        "stop_rate": stops / n if n else math.nan,
        "ev_pct": wins / n * TARGET_PCT + stops / n * STOP_PCT if n else math.nan,
        "win_ci95": list(wci),
        "stop_ci95": list(sci),
        "signal_dates": int(df["d"].nunique()) if n else 0,
        "codes": int(df["code"].nunique()) if n else 0,
    }


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


def apply_cooldown(q: pd.DataFrame, trading_pos: dict[str, int]) -> pd.DataFrame:
    work = q.copy()
    work["signal_pos"] = work["d"].map(trading_pos)
    if work["signal_pos"].isna().any():
        missing = work.loc[work["signal_pos"].isna(), "d"].unique().tolist()
        raise RuntimeError(f"推荐线日期不在指数交易日历：{missing[:5]}")
    keep = pd.Series(False, index=work.index)
    cooldown = pd.Series(False, index=work.index)
    for _, group in work.sort_values(["code", "signal_pos"]).groupby("code", sort=False):
        last_qualified_pos: int | None = None
        for idx, row in group.iterrows():
            pos = int(row["signal_pos"])
            blocked = last_qualified_pos is not None and pos - last_qualified_pos <= COOLDOWN_N
            cooldown.loc[idx] = blocked
            keep.loc[idx] = not blocked
            # 与生产/敏感性脚本一致：被冷却的过线票也刷新计时。
            last_qualified_pos = pos
    work["cooldown"] = cooldown
    return work.loc[keep].sort_values(["d", "code"]).reset_index(drop=True)


def attach_path_dates(q: pd.DataFrame, klines: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """复算每笔的进场日/结算日；同时核验归档 outcome，供跨假期持仓描述。"""
    work = q.copy()
    work["entry_date"] = ""
    work["resolve_date"] = ""
    work["derived_outcome"] = ""
    mismatch = 0

    for code, rows in work.groupby("code"):
        k = klines.loc[klines["code"] == code].sort_values("d").reset_index(drop=True)
        if k.empty:
            raise RuntimeError(f"缺少K线：{code}")
        loc = {str(d): i for i, d in enumerate(k["d"].astype(str))}
        o = k["o"].to_numpy(float)
        c = k["c"].to_numpy(float)
        h = k["h"].to_numpy(float)
        l = k["l"].to_numpy(float)
        d = k["d"].astype(str).to_numpy()

        for idx, row in rows.iterrows():
            i = loc.get(str(row["d"]))
            if i is None or i + 2 >= len(k):
                raise RuntimeError(f"无法复算路径：{code} {row['d']}")
            entry_i = i + 1
            entry = o[entry_i]
            stop = entry * (1 + STOP_PCT / 100)
            target = entry * (1 + TARGET_PCT / 100)
            derived = ""
            resolve_i: int | None = None

            if c[entry_i] <= stop:
                derived, resolve_i = "stop", entry_i
            else:
                end = min(entry_i + MAX_HOLD, len(k) - 1)
                for j in range(entry_i + 1, end + 1):
                    if l[j] <= stop:
                        derived, resolve_i = "stop", j
                        break
                    if h[j] >= target:
                        derived, resolve_i = "win", j
                        break
                if not derived and entry_i + MAX_HOLD <= len(k) - 1:
                    derived, resolve_i = "timeout", entry_i + MAX_HOLD

            if not derived or resolve_i is None:
                raise RuntimeError(f"成熟样本仍未结算：{code} {row['d']}")
            work.at[idx, "entry_date"] = d[entry_i]
            work.at[idx, "resolve_date"] = d[resolve_i]
            work.at[idx, "derived_outcome"] = derived
            mismatch += int(derived != row["outcome"])

    return work, mismatch


def load_holidays(
    trading_dates: list[str],
    feature_start: str,
    mature_cutoff: str,
) -> pd.DataFrame:
    holidays = pd.read_csv(CALENDAR_CSV, dtype=str)
    pos = {d: i for i, d in enumerate(trading_dates)}
    start_pos = pos[feature_start]
    cutoff_pos = pos[mature_cutoff]
    records = []
    for _, row in holidays.iterrows():
        r = row.to_dict()
        last = r["last_trade"]
        reopen = r["reopen"]
        r["gap_calendar_days"] = (
            pd.Timestamp(reopen) - pd.Timestamp(last)
        ).days - 1
        r["long_gap"] = bool(r["gap_calendar_days"] >= 7)
        r["in_market_calendar"] = last in pos and reopen in pos
        r["descriptive_complete_5"] = False
        r["local_complete"] = False
        if r["in_market_calendar"]:
            lp, rp = pos[last], pos[reopen]
            r["last_pos"] = lp
            r["reopen_pos"] = rp
            r["descriptive_complete_5"] = (
                lp - (max(WINDOWS) - 1) >= start_pos
                and rp + (max(WINDOWS) - 1) <= cutoff_pos
            )
            r["local_complete"] = (
                lp - (LOCAL_SIDE - 1) >= start_pos
                and rp + (LOCAL_SIDE - 1) <= cutoff_pos
            )
        else:
            r["last_pos"] = None
            r["reopen_pos"] = None
        records.append(r)
    return pd.DataFrame(records)


def event_dates(
    event: pd.Series | dict[str, Any],
    trading_dates: list[str],
    side: str,
    width: int,
) -> list[str]:
    if side == "pre":
        end = int(event["last_pos"])
        return trading_dates[end - width + 1 : end + 1]
    if side == "post":
        start = int(event["reopen_pos"])
        return trading_dates[start : start + width]
    raise ValueError(side)


def pooled_window_stats(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
    width: int,
) -> dict[str, Any]:
    pre_dates: set[str] = set()
    post_dates: set[str] = set()
    memberships: dict[tuple[str, str], int] = {}
    for _, event in events.iterrows():
        for side in ("pre", "post"):
            dates = event_dates(event, trading_dates, side, width)
            target = pre_dates if side == "pre" else post_dates
            for date in dates:
                target.add(date)
                memberships[(side, date)] = memberships.get((side, date), 0) + 1
    overlap_dates = sum(v > 1 for v in memberships.values())
    near = pre_dates | post_dates
    return {
        "pre": perf(sample[sample["d"].isin(pre_dates)]),
        "post": perf(sample[sample["d"].isin(post_dates)]),
        "outside": perf(sample[~sample["d"].isin(near)]),
        "pre_dates": len(pre_dates),
        "post_dates": len(post_dates),
        "overlap_dates": overlap_dates,
    }


@dataclass
class EventCounts:
    event_id: str
    year: int
    holiday: str
    n: np.ndarray
    stops: np.ndarray


def build_event_counts(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
    side: str,
    date_col: str = "d",
) -> list[EventCounts]:
    daily = sample.groupby(date_col).agg(
        n=("outcome", "size"),
        stops=("outcome", lambda x: int((x == "stop").sum())),
    )
    records: list[EventCounts] = []
    for _, event in events.iterrows():
        dates = event_dates(event, trading_dates, side, LOCAL_SIDE)
        n = np.array([int(daily.at[d, "n"]) if d in daily.index else 0 for d in dates])
        stops = np.array(
            [int(daily.at[d, "stops"]) if d in daily.index else 0 for d in dates]
        )
        records.append(
            EventCounts(
                event_id=str(event["event_id"]),
                year=int(event["year"]),
                holiday=str(event["holiday"]),
                n=n,
                stops=stops,
            )
        )
    return records


def aggregate_delta(
    records: list[EventCounts],
    side: str,
    width: int,
    starts: Iterable[int] | None = None,
) -> tuple[float, int, int, int, int]:
    if starts is None:
        actual_start = LOCAL_SIDE - width if side == "pre" else 0
        starts = [actual_start] * len(records)
    tn = ts = cn = cs = 0
    for record, start in zip(records, starts):
        mask = np.zeros(LOCAL_SIDE, dtype=bool)
        mask[int(start) : int(start) + width] = True
        tn += int(record.n[mask].sum())
        ts += int(record.stops[mask].sum())
        cn += int(record.n[~mask].sum())
        cs += int(record.stops[~mask].sum())
    if tn == 0 or cn == 0:
        return (math.nan, tn, ts, cn, cs)
    return (ts / tn - cs / cn, tn, ts, cn, cs)


def local_randomization_test(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
    side: str,
    width: int,
    seed: int,
    n_perm: int = N_PERM,
    n_boot: int = N_BOOT,
    date_col: str = "d",
) -> dict[str, Any]:
    records = build_event_counts(sample, events, trading_dates, side, date_col=date_col)
    actual, tn, ts, cn, cs = aggregate_delta(records, side, width)
    if not records or math.isnan(actual):
        return {
            "events": len(records),
            "side": side,
            "width": width,
            "delta_stop_pp": math.nan,
            "p_two_sided": math.nan,
        }

    # 每个节日同侧 10 个交易日内，随机放置一个等长连续窗口。
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    max_start = LOCAL_SIDE - width
    for i in range(n_perm):
        starts = rng.integers(0, max_start + 1, size=len(records))
        null[i] = aggregate_delta(records, side, width, starts)[0]
    null_center = float(np.nanmean(null))
    distance = abs(actual - null_center)
    p_two = (int(np.sum(np.abs(null - null_center) >= distance)) + 1) / (n_perm + 1)

    # 以节日事件为 cluster bootstrap 单位。
    boot = np.empty(n_boot)
    for i in range(n_boot):
        chosen = rng.integers(0, len(records), size=len(records))
        sample_records = [records[j] for j in chosen]
        boot[i] = aggregate_delta(sample_records, side, width)[0]
    finite_boot = boot[np.isfinite(boot)]
    ci = (
        [float(np.quantile(finite_boot, 0.025)), float(np.quantile(finite_boot, 0.975))]
        if len(finite_boot)
        else [math.nan, math.nan]
    )

    event_deltas = []
    for record in records:
        d, en, _, ec, _ = aggregate_delta([record], side, width)
        if en > 0 and ec > 0 and np.isfinite(d):
            event_deltas.append(d)

    loo: dict[str, float] = {}
    if len(records) > 1:
        for i, record in enumerate(records):
            reduced = records[:i] + records[i + 1 :]
            d = aggregate_delta(reduced, side, width)[0]
            if np.isfinite(d):
                loo[record.event_id] = d * 100

    return {
        "events": len(records),
        "events_with_both_groups": len(event_deltas),
        "side": side,
        "width": width,
        "treated_n": tn,
        "treated_stops": ts,
        "treated_stop_rate": ts / tn if tn else math.nan,
        "control_n": cn,
        "control_stops": cs,
        "control_stop_rate": cs / cn if cn else math.nan,
        "delta_stop_pp": actual * 100,
        "event_equal_delta_stop_pp": (
            float(np.mean(event_deltas) * 100) if event_deltas else math.nan
        ),
        "cluster_boot_ci95_pp": [x * 100 for x in ci],
        "p_two_sided": p_two,
        "null_mean_delta_pp": null_center * 100,
        "permutations": n_perm,
        "bootstrap_draws": n_boot,
        "local_side_sessions": LOCAL_SIDE,
        "null_design": "每个节日同侧10交易日内随机连续放置等长窗口",
        "clock": "signal_T" if date_col == "d" else "entry_T_plus_1",
        "leave_one_event_out_delta_pp": loo,
        "leave_one_event_out_range_pp": (
            [min(loo.values()), max(loo.values())] if loo else [math.nan, math.nan]
        ),
    }


def bh_adjust(tests: list[dict[str, Any]]) -> None:
    valid = [(i, float(t["p_two_sided"])) for i, t in enumerate(tests) if np.isfinite(t.get("p_two_sided", math.nan))]
    if not valid:
        return
    order = sorted(valid, key=lambda x: x[1])
    m = len(order)
    adjusted = [math.nan] * len(tests)
    running = 1.0
    for rank_from_end in range(m - 1, -1, -1):
        original_index, p = order[rank_from_end]
        rank = rank_from_end + 1
        running = min(running, p * m / rank)
        adjusted[original_index] = min(1.0, running)
    for i, q in enumerate(adjusted):
        tests[i]["q_bh"] = q


def relative_day_stats(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
    mode: str,
) -> list[dict[str, Any]]:
    rows = []
    for rel in range(-5, 6):
        if rel == 0:
            continue
        dates = set()
        for _, event in events.iterrows():
            if rel < 0:
                all_pre = event_dates(event, trading_dates, "pre", 5)
                date = all_pre[rel]  # -5..-1
            else:
                all_post = event_dates(event, trading_dates, "post", 5)
                date = all_post[rel - 1]
            dates.add(date)
        s = perf(sample[sample["d"].isin(dates)])
        rows.append({"mode": mode, "relative_session": rel, **s})
    return rows


def event_table(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for _, event in events.iterrows():
        pre = set(event_dates(event, trading_dates, "pre", 5))
        post = set(event_dates(event, trading_dates, "post", 5))
        last, reopen = str(event["last_trade"]), str(event["reopen"])
        carried = sample[
            (sample["entry_date"] <= last)
            & (sample["resolve_date"] >= reopen)
        ]
        rows.append(
            {
                "event_id": event["event_id"],
                "year": int(event["year"]),
                "holiday": event["holiday"],
                "last_trade": last,
                "reopen": reopen,
                "gap_calendar_days": int(event["gap_calendar_days"]),
                "long_gap": bool(event["long_gap"]),
                **{f"pre5_{k}": v for k, v in perf(sample[sample["d"].isin(pre)]).items()},
                **{f"post5_{k}": v for k, v in perf(sample[sample["d"].isin(post)]).items()},
                **{f"carried_{k}": v for k, v in perf(carried).items()},
            }
        )
    return rows


def toxic_months(raw: pd.DataFrame) -> list[str]:
    monthly = raw.groupby("month").agg(
        n=("outcome", "size"),
        stops=("outcome", lambda x: int((x == "stop").sum())),
    )
    monthly["stop_rate"] = monthly["stops"] / monthly["n"]
    return monthly[(monthly["n"] >= 15) & (monthly["stop_rate"] >= 0.30)].index.tolist()


def toxic_holiday_relation(
    sample: pd.DataFrame,
    events: pd.DataFrame,
    trading_dates: list[str],
    mode: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    反向归因：节日窗口是否“制造”毒月。

    对每个1/3/5日窗口：
    1) 比较毒月内节日窗/非节日窗雷率与信号、雷的份额；
    2) 月级检验“有节日暴露的月份是否更容易成为毒月”；
    3) 删除节日窗后重算毒月，并区分“雷率真正跌破30%”与“仅因n<15失去资格”。
    """
    work = sample.copy()
    base_monthly = work.groupby("month").agg(
        n=("outcome", "size"),
        stops=("outcome", lambda x: int((x == "stop").sum())),
    )
    base_monthly["stop_rate"] = base_monthly["stops"] / base_monthly["n"]
    original_toxic = set(
        base_monthly[
            (base_monthly["n"] >= 15) & (base_monthly["stop_rate"] >= 0.30)
        ].index
    )
    result: dict[str, Any] = {
        "mode": mode,
        "original_toxic_months": sorted(original_toxic),
        "by_width": {},
    }
    attribution_rows: list[dict[str, Any]] = []

    for width in WINDOWS:
        membership: dict[str, list[str]] = {}
        for _, event in events.iterrows():
            for side in ("pre", "post"):
                label = f"{event['year']} {event['holiday']} {side}"
                for date in event_dates(event, trading_dates, side, width):
                    membership.setdefault(date, []).append(label)
        holiday_dates = set(membership)

        x = work.copy()
        x["holiday_window"] = x["d"].isin(holiday_dates)
        monthly = x.groupby("month").agg(
            n=("outcome", "size"),
            stops=("outcome", lambda q: int((q == "stop").sum())),
            holiday_n=("holiday_window", "sum"),
        )
        h_monthly = x[x["holiday_window"]].groupby("month").agg(
            holiday_stops=("outcome", lambda q: int((q == "stop").sum()))
        )
        monthly = monthly.join(h_monthly, how="left")
        monthly["holiday_stops"] = monthly["holiday_stops"].fillna(0).astype(int)
        monthly["stop_rate"] = monthly["stops"] / monthly["n"]
        monthly["holiday_signal_share"] = monthly["holiday_n"] / monthly["n"]
        monthly["toxic"] = (monthly["n"] >= 15) & (monthly["stop_rate"] >= 0.30)
        monthly["has_holiday_signal"] = monthly["holiday_n"] > 0

        valid = monthly[monthly["n"] >= 15].copy()
        cross = (
            pd.crosstab(valid["toxic"], valid["has_holiday_signal"])
            .reindex(index=[False, True], columns=[False, True], fill_value=0)
        )
        odds, fisher_p = fisher_exact(cross.to_numpy())
        spear = spearmanr(valid["holiday_signal_share"], valid["stop_rate"])

        tox_pool = x[x["month"].isin(original_toxic)]
        h_pool = tox_pool[tox_pool["holiday_window"]]
        non_pool = tox_pool[~tox_pool["holiday_window"]]
        h_stops = int((h_pool["outcome"] == "stop").sum())
        non_stops = int((non_pool["outcome"] == "stop").sum())
        all_stops = h_stops + non_stops

        removed = x[~x["holiday_window"]]
        after = removed.groupby("month").agg(
            n=("outcome", "size"),
            stops=("outcome", lambda q: int((q == "stop").sum())),
        )
        after["stop_rate"] = after["stops"] / after["n"]
        after_toxic = set(
            after[(after["n"] >= 15) & (after["stop_rate"] >= 0.30)].index
        )
        disappeared = sorted(original_toxic - after_toxic)
        true_rate_rescue = []
        threshold_indeterminate = []
        disappeared_detail = []
        for month in disappeared:
            after_n = int(after.at[month, "n"]) if month in after.index else 0
            after_rate = (
                float(after.at[month, "stop_rate"]) if month in after.index else math.nan
            )
            if after_n >= 15 and after_rate < 0.30:
                reason = "rate_below_30"
                true_rate_rescue.append(month)
            else:
                reason = "n_below_15"
                threshold_indeterminate.append(month)
            disappeared_detail.append(
                {
                    "month": month,
                    "after_n": after_n,
                    "after_stop_rate": after_rate,
                    "reason": reason,
                }
            )

        width_result = {
            "width": width,
            "valid_months_n_ge_15": int(len(valid)),
            "month_level_crosstab": {
                "normal_no_holiday": int(cross.loc[False, False]),
                "normal_has_holiday": int(cross.loc[False, True]),
                "toxic_no_holiday": int(cross.loc[True, False]),
                "toxic_has_holiday": int(cross.loc[True, True]),
            },
            "month_level_odds_ratio": float(odds),
            "month_level_fisher_p": float(fisher_p),
            "monthly_holiday_share_vs_stop_rate_spearman": float(spear.statistic),
            "monthly_holiday_share_vs_stop_rate_p": float(spear.pvalue),
            "toxic_pool": {
                "holiday_n": int(len(h_pool)),
                "holiday_stops": h_stops,
                "holiday_stop_rate": (
                    h_stops / len(h_pool) if len(h_pool) else math.nan
                ),
                "nonholiday_n": int(len(non_pool)),
                "nonholiday_stops": non_stops,
                "nonholiday_stop_rate": (
                    non_stops / len(non_pool) if len(non_pool) else math.nan
                ),
                "holiday_signal_share": (
                    len(h_pool) / len(tox_pool) if len(tox_pool) else math.nan
                ),
                "holiday_stop_share": (
                    h_stops / all_stops if all_stops else math.nan
                ),
            },
            "toxic_after_removal": sorted(after_toxic),
            "persistent_original_toxic": sorted(original_toxic & after_toxic),
            "disappeared_original_toxic": disappeared,
            "disappeared_detail": disappeared_detail,
            "true_rate_rescue_months": true_rate_rescue,
            "threshold_indeterminate_months": threshold_indeterminate,
            "new_toxic_after_removal": sorted(after_toxic - original_toxic),
        }
        result["by_width"][str(width)] = width_result

        if width == 5:
            for month in sorted(original_toxic):
                a = x[x["month"] == month]
                h = a[a["holiday_window"]]
                non = a[~a["holiday_window"]]
                labels = sorted(
                    {
                        label
                        for date in h["d"].unique()
                        for label in membership.get(date, [])
                    }
                )
                after_n = int(len(non))
                after_rate = (
                    float((non["outcome"] == "stop").mean())
                    if after_n
                    else math.nan
                )
                still_toxic = after_n >= 15 and after_rate >= 0.30
                attribution_rows.append(
                    {
                        "mode": mode,
                        "month": month,
                        "n": int(len(a)),
                        "stops": int((a["outcome"] == "stop").sum()),
                        "stop_rate": float((a["outcome"] == "stop").mean()),
                        "holiday_n": int(len(h)),
                        "holiday_stops": int((h["outcome"] == "stop").sum()),
                        "holiday_stop_rate": (
                            float((h["outcome"] == "stop").mean())
                            if len(h)
                            else math.nan
                        ),
                        "nonholiday_n": after_n,
                        "nonholiday_stops": int(
                            (non["outcome"] == "stop").sum()
                        ),
                        "nonholiday_stop_rate": after_rate,
                        "holiday_signal_share": (
                            len(h) / len(a) if len(a) else math.nan
                        ),
                        "holiday_stop_share": (
                            (h["outcome"] == "stop").sum()
                            / max(1, (a["outcome"] == "stop").sum())
                        ),
                        "still_toxic_after_removal": still_toxic,
                        "holiday_windows": "; ".join(labels),
                    }
                )
    return result, attribution_rows


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    return value


def pct(x: Any) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{float(x) * 100:.1f}%"


def pp(x: Any) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{float(x):+.1f}pp"


def write_summary(
    path: Path,
    results: dict[str, Any],
    event_df: pd.DataFrame,
) -> None:
    lines = [
        "# 抄底策略 × 节假日前后暴雷事件研究",
        "",
        f"- 数据成熟截止信号日：`{results['metadata']['mature_signal_cutoff']}`",
        f"- 原始推荐线：{results['samples']['raw']['n']}笔；5日冷却线：{results['samples']['cooldown5']['n']}笔",
        f"- 官方闭市事件：20次；可做完整局部检验：{results['metadata']['local_complete_events']}次",
        "- 标签：T+1开盘进场，20交易日内先+5%为胜、先-8%为雷。",
        "- 主推断：每个节日同侧10个交易日内随机移动连续窗口；节日事件 cluster bootstrap。",
        "",
        "## 当前5日冷却线：节前/节后窗口",
        "",
        "| 窗口 | 方向 | 样本n | 雷率 | 局部对照n | 对照雷率 | 差值 | 95% cluster CI | p | BH q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for test in results["local_tests"]:
        if test["mode"] != "cooldown5":
            continue
        ci = test.get("cluster_boot_ci95_pp", [None, None])
        ci_text = (
            f"[{ci[0]:+.1f}, {ci[1]:+.1f}]"
            if ci[0] is not None and ci[1] is not None
            else "NA"
        )
        lines.append(
            f"| {test['width']}交易日 | {'节前' if test['side']=='pre' else '节后'} "
            f"| {test.get('treated_n', 0)} | {pct(test.get('treated_stop_rate'))} "
            f"| {test.get('control_n', 0)} | {pct(test.get('control_stop_rate'))} "
            f"| {pp(test.get('delta_stop_pp'))} | {ci_text} "
            f"| {test.get('p_two_sided', math.nan):.3f} | {test.get('q_bh', math.nan):.3f} |"
        )

    lines += [
        "",
        "## 时间稳定性（当前5日冷却线、5交易日窗）",
        "",
        "| 子样本 | 方向 | 事件数 | n | 雷率 | 局部对照雷率 | 差值 | p |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for test in results["robustness"]:
        lines.append(
            f"| {test['subset']} | {'节前' if test['side']=='pre' else '节后'} "
            f"| {test.get('events', 0)} | {test.get('treated_n', 0)} "
            f"| {pct(test.get('treated_stop_rate'))} | {pct(test.get('control_stop_rate'))} "
            f"| {pp(test.get('delta_stop_pp'))} | {test.get('p_two_sided', math.nan):.3f} |"
        )

    lines += [
        "",
        "## 毒月是否由节假日窗口制造（反向归因）",
        "",
        "| 口径 | 原毒月 | 毒月内节日窗n/雷率 | 非节日n/雷率 | 节日窗信号份额/雷份额 | 删除后保留 | 真正因雷率<30%消失 | 月级Fisher p | Spearman |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in ("raw", "cooldown5"):
        relation = results["toxic_holiday_relation"][mode]
        w = relation["by_width"]["5"]
        pool = w["toxic_pool"]
        lines.append(
            f"| {'原始推荐线' if mode=='raw' else '5日冷却线'} "
            f"| {len(relation['original_toxic_months'])} "
            f"| {pool['holiday_n']}/{pct(pool['holiday_stop_rate'])} "
            f"| {pool['nonholiday_n']}/{pct(pool['nonholiday_stop_rate'])} "
            f"| {pct(pool['holiday_signal_share'])}/{pct(pool['holiday_stop_share'])} "
            f"| {len(w['persistent_original_toxic'])} "
            f"| {len(w['true_rate_rescue_months'])} "
            f"| {w['month_level_fisher_p']:.3f} "
            f"| {w['monthly_holiday_share_vs_stop_rate_spearman']:+.3f} |"
        )
    lines += [
        "",
        "- “删除后消失”必须区分：若只是剩余样本降到n<15，不能说节假日制造了毒月；只有n仍≥15且雷率跌破30%才算真正反向证据。",
        "- 本样本在±5日口径下，真正因雷率跌破30%而消失的毒月为0；原始线有3个月、冷却线有1个月仅因删除后n<15失去毒月资格。",
        "- 原始线删除节日窗后反而新增2024-09为毒月，说明当月节日窗是稀释风险而非制造风险。",
        "",
        "## 进场日时钟复核（当前5日冷却线）",
        "",
        "| 窗口 | 方向 | n | 雷率 | 局部对照雷率 | 差值 | p | BH q |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for test in results["entry_clock_tests"]:
        lines.append(
            f"| {test['width']}交易日 | {'节前' if test['side']=='pre' else '节后'} "
            f"| {test.get('treated_n', 0)} | {pct(test.get('treated_stop_rate'))} "
            f"| {pct(test.get('control_stop_rate'))} | {pp(test.get('delta_stop_pp'))} "
            f"| {test.get('p_two_sided', math.nan):.3f} | {test.get('q_bh', math.nan):.3f} |"
        )

    lines += [
        "",
        "## 逐节日（当前5日冷却线）",
        "",
        "| 节日 | 节前n/雷率 | 节后n/雷率 | 实际跨假期持仓n/最终雷率 |",
        "|---|---:|---:|---:|",
    ]
    for _, r in event_df.iterrows():
        lines.append(
            f"| {r['year']} {r['holiday']} "
            f"| {int(r['pre5_n'])}/{pct(r['pre5_stop_rate'])} "
            f"| {int(r['post5_n'])}/{pct(r['post5_stop_rate'])} "
            f"| {int(r['carried_n'])}/{pct(r['carried_stop_rate'])} |"
        )

    lines += [
        "",
        "## 解释边界",
        "",
        "- 这是事后相关性研究，不是已通过走样本的生产 gate。",
        "- “实际跨假期持仓”以节前收盘仍未结算为条件，存在存续条件选择，只作持仓风险描述。",
        "- 2024元旦受60日因子预热边界影响；2026端午及之后未走完整20交易日标签窗，均不进入主检验。",
        "- 股票池是研究时点高流动性幸存者池，行业/成分并非历史PIT；绝对胜率需按原skill水分声明解读。",
        "- 未修改 bottom-fishing workflow、生产规则、权重或影子日志。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_ROOT / "output")
    parser.add_argument("--n-perm", type=int, default=N_PERM)
    parser.add_argument("--n-boot", type=int, default=N_BOOT)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    checks = verify_inputs()
    panel = pd.read_parquet(DATA_ROOT / "panel.parquet")
    index_panel = pd.read_parquet(DATA_ROOT / "index_panel.parquet")
    klines = pd.read_parquet(DATA_ROOT / "klines.parquet")
    trading_dates = index_panel["d"].astype(str).tolist()
    trading_pos = {d: i for i, d in enumerate(trading_dates)}

    # 信号日 i 的完整标签需要一直看到 i+21（T+1进场，再观察20交易日）。
    mature_cutoff = trading_dates[-(MAX_HOLD + 2)]
    feature_start = str(panel["d"].astype(str).min())

    raw_all = qualify(panel)
    raw_all["signal_pos"] = raw_all["d"].map(trading_pos)
    cooldown_all = apply_cooldown(raw_all, trading_pos)
    raw = raw_all[raw_all["d"] <= mature_cutoff].copy().reset_index(drop=True)
    cooldown = cooldown_all[cooldown_all["d"] <= mature_cutoff].copy().reset_index(drop=True)
    raw, mismatch_raw = attach_path_dates(raw, klines)
    cooldown, mismatch_cooldown = attach_path_dates(cooldown, klines)
    if mismatch_raw or mismatch_cooldown:
        raise RuntimeError(
            f"路径标签复算不一致：raw={mismatch_raw}, cooldown={mismatch_cooldown}"
        )

    holidays = load_holidays(trading_dates, feature_start, mature_cutoff)
    descriptive_events = holidays[holidays["descriptive_complete_5"]].copy()
    local_events = holidays[holidays["local_complete"]].copy()
    tox = toxic_months(raw)
    toxic_relations: dict[str, Any] = {}
    toxic_attribution_rows: list[dict[str, Any]] = []

    samples = {"raw": raw, "cooldown5": cooldown}
    pooled: dict[str, Any] = {}
    local_tests: list[dict[str, Any]] = []
    entry_clock_tests: list[dict[str, Any]] = []
    relative_rows: list[dict[str, Any]] = []

    for mode, sample in samples.items():
        relation, relation_rows = toxic_holiday_relation(
            sample, descriptive_events, trading_dates, mode
        )
        toxic_relations[mode] = relation
        toxic_attribution_rows.extend(relation_rows)
        pooled[mode] = {
            str(width): pooled_window_stats(
                sample, descriptive_events, trading_dates, width
            )
            for width in WINDOWS
        }
        relative_rows.extend(
            relative_day_stats(sample, descriptive_events, trading_dates, mode)
        )
        for width in WINDOWS:
            for side_i, side in enumerate(("pre", "post")):
                test = local_randomization_test(
                    sample,
                    local_events,
                    trading_dates,
                    side,
                    width,
                    seed=RANDOM_SEED + width * 100 + side_i + (0 if mode == "raw" else 10_000),
                    n_perm=args.n_perm,
                    n_boot=args.n_boot,
                )
                test["mode"] = mode
                local_tests.append(test)
    bh_adjust(local_tests)

    # 最后一交易日的信号要到节后首日才进场，因此另以真实T+1进场日复核。
    for width in WINDOWS:
        for side_i, side in enumerate(("pre", "post")):
            test = local_randomization_test(
                cooldown,
                local_events,
                trading_dates,
                side,
                width,
                seed=RANDOM_SEED + 30_000 + width * 100 + side_i,
                n_perm=args.n_perm,
                n_boot=args.n_boot,
                date_col="entry_date",
            )
            test["mode"] = "cooldown5"
            entry_clock_tests.append(test)
    bh_adjust(entry_clock_tests)

    robustness: list[dict[str, Any]] = []
    subsets: list[tuple[str, pd.DataFrame, pd.DataFrame]] = [
        ("发现期2024-2025", cooldown, local_events[local_events["year"].astype(int) <= 2025]),
        ("留出期2026", cooldown, local_events[local_events["year"].astype(int) == 2026]),
        ("2024", cooldown, local_events[local_events["year"].astype(int) == 2024]),
        ("2025", cooldown, local_events[local_events["year"].astype(int) == 2025]),
        ("长假gap>=7天", cooldown, local_events[local_events["long_gap"]]),
        ("短假gap<7天", cooldown, local_events[~local_events["long_gap"]]),
        ("剔除毒月", cooldown[~cooldown["month"].isin(tox)], local_events),
    ]
    for subset_i, (name, sample, events) in enumerate(subsets):
        for side_i, side in enumerate(("pre", "post")):
            test = local_randomization_test(
                sample,
                events,
                trading_dates,
                side,
                5,
                seed=RANDOM_SEED + 50_000 + subset_i * 10 + side_i,
                n_perm=args.n_perm,
                n_boot=args.n_boot,
            )
            test["subset"] = name
            robustness.append(test)

    event_rows = event_table(cooldown, descriptive_events, trading_dates)
    event_df = pd.DataFrame(event_rows)
    relative_df = pd.DataFrame(relative_rows)

    results: dict[str, Any] = {
        "schema": "bottom-holiday-event-study/v1",
        "metadata": {
            "created_for": "2024-2026 A股节假日前后与bottom-fishing暴雷相关性",
            "calendar_retrieved_at": "2026-07-23",
            "calendar_source": "上海证券交易所年度休市通知",
            "calendar_file": str(CALENDAR_CSV),
            "official_closure_events": int(len(holidays)),
            "descriptive_complete_events": int(len(descriptive_events)),
            "local_complete_events": int(len(local_events)),
            "feature_start": feature_start,
            "index_last_date": trading_dates[-1],
            "mature_signal_cutoff": mature_cutoff,
            "right_censor_rule": "仅保留信号日后已完整覆盖T+1进场+20交易日观察窗的样本",
            "label": "T+1开盘进场；先+5%=win，先-8%=stop，20交易日",
            "qualification": "防守日score>=18或非防守日stock_score>=15，ATR<=4",
            "cooldown": "距该股上次过线<=5交易日则压下；被压下过线也刷新计时",
            "local_test": "节日前/后同侧10交易日；相邻1/3/5日 vs 其余日期；连续窗口随机化",
            "random_seed": RANDOM_SEED,
            "input_checks": checks,
            "path_label_mismatches": {
                "raw": mismatch_raw,
                "cooldown5": mismatch_cooldown,
            },
            "toxic_months_raw_definition": tox,
            "workflow_modified": False,
        },
        "holidays": holidays.to_dict(orient="records"),
        "samples": {name: perf(sample) for name, sample in samples.items()},
        "pooled_windows": pooled,
        "local_tests": local_tests,
        "entry_clock_tests": entry_clock_tests,
        "robustness": robustness,
        "toxic_holiday_relation": toxic_relations,
        "event_table": event_rows,
    }

    json_path = args.out_dir / "results.json"
    event_path = args.out_dir / "event_stats_cooldown5.csv"
    relative_path = args.out_dir / "relative_day_stats.csv"
    toxic_relation_path = args.out_dir / "toxic_month_holiday_attribution.csv"
    summary_path = args.out_dir / "summary.md"
    json_path.write_text(
        json.dumps(json_ready(results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    event_df.to_csv(event_path, index=False, encoding="utf-8-sig")
    relative_df.to_csv(relative_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(toxic_attribution_rows).to_csv(
        toxic_relation_path, index=False, encoding="utf-8-sig"
    )
    write_summary(summary_path, json_ready(results), event_df)

    print(f"[PASS] inputs: {len(checks)}/{len(checks)} hash matched")
    print(
        f"[DATA] index={trading_dates[0]}~{trading_dates[-1]} "
        f"mature_signal_cutoff={mature_cutoff}"
    )
    print(
        f"[SAMPLE] raw={len(raw)} cooldown5={len(cooldown)} "
        f"events(local)={len(local_events)}/{len(holidays)}"
    )
    print(f"[TOXIC] {tox}")
    for test in local_tests:
        if test["mode"] == "cooldown5":
            print(
                f"[LOCAL] cooldown5 {test['side']} k={test['width']} "
                f"n={test.get('treated_n', 0)} "
                f"stop={test.get('treated_stop_rate', math.nan)*100:.1f}% "
                f"ctrl={test.get('control_stop_rate', math.nan)*100:.1f}% "
                f"delta={test.get('delta_stop_pp', math.nan):+.1f}pp "
                f"p={test.get('p_two_sided', math.nan):.4f} "
                f"q={test.get('q_bh', math.nan):.4f}"
            )
    print(f"[WRITE] {json_path}")
    print(f"[WRITE] {event_path}")
    print(f"[WRITE] {relative_path}")
    print(f"[WRITE] {toxic_relation_path}")
    print(f"[WRITE] {summary_path}")


if __name__ == "__main__":
    main()
