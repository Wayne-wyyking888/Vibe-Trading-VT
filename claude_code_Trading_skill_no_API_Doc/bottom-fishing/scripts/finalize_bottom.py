#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动恢复并发布 bottom-fishing 裁定版。

前置条件：Codex 已完成 bottom_adjudication.json 的 Agent②/③审计。
本脚本固定执行：基线→派生字段归一→搜索审计→裁定/ETF自动重试→跨源验价→附审计→
报告增强→品牌清理→最终硬验收→HTML内容巡检。任何中间失败均不得发布。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import time
from typing import Any


SKILL = pathlib.Path(__file__).resolve().parents[1]
REPO = pathlib.Path(__file__).resolve().parents[3]
ACCEPTANCE = REPO / "codex_acceptance" / "acceptance.py"
RUN_ENGINE = REPO / "codex_acceptance" / "run_engine.py"
VERIFY_PRICES = REPO / "codex_acceptance" / "verify_prices.py"
NORMALIZE_AUDIT = SKILL / "scripts" / "normalize_bottom_audit.py"
STATE = SKILL / "state"
RESULT = STATE / "bottom_latest.json"
AUDIT = STATE / "bottom_adjudication.json"
PRICE_RESULT = STATE / "codex_price_verification.json"


def _run(args: list[str], *, accepted: set[int] | None = None) -> int:
    print("[auto]", " ".join(args), flush=True)
    completed = subprocess.run(args, cwd=str(REPO), check=False)
    accepted = accepted or {0}
    if completed.returncode not in accepted:
        raise RuntimeError(f"命令失败({completed.returncode}): {' '.join(args)}")
    return completed.returncode


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _etf_complete(result: dict[str, Any]) -> tuple[bool, str]:
    candidates = list(result.get("candidates") or [])
    meta = result.get("etf_holdings_meta") or {}
    if not candidates:
        return meta.get("status") == "no_candidates", str(meta.get("status"))
    statuses = {
        str(row.get("code")): str((row.get("etf_holdings") or {}).get("status"))
        for row in candidates
    }
    ok = (meta.get("status") == "ok" and meta.get("online_refresh") is True and
          all(status in {"ok", "no_etf"} for status in statuses.values()))
    return ok, f"meta={meta.get('status')} candidates={statuses}"


def _report_path(result: dict[str, Any]) -> pathlib.Path:
    stamp = dt.datetime.fromisoformat(str(result.get("adjudicated_at")))
    name = f"bottom_cn_{stamp.strftime('%Y-%m-%d_%H-%M-%S')}_裁定版.html"
    return SKILL / "reports" / name


def _merge_price_verification() -> None:
    price = _load(PRICE_RESULT)
    audit = _load(AUDIT)
    codex_audit = audit.setdefault("codex_audit", {})
    codex_audit["price_verification_by_code"] = price.get("price_verification_by_code") or {}
    _write_atomic(AUDIT, audit)


def _html_qa(path: pathlib.Path, result: dict[str, Any]) -> None:
    raw = path.read_text(encoding="utf-8-sig")
    forbidden = (
        "数据获取失败",
        "未识别明确相对受益板块",
        "未识别明确相对承压板块",
    )
    found = [text for text in forbidden if text in raw]
    if found:
        raise RuntimeError(f"HTML仍含禁止发布占位/失败文案: {found}")
    candidates = list(result.get("candidates") or [])
    for row in candidates:
        code = str(row.get("code") or "")
        required = (
            f"codex-bottom-etf:{code}:start",
            f"codex-bottom-ruling-evidence:{code}:start",
        )
        missing = [marker for marker in required if marker not in raw]
        if missing:
            raise RuntimeError(f"HTML {code} 缺自动增强区块: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description="bottom-fishing 自动恢复发布器")
    parser.add_argument("--attempts", type=int, default=3, help="ETF/验价最大自动重试次数")
    args = parser.parse_args()
    attempts = max(1, min(int(args.attempts), 6))
    py = sys.executable

    _run([py, str(ACCEPTANCE), "baseline"])
    _run([py, str(NORMALIZE_AUDIT), "--audit", str(AUDIT)])
    _run([py, str(ACCEPTANCE), "validate-bottom-search",
          "--result", str(RESULT), "--audit", str(AUDIT)])

    final_result: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        _run([py, str(RUN_ENGINE), "bottom", "--", "--adjudicate"])
        final_result = _load(RESULT)
        complete, detail = _etf_complete(final_result)
        print(f"[auto] ETF attempt {attempt}/{attempts}: {detail}", flush=True)
        if complete:
            break
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    else:
        raise RuntimeError("ETF在线刷新与备用端点重试后仍未完整，不发布HTML")

    assert final_result is not None
    report = _report_path(final_result)
    if not report.is_file():
        raise RuntimeError(f"裁定版HTML不存在: {report}")

    for attempt in range(1, attempts + 1):
        code = subprocess.run(
            [py, str(VERIFY_PRICES), "--skill", "bottom-fishing", "--result", str(RESULT),
             "--out", str(PRICE_RESULT)],
            cwd=str(REPO),
            check=False,
        ).returncode
        if code == 0:
            break
        if attempt < attempts:
            time.sleep(1.5 * attempt)
    else:
        raise RuntimeError("跨源收盘价自动换源重试后仍未全部 verified，不发布HTML")

    _merge_price_verification()
    _run([py, str(ACCEPTANCE), "attach-audit", "--result", str(RESULT), "--audit", str(AUDIT)])
    _run([py, str(ACCEPTANCE), "augment-report", "--skill", "bottom-fishing",
          "--json", str(RESULT), "--html", str(report)])
    _run([py, str(ACCEPTANCE), "brand-report", "--html", str(report)])
    _run([py, str(ACCEPTANCE), "validate", "--skill", "bottom-fishing",
          "--json", str(RESULT), "--html", str(report), "--require-bottom-search"])
    final_result = _load(RESULT)
    _html_qa(report, final_result)
    print(f"[PASS] final_report={report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
