#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import pathlib
import tempfile
import types
import unittest
from unittest import mock

import run_engine
from acceptance import validate_bottom_etf, validate_html
from bottom_etf import VERSION, _is_etf_row, _similarity, inject_etf_sections


class BottomEtfTest(unittest.TestCase):
    def test_etf_filter_excludes_link_fund(self) -> None:
        base = {
            "FUND_CODE": "510300",
            "FUND_DERIVECODE": "510300.SH",
            "HOLDER_NAME": "华泰柏瑞沪深300ETF",
            "F9_HOLDER_NAME": "华泰柏瑞沪深300交易型开放式指数证券投资基金",
        }
        self.assertTrue(_is_etf_row(base))
        link = dict(base, FUND_CODE="460300", FUND_DERIVECODE="460300.OF",
                    HOLDER_NAME="沪深300ETF联接", F9_HOLDER_NAME="沪深300ETF联接基金")
        self.assertFalse(_is_etf_row(link))

    def test_similarity_uses_returns_and_t_cutoff(self) -> None:
        dates = [f"2026-01-{day:02d}" for day in range(1, 32)] + [f"2026-02-{day:02d}" for day in range(1, 32)]
        stock = [[date, 100 + i * 0.4 + (i % 3)] for i, date in enumerate(dates)]
        close = [100.0]
        for i in range(1, len(dates)):
            stock_ret = stock[i][1] / stock[i - 1][1]
            close.append(close[-1] * stock_ret)
        etf = [[date, value] for date, value in zip(dates, close)]
        result = _similarity(stock, etf, "2026-02-28")
        self.assertIsNotNone(result)
        self.assertGreater(result["correlation"], 0.999)
        self.assertEqual(result["window_end"], "2026-02-28")

    def _result(self) -> dict:
        holding = {
            "version": VERSION,
            "status": "ok",
            "used_in_recommendation": False,
            "report_date": "2026-03-31",
            "report_name": "2026年一季报",
            "complete": True,
            "newer_incomplete_report_name": "2026年中报",
            "source_url": "https://data.eastmoney.com/example",
            "holding_etf_count": 1,
            "similarity_universe_count": 1,
            "similarity_success_count": 1,
            "all_etfs": [{
                "code": "510300", "name": "沪深300ETF", "holding_weight_pct": 4.2,
                "holding_value_yuan": 1e8, "correlation": 0.91, "path_rmse": 2.0,
                "common_return_days": 60, "etf_return_pct": -3.2,
                "window_start": "2026-05-08", "window_end": "2026-07-31",
                "similarity_status": "ok", "similarity_rank": 1,
            }],
            "ranked": [{
                "code": "510300", "name": "沪深300ETF", "holding_weight_pct": 4.2,
                "holding_value_yuan": 1e8, "correlation": 0.91, "path_rmse": 2.0,
                "common_return_days": 60, "etf_return_pct": -3.2,
                "window_start": "2026-05-08", "window_end": "2026-07-31",
                "similarity_status": "ok", "similarity_rank": 1,
            }],
        }
        return {
            "T": "2026-07-31",
            "etf_holdings_meta": {
                "version": VERSION, "status": "ok", "used_in_recommendation": False,
                "similarity_metric": "60日收益 Pearson", "similarity_max_etfs_by_holding_weight": 80,
                "html_top": 5,
            },
            "candidates": [{"code": "300750", "etf_holdings": holding}],
        }

    def test_acceptance_enforces_t_cutoff_and_display_only(self) -> None:
        result = self._result()
        self.assertTrue(validate_bottom_etf(result).passed)
        result["candidates"][0]["etf_holdings"]["ranked"][0]["window_end"] = "2026-08-03"
        checked = validate_bottom_etf(result)
        self.assertFalse(checked.passed)
        self.assertTrue(any("越过 T 日" in message for message in checked.errors))

    def test_acceptance_allows_zero_disclosed_etfs(self) -> None:
        result = self._result()
        payload = result["candidates"][0]["etf_holdings"]
        payload.update({
            "status": "no_etf", "holding_etf_count": 0, "similarity_success_count": 0,
            "all_etfs": [], "ranked": [],
        })
        self.assertTrue(validate_bottom_etf(result).passed)

    def test_html_inserts_after_f10_and_is_idempotent(self) -> None:
        raw = (
            "<style></style><div class=card><span>300750</span>"
            "<div class=row>F10: test</div><div class=plan>plan</div></div>"
            "<h3>观察池</h3>"
        )
        rendered = inject_etf_sections(raw, self._result())
        marker = "codex-bottom-etf:300750:start"
        self.assertEqual(rendered.count(marker), 1)
        self.assertLess(rendered.find("F10: test"), rendered.find(marker))
        self.assertLess(rendered.find(marker), rendered.find("class=plan"))
        self.assertIn("510300", rendered)
        self.assertNotIn("展开全部公开披露ETF", rendered)
        self.assertEqual(inject_etf_sections(rendered, self._result()), rendered)

    def test_html_without_f10_still_precedes_plan(self) -> None:
        result = self._result()
        raw = ("<style></style><div class=card><span>300750</span><div class=plan>plan</div></div>"
               "<h3>观察池</h3><div>非投资建议</div>")
        rendered = inject_etf_sections(raw, result)
        self.assertLess(rendered.find("codex-bottom-etf:300750:start"), rendered.find("class=plan"))
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "no-f10.html"
            path.write_text(rendered, encoding="utf-8")
            checked = validate_html("bottom-fishing", result, path, strict=False)
        self.assertTrue(checked.passed, checked.errors)

    def test_html_shows_only_top_five(self) -> None:
        result = self._result()
        payload = result["candidates"][0]["etf_holdings"]
        rows = []
        for rank in range(1, 7):
            rows.append({
                "code": f"51030{rank}", "name": f"ETF-{rank}", "holding_weight_pct": 7 - rank,
                "holding_value_yuan": 1e8, "correlation": 1 - rank / 10, "path_rmse": rank,
                "common_return_days": 60, "etf_return_pct": rank, "window_start": "2026-05-08",
                "window_end": "2026-07-31", "similarity_status": "ok", "similarity_rank": rank,
            })
        payload["all_etfs"] = rows
        payload["ranked"] = rows
        payload["holding_etf_count"] = 6
        payload["similarity_success_count"] = 6
        raw = (
            "<style></style><div class=card><span>300750</span>"
            "<div class=row>F10: test</div><div class=plan>plan</div></div><h3>观察池</h3>"
        )
        rendered = inject_etf_sections(raw, result)
        self.assertIn("510305 ETF-5", rendered)
        self.assertNotIn("510306 ETF-6", rendered)

    def test_html_acceptance_checks_f10_placement(self) -> None:
        result = self._result()
        raw = (
            "<style></style><div class=card><span>300750</span>"
            "<div class=row>F10: test</div><div class=plan>plan</div></div>"
            "<h3>观察池</h3><div>非投资建议</div>"
        )
        rendered = inject_etf_sections(raw, result)
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "sample.html"
            path.write_text(rendered, encoding="utf-8")
            checked = validate_html("bottom-fishing", result, path, strict=False)
        self.assertTrue(checked.passed, checked.errors)

    def test_html_acceptance_allows_candidate_without_plan(self) -> None:
        result = self._result()
        raw = (
            "<style></style><div class=card><span>300750</span>"
            "<div class=row>F10: test</div></div><h3>观察池</h3><div>非投资建议</div>"
        )
        rendered = inject_etf_sections(raw, result)
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "no-plan.html"
            path.write_text(rendered, encoding="utf-8")
            checked = validate_html("bottom-fishing", result, path, strict=False)
        self.assertTrue(checked.passed, checked.errors)

    def test_wrapper_enriches_only_after_original_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            legacy = root / "legacy.html"
            out_json = root / "bottom_latest.json"
            original_saw_etf = []

            def original(result: dict) -> pathlib.Path:
                original_saw_etf.append(
                    "etf_holdings_meta" in result
                    or any("etf_holdings" in row for row in result.get("candidates") or [])
                )
                legacy.write_text("<div>T=2026-07-31</div>", encoding="utf-8")
                return legacy

            def enrich(result: dict, *_args, **_kwargs) -> dict:
                result["etf_holdings_meta"] = {
                    "version": VERSION, "status": "no_candidates", "used_in_recommendation": False,
                }
                return result

            module = types.SimpleNamespace(render_html=original, OUT_JSON=out_json)
            with mock.patch.object(run_engine, "enrich_bottom_result", enrich), \
                 mock.patch.object(run_engine, "inject_etf_sections", lambda raw, _result: raw + "<!--enhanced-->"):
                run_engine._install_bottom_cn_report_naming(module)
                result = {
                    "T": "2026-07-31", "generated_at": "2026-08-02T12:34:56+08:00",
                    "adjudicated": True, "adjudicated_at": "2026-08-02T12:35:00+08:00",
                    "etf_holdings_meta": {"version": "stale"},
                    "candidates": [{"code": "300750", "etf_holdings": {"version": "stale"}}],
                }
                rendered = module.render_html(result)

            self.assertEqual(original_saw_etf, [False])
            self.assertIn("<!--enhanced-->", rendered.read_text(encoding="utf-8"))
            self.assertIn('"etf_holdings_meta"', out_json.read_text(encoding="utf-8"))

    def test_wrapper_skips_etf_before_agent_adjudication(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            legacy = root / "legacy.html"

            def original(_result: dict) -> pathlib.Path:
                legacy.write_text("<div>T=2026-07-31</div>", encoding="utf-8")
                return legacy

            module = types.SimpleNamespace(render_html=original, OUT_JSON=root / "bottom_latest.json")
            with mock.patch.object(
                run_engine, "enrich_bottom_result", side_effect=AssertionError("初扫不得请求ETF")
            ):
                run_engine._install_bottom_cn_report_naming(module)
                result = {"T": "2026-07-31", "generated_at": "2026-08-02T12:34:56+08:00", "candidates": []}
                rendered = module.render_html(result)
            self.assertNotIn("etf_holdings_meta", result)
            self.assertNotIn("codex-bottom-etf", rendered.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
