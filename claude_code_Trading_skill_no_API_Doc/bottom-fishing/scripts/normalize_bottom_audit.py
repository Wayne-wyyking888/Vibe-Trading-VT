#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize only deterministic Agent②/③ audit fields before hard validation.

This helper never invents sources, queries, warnings, metrics, scenarios, alerts, or
sector calls. Missing research content remains a validation error for Codex to fix.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _set(container: dict[str, Any], key: str, value: Any, path: str,
         changes: list[str]) -> None:
    if container.get(key) != value:
        container[key] = value
        changes.append(path)


def normalize(document: dict[str, Any]) -> list[str]:
    audit = document.get("codex_audit")
    if not isinstance(audit, dict):
        raise ValueError("缺 codex_audit")
    toxic = audit.get("toxic_risk_warning")
    if not isinstance(toxic, dict):
        raise ValueError("缺 codex_audit.toxic_risk_warning")
    retrieved = str(toxic.get("retrieved_at_beijing", "")).strip()
    retrieved_date = _date(retrieved)
    if not retrieved or retrieved_date is None:
        raise ValueError("toxic_risk_warning.retrieved_at_beijing 无效")

    changes: list[str] = []
    _set(audit, "retrieved_on_beijing", str(retrieved_date),
         "codex_audit.retrieved_on_beijing", changes)

    predictive = toxic.get("predictive_input_coverage")
    if isinstance(predictive, dict):
        for category, item in predictive.items():
            if isinstance(item, dict):
                _set(item, "as_of_beijing", retrieved,
                     f"toxic_risk_warning.predictive_input_coverage.{category}.as_of_beijing",
                     changes)

    discovery = toxic.get("market_discovery")
    if isinstance(discovery, dict):
        _set(discovery, "evaluated_at_beijing", retrieved,
             "toxic_risk_warning.market_discovery.evaluated_at_beijing", changes)
        for group_name in ("lanes", "sector_family_coverage"):
            group = discovery.get(group_name)
            if not isinstance(group, dict):
                continue
            for item_id, item in group.items():
                if isinstance(item, dict):
                    _set(item, "as_of_beijing", retrieved,
                         f"toxic_risk_warning.market_discovery.{group_name}."
                         f"{item_id}.as_of_beijing", changes)
        movers = [item for item in discovery.get("material_movers") or []
                  if isinstance(item, dict)]
        events = [item for item in discovery.get("event_clusters") or []
                  if isinstance(item, dict)]
        _set(discovery, "unresolved_material_mover_ids", _unique([
            item.get("mover_id") for item in movers
            if item.get("catalyst_status") == "unresolved"
        ]), "toxic_risk_warning.market_discovery.unresolved_material_mover_ids", changes)
        _set(discovery, "unmapped_material_event_ids", _unique([
            item.get("event_id") for item in events
            if item.get("disposition") == "unresolved"
        ]), "toxic_risk_warning.market_discovery.unmapped_material_event_ids", changes)
        tradability_by_absorption = {
            "new_unpriced": "fresh_catalyst",
            "partially_priced": "continuation_watch",
            "priced_on_t": "continuation_watch",
            "priced_before_t": "already_priced",
            "stale": "stale_excluded",
            "unclear": "unresolved",
        }
        for event in events:
            absorption = event.get("ashare_absorption")
            status = str((absorption or {}).get("status", ""))
            if status in tradability_by_absorption:
                event_id = str(event.get("event_id", ""))
                _set(event, "tradability_flag", tradability_by_absorption[status],
                     f"toxic_risk_warning.market_discovery.event_clusters."
                     f"{event_id}.tradability_flag", changes)

    sources = {
        str(item.get("source_ref", "")): item
        for item in toxic.get("sources") or []
        if isinstance(item, dict) and str(item.get("source_ref", ""))
    }
    runtime = toxic.get("runtime_evaluation")
    runtime_refs: list[str] = []
    if isinstance(runtime, dict):
        for domain, item in runtime.items():
            if not isinstance(item, dict):
                continue
            _set(item, "evaluated_at_beijing", retrieved,
                 f"toxic_risk_warning.runtime_evaluation.{domain}.evaluated_at_beijing",
                 changes)
            refs = _unique(list(item.get("source_refs") or []))
            _set(item, "source_refs", refs,
                 f"toxic_risk_warning.runtime_evaluation.{domain}.source_refs", changes)
            runtime_refs.extend(refs)
            published = [
                _date((sources.get(ref) or {}).get("published_at")) for ref in refs
            ]
            published = [value for value in published if value is not None]
            latest = str(max(published)) if published else None
            _set(item, "latest_source_published_at", latest,
                 f"toxic_risk_warning.runtime_evaluation.{domain}.latest_source_published_at",
                 changes)

    outlook = toxic.get("ashare_runtime_outlook")
    if isinstance(outlook, dict):
        _set(outlook, "evaluated_at_beijing", retrieved,
             "toxic_risk_warning.ashare_runtime_outlook.evaluated_at_beijing", changes)
        _set(outlook, "source_refs", _unique(runtime_refs),
             "toxic_risk_warning.ashare_runtime_outlook.source_refs", changes)
        calls = [item for item in outlook.get("sector_calls") or [] if isinstance(item, dict)]
        for direction, field in (("beneficiary", "sector_beneficiaries"),
                                 ("pressure", "sector_pressures")):
            displays = []
            for call in calls:
                if call.get("direction") != direction:
                    continue
                reasons = "；".join(
                    str(value).strip() for value in call.get("reasons") or []
                    if str(value).strip()
                )
                displays.append(f"{str(call.get('sector_name', '')).strip()}（{reasons}）")
            if displays:
                _set(outlook, field, displays,
                     f"toxic_risk_warning.ashare_runtime_outlook.{field}", changes)

    warning_items = [item for item in toxic.get("warnings") or [] if isinstance(item, dict)]
    delta_items = [
        item for item in toxic.get("post_t_safety_items") or [] if isinstance(item, dict)
    ]
    by_code = toxic.get("by_code")
    if isinstance(by_code, dict):
        for code, item in by_code.items():
            if not isinstance(item, dict):
                continue
            warning_ids = _unique([
                warning.get("warning_id") for warning in warning_items
                if str(code) in {str(value) for value in warning.get("codes") or []}
            ])
            delta_ids = _unique([
                delta.get("delta_id") for delta in delta_items
                if str(code) in {str(value) for value in delta.get("codes") or []}
            ])
            _set(item, "warning_ids", warning_ids,
                 f"toxic_risk_warning.by_code.{code}.warning_ids", changes)
            _set(item, "post_t_delta_ids", delta_ids,
                 f"toxic_risk_warning.by_code.{code}.post_t_delta_ids", changes)
            linked = [entry for entry in warning_items + delta_items
                      if entry.get("warning_id") in warning_ids or entry.get("delta_id") in delta_ids]
            exposure = ("high" if any(entry.get("level") == "high" for entry in linked) else
                        "watch" if linked else "none")
            _set(item, "exposure", exposure,
                 f"toxic_risk_warning.by_code.{code}.exposure", changes)

    blocked = any(
        isinstance(item, dict) and item.get("status") == "blocked"
        for group in (toxic.get("coverage") or {}, runtime or {})
        for item in (group.values() if isinstance(group, dict) else [])
    )
    levels = [str(item.get("level", "")) for item in warning_items + delta_items]
    overall = "elevated" if "high" in levels else "watch" if levels or blocked else "clear"
    _set(toxic, "overall_status", overall,
         "toxic_risk_warning.overall_status", changes)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="同步 bottom-fishing 审计中可机械派生的字段；不补研究内容"
    )
    parser.add_argument("--audit", required=True)
    parser.add_argument("--check", action="store_true",
                        help="只报告会被同步的字段，不写文件；有差异时返回2")
    args = parser.parse_args()
    path = pathlib.Path(args.audit).resolve()
    if not path.is_file():
        raise SystemExit(f"审计文件不存在: {path}")
    document = _load(path)
    changes = normalize(document)
    if args.check:
        print(json.dumps({"status": "clean" if not changes else "needs_normalize",
                          "changes": changes}, ensure_ascii=False, indent=2))
        return 0 if not changes else 2
    if changes:
        _write_atomic(path, document)
    print(json.dumps({"status": "normalized", "changed": len(changes),
                      "fields": changes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
