# -*- coding: utf-8 -*-
"""Apply the frozen v0.2 winner to unresolved, uncooled check-mark shadow rows."""
from __future__ import annotations

import json
import pathlib

import pandas as pd


OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_agent15_book_filter")
FAMILIES = ("F1", "F2", "F3", "F4", "F5")


def decide(frame: pd.DataFrame, selected: dict) -> tuple[pd.Series, pd.Series]:
    active = [selected[family] for family in FAMILIES if selected.get(family)]
    count = sum(
        (frame[name].fillna(False).astype(bool).astype(int) for name in active),
        start=pd.Series(0, index=frame.index),
    )
    reject = (count >= int(selected["k"])) & ~frame.v02_unknown.fillna(True).astype(bool)
    return count, reject


def fmt_number(value: object, decimals: int = 2) -> str:
    return "NA" if pd.isna(value) else f"{float(value):.{decimals}f}"


def render(rows: pd.DataFrame, selected: dict, frozen_asof: str) -> str:
    lines = [
        "# v0.2 未结算且未冷却 ✓ shadow 观察表",
        "",
        f"- 行情冻结截至：`{frozen_asof}`；不是联网实时价格。",
        "- 涨跌口径：T+1 开盘买入价 → 冻结行情最后收盘价；尚无 T+1 行情时记为 NA。",
        f"- v0.2 唯一候选：`{selected['candidate_id']}`；该表不参与调参。",
        f"- 共 {len(rows)} 笔；v0.2 filter {int(rows.agent15_filter.sum())} 笔，保留 {int((~rows.agent15_filter).sum())} 笔。",
        "",
        "|信号日|代码|股票|T+1日|买入价|截至日|最新收盘|至今涨跌|已观察交易日|F2|F3|Agent1.5 filter|",
        "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows.itertuples(index=False):
        ret = "NA" if pd.isna(row.current_return_pct) else f"{row.current_return_pct:+.2f}%"
        lines.append(
            f"|{row.signal_date}|{row.code}|{row.name}|{row.entry_date or 'NA'}|"
            f"{fmt_number(row.entry_t1_open)}|{row.latest_quote_date}|{fmt_number(row.latest_close)}|"
            f"{ret}|{row.observed_bars}|{'是' if row.F2_B11_G025 else '否'}|"
            f"{'是' if row.F3_V180_E025 else '否'}|{'是（否决）' if row.agent15_filter else '否（保留）'}|"
        )
    lines += [
        "",
        "说明：`outcome=open` 仅表示按 +5% / -8% / 20交易日、stop-first 标签尚未结算；浮盈亏不是最终胜负。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    selected_payload = json.loads((OUT / "v02_selected_candidate.json").read_text(encoding="utf-8"))
    selected = selected_payload["selected"]
    shadow = pd.read_csv(OUT / "shadow_event_audit.csv", dtype={"code": str, "d": str})
    features = pd.read_csv(OUT / "v02_feature_matrix.csv.gz", dtype={"code": str, "d": str})
    klines = pd.read_csv(OUT / "klines.csv.gz", dtype={"code": str, "d": str})

    shadow_cool = shadow.shadow_cooldown.fillna(False).astype(bool)
    open_checks = shadow[
        (~shadow_cool) & shadow.judge.eq("✓") & shadow.outcome.eq("open")
    ].copy()
    open_checks = open_checks.merge(features, on=["code", "d"], how="inner", validate="one_to_one")
    trigger_count, rejected = decide(open_checks, selected)
    open_checks["v02_trigger_count"] = trigger_count
    open_checks["agent15_filter"] = rejected

    output_rows = []
    for row in open_checks.sort_values(["d", "code"]).itertuples(index=False):
        stock = klines[klines.code.eq(row.code)].sort_values("d")
        future = stock[stock.d.gt(row.d)]
        entry_row = future.iloc[0] if len(future) else None
        entry = float(entry_row.o) if entry_row is not None else None
        latest = stock.iloc[-1]
        current_return = (float(latest.c) / entry - 1) * 100 if entry is not None else None
        active_hits = [selected[family] for family in FAMILIES if selected.get(family) and bool(getattr(row, selected[family]))]
        output_rows.append({
            "signal_date": row.d,
            "code": row.code,
            "name": row.name,
            "entry_date": str(entry_row.d) if entry_row is not None else "",
            "entry_t1_open": entry,
            "latest_quote_date": str(latest.d),
            "latest_close": float(latest.c),
            "current_return_pct": current_return,
            "observed_bars": int(len(future)),
            "outcome": "open",
            "F2_B11_G025": bool(row.F2_B11_G025),
            "F3_V180_E025": bool(row.F3_V180_E025),
            "v02_trigger_count": int(row.v02_trigger_count),
            "selected_variant_hits": ";".join(active_hits),
            "agent15_filter": bool(row.agent15_filter),
            "v02_decision": "reject" if row.agent15_filter else "keep",
        })
    result = pd.DataFrame(output_rows)
    frozen_asof = str(klines.d.max())
    result.to_csv(OUT / "v02_open_check_watchlist.csv", index=False, encoding="utf-8-sig")
    (OUT / "v02_open_check_watchlist.md").write_text(render(result, selected, frozen_asof), encoding="utf-8")
    audit = {
        "schema": "bottom-agent15-v02-open-check-watchlist/v1",
        "scope": "cooldown=false, judge=check, outcome=open",
        "price_asof": frozen_asof,
        "return_basis": "T+1 open to latest frozen close",
        "selected_candidate": selected["candidate_id"],
        "rows": len(result),
        "filtered": int(result.agent15_filter.sum()),
        "kept": int((~result.agent15_filter).sum()),
    }
    (OUT / "v02_open_check_watchlist.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render(result, selected, frozen_asof))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
