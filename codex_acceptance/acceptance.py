#!/usr/bin/env python3
"""三个 Codex 交易 skill 的非侵入式验收门禁。

确定性引擎保持不变；本程序在报告被称为“最终版”前校验 JSON/HTML 与人工/Codex
裁定层。只使用 Python 标准库，不发起网络请求。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import html
import importlib.util
import json
import math
import pathlib
import re
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlsplit


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SKILLS_SOURCE = ROOT / "claude_code_Trading_skill_no_API_Doc"
DATA = pathlib.Path(r"C:\Trading_analysis\data")
MANIFEST = HERE / "baseline_manifest.json"
DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
CODE_RE = re.compile(r"(?<!\d)(?:00|30|60|68)\d{4}(?!\d)")
WEEKLY_TRADABLE_CODE_RE = re.compile(r"^(?:00|30|60)\d{4}$")


@dataclass
class Result:
    name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)

    def warn(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.warnings.append(message)

    @property
    def passed(self) -> bool:
        return not self.errors

    def merge(self, other: "Result") -> None:
        self.checks += other.checks
        self.errors.extend(f"{other.name}: {x}" for x in other.errors)
        self.warnings.extend(f"{other.name}: {x}" for x in other.warnings)


def _load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _rr_num(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, str) and ":" in value:
        value = value.split(":", 1)[0]
    return _num(value, default)


def _close(a: Any, b: Any, tol: float = 0.11) -> bool:
    na, nb = _num(a), _num(b)
    return na is not None and nb is not None and abs(na - nb) <= tol


def _code_list(items: Iterable[dict[str, Any]]) -> list[str]:
    return [str(x.get("code", "")) for x in items]


def _plain_html(raw: str) -> str:
    raw = re.sub(r"(?is)<style.*?</style>|<script.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _valid_http_url(value: Any) -> bool:
    """Accept only absolute HTTP(S) URLs without control characters or whitespace."""
    text = str(value or "")
    if not text or "\\" in text or any(ch.isspace() or ord(ch) < 32 for ch in text):
        return False
    try:
        parsed = urlsplit(text)
        parsed.port  # force validation of an explicitly supplied port
    except (TypeError, ValueError):
        return False
    return (parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname) and
            parsed.username is None and parsed.password is None)


class _LinkCollector(HTMLParser):
    """Collect decoded link targets; HTMLParser turns ``&amp;`` back into ``&``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"href", "src"} and value:
                self.urls.add(value)


def _html_link_targets(raw: str) -> set[str]:
    parser = _LinkCollector()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # The caller will fail any required link that could not be parsed.
        pass
    return parser.urls


def baseline_check() -> Result:
    out = Result("baseline")
    manifest = _load(MANIFEST)
    out.check(manifest.get("schema") == "codex-trading-baseline/v1", "基线 manifest schema 错误")
    for rel, expected in manifest.get("files", {}).items():
        path = SKILLS_SOURCE / pathlib.PurePosixPath(rel)
        out.check(path.is_file(), f"基线文件缺失: {rel}")
        if path.is_file():
            actual = _sha256(path)
            out.check(actual == expected, f"不可变引擎发生变化: {rel} expected={expected} actual={actual}")
    required_buckets = {"mom", "vol", "tech", "tape", "pull"}
    for rel in manifest.get("mutable_contract_files", {}):
        path = SKILLS_SOURCE / pathlib.PurePosixPath(rel)
        out.check(path.is_file(), f"可变状态文件缺失: {rel}")
        if not path.is_file():
            continue
        try:
            state = _load(path)
        except (OSError, json.JSONDecodeError) as exc:
            out.errors.append(f"权重状态无法解析: {rel}: {exc}")
            continue
        out.check(_date(state.get("generated_at")) is not None, f"{rel} generated_at 无效")
        out.check(isinstance(state.get("fwd_days"), int) and 1 <= state.get("fwd_days") <= 60,
                  f"{rel} fwd_days 越界")
        weights = state.get("weights") or {}
        out.check(set(weights) == required_buckets, f"{rel} 权重桶 schema 变化")
        for name, value in weights.items():
            number = _num(value)
            out.check(number is not None and 0 <= number <= 2, f"{rel} {name} 权重越界")
        shrink = _num(state.get("shrink"))
        out.check(shrink is not None and 0 <= shrink <= 1, f"{rel} shrink 越界")
        out.check(isinstance(state.get("ic"), list) and bool(state.get("ic")), f"{rel} 缺 IC 明细")
    return out


REQUIRED_AUDITOR_CHECKS = {
    "数据新鲜度",
    "跨源价格",
    "算术",
    "rubric",
    "价位逻辑",
    "引用",
}
SOURCE_TIERS = {"公告", "交易所", "主流媒体", "研报", "其他"}
F10_MATCHES = {"confirmed", "conflict", "missing", "not_applicable"}


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


def _beijing_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    labelled = text.endswith("北京时间")
    if labelled:
        text = text[:-4].strip()
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if not labelled:
            return None
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    if parsed.utcoffset() != dt.timedelta(hours=8):
        return None
    return parsed.astimezone(BEIJING_TZ)


def audit_common(obj: dict[str, Any], skill: str, required_codes: Iterable[str]) -> Result:
    out = Result(f"{skill}.codex_audit")
    audit = obj.get("codex_audit")
    out.check(isinstance(audit, dict), "缺少顶层 codex_audit；不得发布最终报告")
    if not isinstance(audit, dict):
        return out
    out.check(audit.get("version") == "codex-trading-audit/v1", "codex_audit.version 错误")
    out.check(audit.get("skill") == skill, "codex_audit.skill 与当前 skill 不符")
    retrieved = str(audit.get("retrieved_on_beijing", ""))
    out.check(bool(DATE_RE.match(retrieved)), "retrieved_on_beijing 必须是北京时间 YYYY-MM-DD")
    cutoff = str(audit.get("evidence_cutoff_beijing", ""))
    out.check(bool(re.match(r"^20\d{2}-\d{2}-\d{2}.*(?:\+08:00|北京时间)", cutoff)),
              "evidence_cutoff_beijing 必须明确北京时间")

    challenge = audit.get("contrarian_challenge") or {}
    out.check(challenge.get("completed") is True, "未执行反方挑战")
    out.check(bool(challenge.get("arguments")), "反方挑战缺少 arguments")
    out.check(bool(challenge.get("responses")), "反方挑战缺少 responses")

    review = audit.get("auditor_review") or {}
    out.check(review.get("passed") is True, "审计官复核未通过")
    out.check(not review.get("failed"), "auditor_review.failed 非空")
    checks_text = "|".join(str(x) for x in review.get("checks") or [])
    for required in REQUIRED_AUDITOR_CHECKS:
        out.check(required.lower() in checks_text.lower(), f"审计官缺机械检查: {required}")

    facts = audit.get("facts")
    out.check(isinstance(facts, list), "codex_audit.facts 必须是数组")
    covered: set[str] = set()
    cutoff_date = _date(cutoff)
    retrieved_date = _date(retrieved)
    for idx, fact in enumerate(facts or []):
        tag = f"facts[{idx}]"
        out.check(isinstance(fact, dict), f"{tag} 必须是对象")
        if not isinstance(fact, dict):
            continue
        code = str(fact.get("code", ""))
        if code:
            covered.add(code)
        out.check(bool(str(fact.get("fact", "")).strip()), f"{tag}.fact 为空")
        out.check(bool(str(fact.get("source_name", "")).strip()), f"{tag}.source_name 为空")
        out.check(_valid_http_url(fact.get("source_url")), f"{tag}.source_url 缺失或无效")
        out.check(bool(DATE_RE.match(str(fact.get("published_at", "")))), f"{tag}.published_at 缺失")
        out.check(bool(DATE_RE.match(str(fact.get("retrieved_at_beijing", "")))),
                  f"{tag}.retrieved_at_beijing 缺失")
        published_date = _date(fact.get("published_at"))
        fact_retrieved = _date(fact.get("retrieved_at_beijing"))
        out.check(published_date is not None and cutoff_date is not None and published_date <= cutoff_date,
                  f"{tag} 发布日晚于证据截止日（疑似前视）")
        out.check(fact_retrieved is not None and fact_retrieved == retrieved_date,
                  f"{tag} 检索日期与顶层 retrieved_on_beijing 不一致")
        event_date = fact.get("event_date")
        out.check(event_date is None or bool(DATE_RE.match(str(event_date))), f"{tag}.event_date 格式无效")
        out.check(fact.get("source_tier") in SOURCE_TIERS, f"{tag}.source_tier 不在允许枚举")
        out.check(fact.get("f10_match") in F10_MATCHES, f"{tag}.f10_match 不在允许枚举")
        out.check(_num(fact.get("rubric_delta")) is not None, f"{tag}.rubric_delta 必须是数值")
        out.check(bool(str(fact.get("reasoning", "")).strip()), f"{tag}.reasoning 为空")
        out.check(bool(str(fact.get("counter_argument", "")).strip()), f"{tag}.counter_argument 为空")
        out.check(bool(str(fact.get("conclusion", "")).strip()), f"{tag}.conclusion 为空")
        out.check(isinstance(fact.get("uncertainties"), list), f"{tag}.uncertainties 必须是数组")
    for code in required_codes:
        out.check(str(code) in covered, f"关键标的 {code} 没有结构化证据记录；无证据也必须留痕")
    return out


MATERIAL_NOTICE_RE = re.compile(
    r"业绩|预告|减值|诉讼|立案|问询|监管|处罚|解禁|减持|增持|回购|"
    r"中标|合同|订单|重组|收购|批件|批复|担保|质押|可转债|停牌|退市|风险警示"
)


def _facts_for(obj: dict[str, Any], code: str) -> list[dict[str, Any]]:
    audit = obj.get("codex_audit") or {}
    return [x for x in audit.get("facts") or [] if isinstance(x, dict) and str(x.get("code", "")) == code]


def _has_f10_fact(facts: list[dict[str, Any]], date: str, url: str | None = None) -> bool:
    for fact in facts:
        if fact.get("f10_match") not in {"confirmed", "conflict"}:
            continue
        if url and str(fact.get("source_url", "")) == url:
            return True
        if date and date in {str(fact.get("published_at", "")), str(fact.get("event_date", ""))}:
            return True
    return False


def f10_cross_check(obj: dict[str, Any], skill: str) -> Result:
    """确保引擎 F10 客观种子中的实质性事件没有被裁定层静默漏掉。"""
    out = Result(f"{skill}.f10-cross")
    if skill == "stock-diagnostic":
        code = str(obj.get("code", ""))
        facts = _facts_for(obj, code)
        f10 = obj.get("f10") or {}
        fc = f10.get("forecast") or {}
        if fc.get("notice_date"):
            date = str(fc.get("notice_date"))
            out.check(_has_f10_fact(facts, date), f"{code} F10 业绩预告 {date} 未在 facts 交叉留痕")
        for lift in f10.get("lift") or []:
            if int(lift.get("days_to", 9999) or 9999) <= 90:
                event_date = str(lift.get("date", ""))
                out.check(_has_f10_fact(facts, event_date), f"{code} F10 解禁 {event_date} 未在 facts 留痕")
    elif skill == "bottom-fishing":
        t_date = _date(obj.get("T"))
        for row in obj.get("candidates") or []:
            code = str(row.get("code", ""))
            facts = _facts_for(obj, code)
            fc = row.get("forecast") or {}
            if fc.get("notice_date") and row.get("f10_flag"):
                date = str(fc.get("notice_date"))
                seed_date = _date(date)
                if seed_date is not None and not (t_date and seed_date > t_date):
                    out.check(_has_f10_fact(facts, date), f"{code} F10 负面预告 {date} 未被确认/证伪")
            for notice in row.get("notices") or []:
                text = str(notice)
                if MATERIAL_NOTICE_RE.search(text):
                    date = text[:10] if DATE_RE.match(text[:10]) else ""
                    seed_date = _date(date)
                    if t_date and seed_date and seed_date > t_date:
                        continue
                    if seed_date is None:
                        continue
                    out.check(bool(date) and _has_f10_fact(facts, date),
                              f"{code} 实质性 F10 公告未留痕: {text[:60]}")
    else:
        for row in obj.get("candidates") or []:
            code = str(row.get("code", ""))
            facts = _facts_for(obj, code)
            for notice in row.get("recent_notices") or []:
                title = str(notice.get("title", ""))
                if notice.get("fresh") and MATERIAL_NOTICE_RE.search(title):
                    date, url = str(notice.get("date", "")), str(notice.get("url", ""))
                    out.check(_has_f10_fact(facts, date, url),
                              f"{code} 新鲜实质公告未逐条留痕: {date} {title}")
            fc = row.get("forecast") or {}
            if fc.get("fresh") and fc.get("notice_date"):
                date = str(fc.get("notice_date"))
                out.check(_has_f10_fact(facts, date), f"{code} 新鲜业绩预告 {date} 未交叉留痕")
    return out


BOTTOM_SEARCH_CATEGORIES = (
    "performance_operations",
    "financial_credit",
    "governance_regulatory",
    "corporate_events",
    "industry_policy_domestic",
    "external_global_peer",
)
BOTTOM_SOURCE_KINDS = {
    "official_direct",
    "verified_official_mirror",
    "independent_media",
    "independent_research",
    "unverified_secondary",
}
BOTTOM_QUERY_MODES = {
    "official",
    "exact_title",
    "broad_web",
    "regulator",
    "industry",
    "overseas",
    "ah_cross_listing",
    "drop_cause",
    "freshness_delta",
}
BOTTOM_MATCH_BASIS = {"title", "published_at", "document_id", "full_text"}
BOTTOM_VERDICT_RANK = {"✗": 0, "?": 1, "✓": 2}
BOTTOM_CATEGORY_LOOKBACK_DAYS = {
    "performance_operations": 180,
    "financial_credit": 365,
    "governance_regulatory": 730,
    "corporate_events": 180,
    "industry_policy_domestic": 90,
    "external_global_peer": 90,
}


def _string_list(value: Any) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def _bottom_verdicts(result_obj: dict[str, Any], audit_doc: dict[str, Any] | None) -> dict[str, str]:
    if audit_doc is None:
        return {
            str(row.get("code", "")): str(row.get("judge", ""))
            for row in result_obj.get("candidates") or []
            if row.get("judge") in BOTTOM_VERDICT_RANK
        }
    verdicts: dict[str, str] = {}
    rulings = audit_doc.get("rulings") or {}
    if isinstance(rulings, dict):
        for code, row in rulings.items():
            if isinstance(row, dict) and row.get("verdict") in BOTTOM_VERDICT_RANK:
                verdicts[str(code)] = str(row.get("verdict"))
    return verdicts


def _bottom_expected_seeds(result_obj: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return one deterministic expected entry per raw forecast/notice seed."""
    expected: dict[str, dict[str, Any]] = {}
    for row in result_obj.get("candidates") or []:
        code = str(row.get("code", ""))
        forecast = row.get("forecast")
        if isinstance(forecast, dict) and forecast:
            text = " ".join(str(forecast.get(key) or "").strip()
                            for key in ("notice_date", "type", "content")).strip()
            date = str(forecast.get("notice_date") or "")
            forecast_date = _date(date)
            forecast_type = str(forecast.get("type") or "")
            expected[f"{code}:forecast"] = {
                "code": code,
                "kind": "forecast",
                "raw_index": None,
                "seed_text": text,
                "raw_date": date if forecast_date else None,
                "material": bool(forecast.get("fresh")) or
                            any(flag in forecast_type for flag in ("预亏", "预减", "略减", "续亏", "首亏")),
            }
        for idx, notice in enumerate(row.get("notices") or []):
            text = str(notice)
            date = text[:10] if _date(text[:10]) else None
            expected[f"{code}:notices:{idx}"] = {
                "code": code,
                "kind": "notice",
                "raw_index": idx,
                "seed_text": text,
                "raw_date": date,
                "material": bool(MATERIAL_NOTICE_RE.search(text)),
            }
    return expected


def validate_bottom_search(result_obj: dict[str, Any], audit_doc: dict[str, Any] | None = None,
                           required: bool = False) -> Result:
    """Validate the versioned bottom-search evidence graph without network access."""
    out = Result("bottom-search")
    pre_adjudication = audit_doc is not None
    document = audit_doc if isinstance(audit_doc, dict) else result_obj
    audit = document.get("codex_audit") or {}
    search = audit.get("bottom_search")
    if not isinstance(search, dict):
        if required:
            out.check(False, "缺 codex_audit.bottom_search；新裁定不得发布")
        return out

    out.check({"version", "T", "cutoff_beijing", "retrieved_at_beijing", "required_categories",
               "sources", "queries", "coverage_by_code", "market_coverage", "f10_seed_ledger",
               "post_t_safety_by_code"} <= set(search), "bottom_search 缺必需字段")

    out.check(search.get("version") == "bottom-search-audit/v1", "bottom_search.version 错误")
    t = str(result_obj.get("T", ""))
    t_date = _date(t)
    t_start = dt.datetime.combine(t_date, dt.time.min, tzinfo=BEIJING_TZ) if t_date else None
    out.check(search.get("T") == t and t_date is not None, "bottom_search.T 与引擎 T 不一致")
    cutoff = str(search.get("cutoff_beijing", ""))
    cutoff_dt = _beijing_datetime(cutoff)
    expected_cutoff = dt.datetime.combine(t_date, dt.time(23, 59, 59), tzinfo=BEIJING_TZ) if t_date else None
    out.check(cutoff_dt is not None and cutoff_dt == expected_cutoff,
              "bottom_search.cutoff_beijing 必须是 T 23:59:59+08:00")
    out.check(cutoff == str(audit.get("evidence_cutoff_beijing", "")),
              "bottom_search.cutoff_beijing 与 codex_audit.evidence_cutoff_beijing 不一致")
    retrieved_text = str(search.get("retrieved_at_beijing", ""))
    retrieved_dt = _beijing_datetime(retrieved_text)
    retrieved_date = _date(retrieved_text)
    out.check(retrieved_dt is not None and t_start is not None and retrieved_dt >= t_start,
              "bottom_search.retrieved_at_beijing 无效或早于 T")
    out.check(retrieved_date == _date(audit.get("retrieved_on_beijing")),
              "bottom_search.retrieved_at_beijing 与 codex_audit.retrieved_on_beijing 不一致")
    out.check(set(_string_list(search.get("required_categories"))) == set(BOTTOM_SEARCH_CATEGORIES) and
              len(_string_list(search.get("required_categories"))) == len(BOTTOM_SEARCH_CATEGORIES),
              "required_categories 必须精确包含六个固定维度")

    candidates = list(result_obj.get("candidates") or [])
    codes = {str(row.get("code", "")) for row in candidates}
    verdicts = _bottom_verdicts(result_obj, document if pre_adjudication else None)
    if pre_adjudication:
        out.check(document.get("T") == t, "裁定文件 T 与引擎结果 T 不一致")
        rulings = document.get("rulings")
        out.check(isinstance(rulings, dict) and set(rulings) == codes,
                  "裁定文件 rulings 必须与候选代码一一对应")
        if isinstance(rulings, dict):
            for code, ruling in rulings.items():
                out.check(isinstance(ruling, dict) and ruling.get("verdict") in BOTTOM_VERDICT_RANK,
                          f"裁定文件 {code}.verdict 无效")
    if candidates:
        out.check(set(verdicts) >= codes, "搜索审计缺候选裁定 verdict")
    else:
        out.check(bool(str(search.get("empty_reason", "")).strip()), "零候选搜索审计必须写 empty_reason")
        for field_name in ("sources", "queries", "f10_seed_ledger"):
            out.check(search.get(field_name) == [], f"零候选时 {field_name} 必须为空数组")
        for field_name in ("coverage_by_code", "post_t_safety_by_code"):
            out.check(search.get(field_name) == {}, f"零候选时 {field_name} 必须为空对象")

    # Facts become graph nodes under this version; old facts remain compatible when the
    # bottom_search object is absent.
    facts = audit.get("facts") or []
    out.check(isinstance(facts, list), "codex_audit.facts 必须是数组")
    fact_by_id: dict[str, dict[str, Any]] = {}
    for idx, fact in enumerate(facts if isinstance(facts, list) else []):
        tag = f"facts[{idx}]"
        out.check(isinstance(fact, dict), f"{tag} 必须是对象")
        if not isinstance(fact, dict):
            continue
        out.check({"fact_id", "category", "source_ref", "seed_refs"} <= set(fact),
                  f"{tag} 缺 bottom-search 图字段")
        fact_id = str(fact.get("fact_id", ""))
        out.check(bool(fact_id) and fact_id not in fact_by_id, f"{tag}.fact_id 缺失或重复")
        if fact_id and fact_id not in fact_by_id:
            fact_by_id[fact_id] = fact
        code = str(fact.get("code", ""))
        category = str(fact.get("category", ""))
        out.check(code in codes or code == "MARKET", f"{tag}.code 不属于候选或 MARKET")
        out.check(category in BOTTOM_SEARCH_CATEGORIES or (code == "MARKET" and category == "market_regime"),
                  f"{tag}.category 不在允许枚举")
        out.check(bool(str(fact.get("source_ref", ""))), f"{tag}.source_ref 为空")
        out.check(isinstance(fact.get("seed_refs"), list), f"{tag}.seed_refs 必须是数组")
        published = _date(fact.get("published_at"))
        out.check(published is not None and t_date is not None and published <= t_date,
                  f"{tag} 不得使用 T 后事实")

    sources = search.get("sources") or []
    out.check(isinstance(sources, list), "bottom_search.sources 必须是数组")
    source_by_ref: dict[str, dict[str, Any]] = {}
    origin_by_canonical: dict[str, str] = {}
    origin_by_access: dict[str, str] = {}
    for idx, source in enumerate(sources if isinstance(sources, list) else []):
        tag = f"bottom_search.sources[{idx}]"
        out.check(isinstance(source, dict), f"{tag} 必须是对象")
        if not isinstance(source, dict):
            continue
        out.check({"source_ref", "access_url", "access_publisher", "source_kind", "origin_id",
                   "canonical_url", "canonical_publisher", "match_basis"} <= set(source),
                  f"{tag} 缺来源血缘字段")
        ref = str(source.get("source_ref", ""))
        out.check(bool(ref) and ref not in source_by_ref, f"{tag}.source_ref 缺失或重复")
        if ref and ref not in source_by_ref:
            source_by_ref[ref] = source
        access_url = str(source.get("access_url", ""))
        canonical_url = str(source.get("canonical_url", ""))
        kind = str(source.get("source_kind", ""))
        out.check(_valid_http_url(access_url), f"{tag}.access_url 无效")
        out.check(bool(str(source.get("access_publisher", "")).strip()), f"{tag}.access_publisher 为空")
        out.check(kind in BOTTOM_SOURCE_KINDS, f"{tag}.source_kind 无效")
        out.check(bool(str(source.get("origin_id", "")).strip()), f"{tag}.origin_id 为空")
        if kind == "official_direct":
            out.check(canonical_url == access_url, f"{tag} 官方直源 canonical_url 必须等于 access_url")
            out.check(bool(str(source.get("canonical_publisher", "")).strip()),
                      f"{tag}.canonical_publisher 为空")
        elif kind == "verified_official_mirror":
            basis = set(_string_list(source.get("match_basis")))
            out.check(_valid_http_url(canonical_url) and canonical_url != access_url,
                      f"{tag} 已验证镜像必须回溯到不同的官方 canonical_url")
            out.check(bool(str(source.get("canonical_publisher", "")).strip()),
                      f"{tag} 已验证镜像缺官方发布者")
            out.check(bool(str(source.get("document_id", "")).strip()),
                      f"{tag} 已验证镜像缺稳定 document_id")
            out.check(len(basis & BOTTOM_MATCH_BASIS) >= 2 and basis <= BOTTOM_MATCH_BASIS,
                      f"{tag} 已验证镜像至少要有两项合法 match_basis")
        elif canonical_url:
            out.check(_valid_http_url(canonical_url), f"{tag}.canonical_url 无效")
        if canonical_url:
            prior_origin = origin_by_canonical.get(canonical_url)
            origin_id = str(source.get("origin_id", ""))
            out.check(prior_origin in (None, origin_id),
                      f"{tag} 同一 canonical_url 必须共享同一 origin_id")
            if prior_origin is None:
                origin_by_canonical[canonical_url] = origin_id
        prior_access_origin = origin_by_access.get(access_url)
        origin_id = str(source.get("origin_id", ""))
        out.check(prior_access_origin in (None, origin_id),
                  f"{tag} 同一 access_url 必须共享同一 origin_id")
        if prior_access_origin is None:
            origin_by_access[access_url] = origin_id

    for fact_id, fact in fact_by_id.items():
        source = source_by_ref.get(str(fact.get("source_ref", "")))
        out.check(source is not None, f"{fact_id} source_ref 无法回指 sources")
        if source is not None:
            out.check(str(fact.get("source_url", "")) == str(source.get("access_url", "")),
                      f"{fact_id} source_url 与来源血缘 access_url 不一致")
            if source.get("source_kind") == "unverified_secondary":
                out.check(fact.get("source_tier") not in {"公告", "交易所"},
                          f"{fact_id} 未核实二级来源不得冒充公告/交易所")

    # Collect post-T deltas before validating query back-references.
    post_table = search.get("post_t_safety_by_code") or {}
    out.check(isinstance(post_table, dict), "post_t_safety_by_code 必须是对象")
    out.check(isinstance(post_table, dict) and set(post_table) == codes,
              "post_t_safety_by_code 必须与候选代码一一对应")
    delta_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for code, post in (post_table.items() if isinstance(post_table, dict) else []):
        tag = f"post_t_safety_by_code.{code}"
        out.check(isinstance(post, dict), f"{tag} 必须是对象")
        if not isinstance(post, dict):
            continue
        out.check({"checked_through_beijing", "base_verdict_asof_t", "effective_verdict", "items"} <= set(post),
                  f"{tag} 缺必需字段")
        checked = str(post.get("checked_through_beijing", ""))
        checked_dt = _beijing_datetime(checked)
        out.check(checked_dt is not None and t_start is not None and retrieved_dt is not None and
                  t_start <= checked_dt <= retrieved_dt, f"{tag}.checked_through_beijing 无效")
        out.check(checked_dt is not None and retrieved_dt is not None and
                  checked_dt.date() == retrieved_dt.date(), f"{tag}.checked_through_beijing 必须覆盖到检索日")
        base = str(post.get("base_verdict_asof_t", ""))
        effective = str(post.get("effective_verdict", ""))
        out.check(base in BOTTOM_VERDICT_RANK and effective in BOTTOM_VERDICT_RANK,
                  f"{tag} 裁定枚举无效")
        if base in BOTTOM_VERDICT_RANK and effective in BOTTOM_VERDICT_RANK:
            out.check(BOTTOM_VERDICT_RANK[effective] <= BOTTOM_VERDICT_RANK[base],
                      f"{tag} T 后信息不得上调裁定")
        out.check(verdicts.get(str(code)) == effective, f"{tag}.effective_verdict 与最终裁定不一致")
        items = post.get("items") or []
        out.check(isinstance(items, list), f"{tag}.items 必须是数组")
        expected_rank = BOTTOM_VERDICT_RANK.get(base)
        for idx, item in enumerate(items if isinstance(items, list) else []):
            item_tag = f"{tag}.items[{idx}]"
            out.check(isinstance(item, dict), f"{item_tag} 必须是对象")
            if not isinstance(item, dict):
                continue
            out.check({"delta_id", "source_ref", "published_at", "event_date", "query_ids", "summary",
                       "polarity", "effect", "used_in_asof_t_verdict", "uncertainties"} <= set(item),
                      f"{item_tag} 缺必需字段")
            delta_id = str(item.get("delta_id", ""))
            out.check(bool(delta_id) and delta_id not in delta_by_id, f"{item_tag}.delta_id 缺失或重复")
            if delta_id and delta_id not in delta_by_id:
                delta_by_id[delta_id] = (str(code), item)
            out.check(str(item.get("source_ref", "")) in source_by_ref, f"{item_tag}.source_ref 无效")
            published = _date(item.get("published_at"))
            out.check(published is not None and t_date is not None and published > t_date,
                      f"{item_tag}.published_at 必须晚于 T")
            out.check(published is not None and retrieved_date is not None and published <= retrieved_date,
                      f"{item_tag}.published_at 晚于实际检索时点")
            out.check(published is not None and checked_dt is not None and published <= checked_dt.date(),
                      f"{item_tag}.published_at 晚于 checked_through_beijing")
            event_date = item.get("event_date")
            out.check(event_date in (None, "") or _date(event_date) is not None,
                      f"{item_tag}.event_date 无效")
            out.check(bool(str(item.get("summary", "")).strip()), f"{item_tag}.summary 为空")
            polarity = str(item.get("polarity", ""))
            effect = str(item.get("effect", ""))
            out.check(polarity in {"positive", "neutral", "negative", "unverified"},
                      f"{item_tag}.polarity 无效")
            out.check(effect in {"none", "cap_at_question", "cap_at_reject"}, f"{item_tag}.effect 无效")
            if polarity in {"positive", "neutral"}:
                out.check(effect == "none", f"{item_tag} T 后正面/中性信息不能改变裁定")
            if polarity == "unverified":
                out.check(effect != "cap_at_reject", f"{item_tag} 未核实增量最多降至 ?")
            delta_source = source_by_ref.get(str(item.get("source_ref", "")))
            if delta_source and delta_source.get("source_kind") == "unverified_secondary":
                out.check(polarity == "unverified" and effect in {"none", "cap_at_question"},
                          f"{item_tag} 未核实二级来源只能标 unverified 且最多降至 ?")
            if effect == "cap_at_question" and effective in BOTTOM_VERDICT_RANK:
                out.check(BOTTOM_VERDICT_RANK[effective] <= BOTTOM_VERDICT_RANK["?"],
                          f"{item_tag} cap_at_question 未落实")
                if expected_rank is not None:
                    expected_rank = min(expected_rank, BOTTOM_VERDICT_RANK["?"])
            if effect == "cap_at_reject":
                out.check(effective == "✗", f"{item_tag} cap_at_reject 未落实")
                if expected_rank is not None:
                    expected_rank = BOTTOM_VERDICT_RANK["✗"]
            out.check(item.get("used_in_asof_t_verdict") is False,
                      f"{item_tag}.used_in_asof_t_verdict 必须为 false")
            out.check(isinstance(item.get("query_ids"), list) and bool(item.get("query_ids")),
                      f"{item_tag}.query_ids 不能为空")
            out.check(isinstance(item.get("uncertainties"), list), f"{item_tag}.uncertainties 必须是数组")
        if expected_rank is not None and effective in BOTTOM_VERDICT_RANK:
            out.check(BOTTOM_VERDICT_RANK[effective] == expected_rank,
                      f"{tag}.effective_verdict 必须由 base 与 delta caps 机械重算")

    queries = search.get("queries") or []
    out.check(isinstance(queries, list), "bottom_search.queries 必须是数组")
    query_by_id: dict[str, dict[str, Any]] = {}
    for idx, query in enumerate(queries if isinstance(queries, list) else []):
        tag = f"bottom_search.queries[{idx}]"
        out.check(isinstance(query, dict), f"{tag} 必须是对象")
        if not isinstance(query, dict):
            continue
        out.check({"query_id", "code", "category", "phase", "query_mode", "query_text", "date_from",
                   "date_to", "executed_at_beijing", "outcome", "reviewed_urls", "selected_fact_ids",
                   "selected_delta_ids", "notes"} <= set(query), f"{tag} 缺必需字段")
        query_id = str(query.get("query_id", ""))
        out.check(bool(query_id) and query_id not in query_by_id, f"{tag}.query_id 缺失或重复")
        if query_id and query_id not in query_by_id:
            query_by_id[query_id] = query
        code = str(query.get("code", ""))
        category = str(query.get("category", ""))
        phase = str(query.get("phase", ""))
        mode = str(query.get("query_mode", ""))
        outcome = str(query.get("outcome", ""))
        fact_ids = _string_list(query.get("selected_fact_ids"))
        delta_ids = _string_list(query.get("selected_delta_ids"))
        reviewed_urls = _string_list(query.get("reviewed_urls"))
        out.check(code in codes or code == "MARKET", f"{tag}.code 不属于候选或 MARKET")
        out.check(category in BOTTOM_SEARCH_CATEGORIES or (code == "MARKET" and category == "market_regime"),
                  f"{tag}.category 无效")
        out.check(phase in {"as_of_t", "post_t_safety"}, f"{tag}.phase 无效")
        out.check(mode in BOTTOM_QUERY_MODES, f"{tag}.query_mode 无效")
        out.check(bool(str(query.get("query_text", "")).strip()), f"{tag}.query_text 为空")
        query_from = _date(query.get("date_from"))
        query_to = _date(query.get("date_to"))
        out.check(query_from is not None and query_to is not None and query_from <= query_to,
                  f"{tag} 查询日期窗无效")
        executed = str(query.get("executed_at_beijing", ""))
        executed_dt = _beijing_datetime(executed)
        out.check(executed_dt is not None and t_start is not None and retrieved_dt is not None and
                  t_start <= executed_dt <= retrieved_dt,
                  f"{tag}.executed_at_beijing 无效")
        out.check(query_to is not None and executed_dt is not None and query_to <= executed_dt.date(),
                  f"{tag}.date_to 晚于查询执行日")
        out.check(outcome in {"selected", "no_relevant_hit", "blocked"}, f"{tag}.outcome 无效")
        out.check(isinstance(query.get("reviewed_urls"), list), f"{tag}.reviewed_urls 必须是数组")
        for url in reviewed_urls:
            out.check(_valid_http_url(url), f"{tag}.reviewed_urls 含无效 URL")
        out.check(isinstance(query.get("selected_fact_ids"), list), f"{tag}.selected_fact_ids 必须是数组")
        out.check(isinstance(query.get("selected_delta_ids"), list), f"{tag}.selected_delta_ids 必须是数组")
        if outcome == "selected":
            out.check(bool(fact_ids or delta_ids), f"{tag} selected 却没有采用 fact/delta")
            out.check(bool(reviewed_urls), f"{tag} selected 必须记录 reviewed_urls")
        else:
            out.check(not fact_ids and not delta_ids, f"{tag} 未命中/受阻却选择了 fact/delta")
            out.check(bool(str(query.get("notes", "")).strip()), f"{tag} 未命中/受阻必须解释 notes")
        if phase == "as_of_t":
            out.check(str(query.get("date_to", "")) == t, f"{tag} as_of_t.date_to 必须等于 T")
            out.check(not delta_ids, f"{tag} as_of_t 不得选择 T 后 delta")
            for fact_id in fact_ids:
                fact = fact_by_id.get(fact_id)
                out.check(fact is not None, f"{tag} selected_fact_id 不存在: {fact_id}")
                if fact is not None:
                    out.check(str(fact.get("code", "")) == code and str(fact.get("category", "")) == category,
                              f"{tag} 选择了其他代码或类别的 fact: {fact_id}")
                    source = source_by_ref.get(str(fact.get("source_ref", "")))
                    out.check(source is not None and str(source.get("access_url", "")) in reviewed_urls,
                              f"{tag} 未在 reviewed_urls 记录 fact 来源: {fact_id}")
        elif phase == "post_t_safety":
            expected_from = t_date + dt.timedelta(days=1) if t_date else None
            out.check(query_from == expected_from, f"{tag} post_t_safety.date_from 必须严格为 T+1")
            out.check(retrieved_date is not None and query_to == retrieved_date,
                      f"{tag} post_t_safety.date_to 必须等于实际检索日")
            out.check(not fact_ids, f"{tag} post_t_safety 不得选择主事实")
            for delta_id in delta_ids:
                delta = delta_by_id.get(delta_id)
                out.check(delta is not None and delta[0] == code, f"{tag} selected_delta_id 不存在或代码不符: {delta_id}")
                if delta is not None:
                    source = source_by_ref.get(str(delta[1].get("source_ref", "")))
                    out.check(source is not None and str(source.get("access_url", "")) in reviewed_urls,
                              f"{tag} 未在 reviewed_urls 记录 delta 来源: {delta_id}")

    # Validate delta -> query backlinks now that the query index exists.
    for delta_id, (code, item) in delta_by_id.items():
        for query_id in _string_list(item.get("query_ids")):
            query = query_by_id.get(query_id)
            out.check(query is not None and query.get("phase") == "post_t_safety" and
                      str(query.get("code", "")) == code and delta_id in _string_list(query.get("selected_delta_ids")),
                      f"{delta_id} query_id 无法双向回指 T 后查询: {query_id}")

    coverage = search.get("coverage_by_code") or {}
    out.check(isinstance(coverage, dict), "coverage_by_code 必须是对象")
    out.check(isinstance(coverage, dict) and set(coverage) == codes,
              "coverage_by_code 必须与候选代码一一对应")
    all_coverage_fact_ids: dict[str, set[str]] = {}
    for code, block in (coverage.items() if isinstance(coverage, dict) else []):
        tag = f"coverage_by_code.{code}"
        out.check(isinstance(block, dict), f"{tag} 必须是对象")
        if not isinstance(block, dict):
            continue
        out.check({"aliases", "profile_tags", "categories", "official_latest_check", "ruling_evidence"} <=
                  set(block), f"{tag} 缺必需字段")
        out.check(isinstance(block.get("aliases"), list) and len(block.get("aliases") or []) >= 2,
                  f"{tag}.aliases 至少包含全称和简称")
        out.check(isinstance(block.get("profile_tags"), list), f"{tag}.profile_tags 必须是数组")
        categories = block.get("categories") or {}
        out.check(isinstance(categories, dict) and set(categories) == set(BOTTOM_SEARCH_CATEGORIES),
                  f"{tag}.categories 必须精确覆盖六维")
        if not isinstance(categories, dict):
            categories = {}
        covered_facts: set[str] = set()
        hit_count = 0
        blocked = False
        for category in BOTTOM_SEARCH_CATEGORIES:
            item = categories.get(category) or {}
            if not isinstance(item, dict):
                out.check(False, f"{tag}.categories.{category} 必须是对象")
                item = {}
            item_tag = f"{tag}.categories.{category}"
            out.check({"status", "query_ids", "fact_ids", "reason"} <= set(item),
                      f"{item_tag} 缺必需字段")
            status = str(item.get("status", ""))
            query_ids = _string_list(item.get("query_ids"))
            fact_ids = _string_list(item.get("fact_ids"))
            out.check(status in {"hit", "no_relevant_hit", "blocked"}, f"{item_tag}.status 无效")
            out.check(bool(query_ids), f"{item_tag}.query_ids 不能为空")
            out.check(bool(str(item.get("reason", "")).strip()), f"{item_tag}.reason 为空")
            matching_queries: list[dict[str, Any]] = []
            for query_id in query_ids:
                query = query_by_id.get(query_id)
                out.check(query is not None and query.get("phase") == "as_of_t" and
                          str(query.get("code", "")) == str(code) and query.get("category") == category,
                          f"{item_tag} query_id 不匹配: {query_id}")
                if query is not None:
                    matching_queries.append(query)
            query_starts = [_date(query.get("date_from")) for query in matching_queries]
            query_starts = [date for date in query_starts if date is not None]
            required_start = t_date - dt.timedelta(days=BOTTOM_CATEGORY_LOOKBACK_DAYS[category]) if t_date else None
            out.check(bool(query_starts) and required_start is not None and min(query_starts) <= required_start,
                      f"{item_tag} 查询窗口未覆盖至少 {BOTTOM_CATEGORY_LOOKBACK_DAYS[category]} 天")
            for fact_id in fact_ids:
                fact = fact_by_id.get(fact_id)
                out.check(fact is not None and str(fact.get("code", "")) == str(code) and
                          fact.get("category") == category, f"{item_tag} fact_id 不匹配: {fact_id}")
                out.check(any(fact_id in _string_list(query.get("selected_fact_ids"))
                              for query in matching_queries), f"{item_tag} fact 未被本维查询采用: {fact_id}")
            if status == "hit":
                hit_count += 1
                out.check(bool(fact_ids), f"{item_tag} hit 却没有 fact_ids")
                out.check(any(query.get("outcome") == "selected" for query in matching_queries),
                          f"{item_tag} hit 却没有 selected 查询")
            elif status == "no_relevant_hit":
                out.check(not fact_ids and any(query.get("outcome") == "no_relevant_hit"
                                               for query in matching_queries),
                          f"{item_tag} no_relevant_hit 与查询/事实不一致")
                variants = {str(query.get("query_text", "")).strip() for query in matching_queries
                            if query.get("outcome") == "no_relevant_hit"}
                out.check(len(variants) >= 2, f"{item_tag} no_relevant_hit 至少需要两种查询变体")
            elif status == "blocked":
                blocked = True
                out.check(not fact_ids and any(query.get("outcome") == "blocked" for query in matching_queries),
                          f"{item_tag} blocked 与查询/事实不一致")
            covered_facts.update(fact_ids)
        all_coverage_fact_ids[str(code)] = covered_facts

        official_queries = [q for q in query_by_id.values() if str(q.get("code", "")) == str(code) and
                            q.get("phase") == "as_of_t" and q.get("query_mode") == "official"]
        drop_queries = [q for q in query_by_id.values() if str(q.get("code", "")) == str(code) and
                        q.get("phase") == "as_of_t" and q.get("query_mode") == "drop_cause"]
        freshness_queries = [q for q in query_by_id.values() if str(q.get("code", "")) == str(code) and
                             q.get("phase") == "post_t_safety" and q.get("query_mode") == "freshness_delta"]
        out.check(bool(official_queries), f"{tag} 缺 official 查询")
        official_starts = [_date(query.get("date_from")) for query in official_queries]
        official_starts = [date for date in official_starts if date is not None]
        out.check(bool(official_starts) and t_date is not None and
                  min(official_starts) <= t_date - dt.timedelta(days=30),
                  f"{tag} official 查询未回扫至少30天")
        out.check(bool(drop_queries), f"{tag} 缺 drop_cause 查询")
        if retrieved_date and t_date and retrieved_date > t_date:
            out.check(any(query.get("outcome") in {"selected", "no_relevant_hit"}
                          for query in freshness_queries),
                      f"{tag} 缺可用的 T 后 freshness_delta 查询（不能全部 blocked）")

        latest = block.get("official_latest_check") or {}
        if not isinstance(latest, dict):
            out.check(False, f"{tag}.official_latest_check 必须是对象")
            latest = {}
        out.check({"query_ids", "checked_sources", "latest_pre_t"} <= set(latest),
                  f"{tag}.official_latest_check 缺必需字段")
        latest_ids = _string_list(latest.get("query_ids"))
        out.check(bool(latest_ids) and all(qid in query_by_id and
                                          query_by_id[qid].get("query_mode") == "official" and
                                          query_by_id[qid].get("phase") == "as_of_t" and
                                          str(query_by_id[qid].get("code", "")) == str(code)
                                          for qid in latest_ids), f"{tag}.official_latest_check.query_ids 无效")
        out.check(isinstance(latest.get("checked_sources"), list) and bool(latest.get("checked_sources")),
                  f"{tag}.official_latest_check.checked_sources 为空")
        latest_date = _date(latest.get("latest_pre_t"))
        latest_queries = [query_by_id[qid] for qid in latest_ids if qid in query_by_id]
        latest_all_blocked = bool(latest_queries) and all(query.get("outcome") == "blocked"
                                                          for query in latest_queries)
        if latest_all_blocked:
            out.check(latest.get("latest_pre_t") in (None, ""),
                      f"{tag}.official_latest_check 全部受阻时 latest_pre_t 应为 null")
        else:
            out.check(latest_date is not None and t_date is not None and latest_date <= t_date,
                      f"{tag}.official_latest_check.latest_pre_t 无效")

        ruling = block.get("ruling_evidence") or {}
        if not isinstance(ruling, dict):
            out.check(False, f"{tag}.ruling_evidence 必须是对象")
            ruling = {}
        out.check({"supporting_fact_ids", "adverse_fact_ids", "decision_fact_ids", "unresolved_query_ids"} <=
                  set(ruling), f"{tag}.ruling_evidence 缺必需字段")
        supporting = _string_list(ruling.get("supporting_fact_ids"))
        adverse = _string_list(ruling.get("adverse_fact_ids"))
        decision = _string_list(ruling.get("decision_fact_ids"))
        unresolved = _string_list(ruling.get("unresolved_query_ids"))
        out.check(set(decision) <= (set(supporting) | set(adverse)),
                  f"{tag}.ruling_evidence.decision 必须来自 supporting/adverse")
        for field_name, ids in (("supporting", supporting), ("adverse", adverse), ("decision", decision)):
            for fact_id in ids:
                fact = fact_by_id.get(fact_id)
                out.check(fact is not None and str(fact.get("code", "")) == str(code),
                          f"{tag}.ruling_evidence.{field_name} 引用了无效 fact: {fact_id}")
        for query_id in unresolved:
            query = query_by_id.get(query_id)
            out.check(query is not None and str(query.get("code", "")) == str(code) and
                      query.get("outcome") in {"blocked", "no_relevant_hit"},
                      f"{tag}.ruling_evidence.unresolved_query_ids 无效: {query_id}")
        post_for_code = post_table.get(str(code)) if isinstance(post_table, dict) else {}
        verdict = str((post_for_code or {}).get("base_verdict_asof_t", "")) if isinstance(post_for_code, dict) else ""
        supporting_decision_sources = [
            source_by_ref.get(str(fact_by_id[fid].get("source_ref", "")))
            for fid in set(decision) & set(supporting) if fid in fact_by_id
        ]
        adverse_decision_sources = [
            source_by_ref.get(str(fact_by_id[fid].get("source_ref", "")))
            for fid in set(decision) & set(adverse) if fid in fact_by_id
        ]
        if verdict == "✓":
            out.check(not blocked, f"{tag} 有 blocked 维度，不得给 ✓")
            out.check(categories.get("performance_operations", {}).get("status") == "hit",
                      f"{tag} ✓ 必须有 performance_operations 命中")
            performance_sources = [source_by_ref.get(str(fact_by_id[fid].get("source_ref", "")))
                                   for fid in _string_list(categories.get("performance_operations", {}).get("fact_ids"))
                                   if fid in fact_by_id]
            out.check(any(source and source.get("source_kind") in
                          {"official_direct", "verified_official_mirror"} for source in performance_sources),
                      f"{tag} ✓ 的最新财务锚必须是官方直源或已验证镜像")
            out.check(hit_count >= 3, f"{tag} ✓ 至少命中三个维度")
            out.check(bool(supporting) and bool(set(decision) & set(supporting)) and
                      any(source and source.get("source_kind") in
                          {"official_direct", "verified_official_mirror"}
                          for source in supporting_decision_sources),
                      f"{tag} ✓ 缺支持裁定的官方决定性事实")
            out.check(all(query.get("outcome") != "blocked" for query in official_queries),
                      f"{tag} ✓ 的官方公告回扫不得受阻")
            out.check(all(query.get("outcome") != "blocked" for query in drop_queries),
                      f"{tag} ✓ 的跌因搜索不得受阻")
            origins = {str(source_by_ref.get(str(fact_by_id[fid].get("source_ref", "")), {}).get("origin_id", ""))
                       for fid in covered_facts if fid in fact_by_id}
            origins.discard("")
            out.check(len(origins) >= 3, f"{tag} ✓ 采用事实不足三个独立 origin_id")
        elif verdict == "✗":
            out.check(bool(adverse) and bool(set(decision) & set(adverse)),
                      f"{tag} ✗ 缺 adverse 决定性事实")
            out.check(any(source and source.get("source_kind") in
                          {"official_direct", "verified_official_mirror"}
                          for source in adverse_decision_sources),
                      f"{tag} ✗ 决定性恶化必须有官方原始证据")
            out.check(all(source and source.get("source_kind") != "unverified_secondary"
                          for source in adverse_decision_sources), f"{tag} ✗ 不得由未核实二级来源决定")
        elif verdict == "?":
            out.check(bool(unresolved) or blocked or (bool(supporting) and bool(adverse)),
                      f"{tag} ? 必须有未决查询、受阻来源或正反冲突")

    market = search.get("market_coverage") or {}
    out.check(isinstance(market, dict), "market_coverage 必须是对象")
    if isinstance(market, dict):
        out.check({"status", "query_ids", "fact_ids", "reason"} <= set(market),
                  "market_coverage 缺必需字段")
        status = str(market.get("status", ""))
        query_ids = _string_list(market.get("query_ids"))
        fact_ids = _string_list(market.get("fact_ids"))
        out.check(status in {"hit", "no_relevant_hit", "blocked"}, "market_coverage.status 无效")
        out.check(bool(str(market.get("reason", "")).strip()), "market_coverage.reason 为空")
        if not candidates:
            out.check(status == "no_relevant_hit" and not query_ids and not fact_ids,
                      "零候选 market_coverage 必须是 no_relevant_hit 且引用为空")
        else:
            out.check(bool(query_ids), "market_coverage.query_ids 不能为空")
        market_queries = []
        for query_id in query_ids:
            query = query_by_id.get(query_id)
            out.check(query is not None and query.get("code") == "MARKET" and
                      query.get("category") == "market_regime" and query.get("phase") == "as_of_t",
                      f"market_coverage query_id 无效: {query_id}")
            if query is not None:
                market_queries.append(query)
        for fact_id in fact_ids:
            fact = fact_by_id.get(fact_id)
            out.check(fact is not None and fact.get("code") == "MARKET" and fact.get("category") == "market_regime",
                      f"market_coverage fact_id 无效: {fact_id}")
            out.check(any(fact_id in _string_list(query.get("selected_fact_ids")) for query in market_queries),
                      f"market_coverage fact 未被查询采用: {fact_id}")
        if not candidates:
            pass
        elif status == "hit":
            out.check(bool(fact_ids) and any(q.get("outcome") == "selected" for q in market_queries),
                      "market_coverage hit 与事实/查询不一致")
        else:
            out.check(not fact_ids and any(q.get("outcome") == status for q in market_queries),
                      f"market_coverage {status} 与事实/查询不一致")

        market_fact_set = set(fact_ids)
        for fact_id, fact in fact_by_id.items():
            code = str(fact.get("code", ""))
            if code == "MARKET":
                out.check(fact_id in market_fact_set, f"{fact_id} 是孤儿 MARKET fact，未进入 market_coverage")
            elif code in codes:
                out.check(fact_id in all_coverage_fact_ids.get(code, set()),
                          f"{fact_id} 是孤儿候选 fact，未进入对应 category coverage")

    # Exact, index-level F10 reconciliation: no date-only or alias matching.
    expected_seeds = _bottom_expected_seeds(result_obj)
    ledger = search.get("f10_seed_ledger") or []
    out.check(isinstance(ledger, list), "f10_seed_ledger 必须是数组")
    mapped_ledger: dict[str, dict[str, Any]] = {}
    raw_ledger_keys: set[str] = set()
    notice_fact_owner: dict[str, str] = {}
    notice_delta_owner: dict[str, str] = {}
    for idx, item in enumerate(ledger if isinstance(ledger, list) else []):
        tag = f"f10_seed_ledger[{idx}]"
        out.check(isinstance(item, dict), f"{tag} 必须是对象")
        if not isinstance(item, dict):
            continue
        out.check({"seed_key", "code", "kind", "raw_index", "seed_text", "raw_date", "timing",
                   "disposition", "query_ids", "fact_ids", "delta_ids", "reason"} <= set(item),
                  f"{tag} 缺必需字段")
        seed_key = str(item.get("seed_key", ""))
        out.check(bool(seed_key) and seed_key not in raw_ledger_keys, f"{tag}.seed_key 缺失或重复")
        raw_ledger_keys.add(seed_key)
        out.check(seed_key in expected_seeds and seed_key not in mapped_ledger,
                  f"{tag}.seed_key 不是原始种子或重复")
        if seed_key not in expected_seeds or seed_key in mapped_ledger:
            continue
        canonical = seed_key
        mapped_ledger[canonical] = item
        expected = expected_seeds[canonical]
        out.check(str(item.get("code", "")) == expected["code"], f"{tag}.code 与原始种子不符")
        out.check(item.get("kind") == expected["kind"], f"{tag}.kind 与原始种子不符")
        out.check(item.get("raw_index") == expected["raw_index"], f"{tag}.raw_index 与原始种子不符")
        out.check(str(item.get("seed_text", "")) == expected["seed_text"], f"{tag}.seed_text 未逐字保留原始种子")
        out.check((str(item.get("raw_date")) if item.get("raw_date") is not None else None) == expected["raw_date"],
                  f"{tag}.raw_date 与原始种子不符")
        seed_date = _date(expected["raw_date"])
        timing = "undated" if seed_date is None else ("post_t" if t_date and seed_date > t_date else "pre_t")
        out.check(item.get("timing") == timing, f"{tag}.timing 与 T 不符")
        disposition = str(item.get("disposition", ""))
        query_ids = _string_list(item.get("query_ids"))
        fact_ids = _string_list(item.get("fact_ids"))
        delta_ids = _string_list(item.get("delta_ids"))
        out.check(bool(str(item.get("reason", "")).strip()), f"{tag}.reason 为空")
        out.check(bool(query_ids), f"{tag}.query_ids 不能为空")
        if timing == "post_t":
            out.check(disposition == "quarantined_post_t" and not fact_ids and bool(query_ids) and bool(delta_ids),
                      f"{tag} T 后种子必须隔离并关联 query/delta")
        elif timing == "undated":
            out.check(disposition == "quarantined_undated" and not fact_ids and not delta_ids and bool(query_ids),
                      f"{tag} 无日期种子必须隔离")
        elif expected["material"]:
            out.check(disposition == "adjudicated_pre_t" and bool(fact_ids) and not delta_ids,
                      f"{tag} T 前实质种子必须逐条裁定")
        else:
            out.check(disposition in {"adjudicated_pre_t", "logged_routine_pre_t"},
                      f"{tag} T 前种子 disposition 无效")
            if disposition == "logged_routine_pre_t":
                out.check(not fact_ids and not delta_ids,
                          f"{tag} logged_routine_pre_t 不得进入 fact/delta")
        if disposition == "adjudicated_pre_t":
            out.check(bool(fact_ids), f"{tag} adjudicated_pre_t 必须关联 fact_ids")
        for query_id in query_ids:
            query = query_by_id.get(query_id)
            out.check(query is not None, f"{tag} query_id 不存在: {query_id}")
            if query is not None and timing == "post_t":
                out.check(query.get("phase") == "post_t_safety", f"{tag} T 后种子关联了 as_of_t 查询")
            elif query is not None and timing != "post_t":
                out.check(query.get("phase") == "as_of_t", f"{tag} T 前/无日期种子查询 phase 无效")
        for fact_id in fact_ids:
            fact = fact_by_id.get(fact_id)
            out.check(fact is not None and str(fact.get("code", "")) == expected["code"],
                      f"{tag} fact_id 不存在或代码不符: {fact_id}")
            if fact is not None:
                out.check(fact.get("f10_match") in {"confirmed", "conflict"},
                          f"{tag} 对账 fact 必须 confirmed/conflict")
                out.check(seed_key in _string_list(fact.get("seed_refs")) or canonical in _string_list(fact.get("seed_refs")),
                          f"{tag} fact.seed_refs 未双向回指 seed")
                out.check(any(fact_id in _string_list((query_by_id.get(query_id) or {}).get("selected_fact_ids"))
                              for query_id in query_ids), f"{tag} fact 未被 ledger 查询采用: {fact_id}")
        if expected["kind"] == "notice" and disposition == "adjudicated_pre_t":
            unique_fact_ids = [fact_id for fact_id in fact_ids if fact_id not in notice_fact_owner]
            out.check(bool(unique_fact_ids), f"{tag} 每条 notice 必须有未被其他 notice 复用的逐条 fact")
            for fact_id in fact_ids:
                notice_fact_owner.setdefault(fact_id, seed_key)
        for delta_id in delta_ids:
            delta = delta_by_id.get(delta_id)
            out.check(delta is not None and delta[0] == expected["code"],
                      f"{tag} delta_id 不存在或代码不符: {delta_id}")
            out.check(any(delta_id in _string_list((query_by_id.get(query_id) or {}).get("selected_delta_ids"))
                          for query_id in query_ids), f"{tag} delta 未被 ledger 查询采用: {delta_id}")
        if expected["kind"] == "notice" and timing == "post_t":
            unique_delta_ids = [delta_id for delta_id in delta_ids if delta_id not in notice_delta_owner]
            out.check(bool(unique_delta_ids), f"{tag} 每条 T 后 notice 必须有未被其他 notice 复用的逐条 delta")
            for delta_id in delta_ids:
                notice_delta_owner.setdefault(delta_id, seed_key)
    out.check(set(mapped_ledger) == set(expected_seeds), "f10_seed_ledger 与 forecast/notices 未一一相等")
    for fact_id, fact in fact_by_id.items():
        for seed_ref in _string_list(fact.get("seed_refs")):
            out.check(seed_ref in raw_ledger_keys or seed_ref in expected_seeds,
                      f"{fact_id}.seed_refs 引用了不存在的种子: {seed_ref}")

    # Concentration is a transparency warning, not an automatic rejection.
    for code in codes:
        used_sources = [source_by_ref.get(str(fact_by_id[fid].get("source_ref", "")))
                        for fid in all_coverage_fact_ids.get(code, set()) if fid in fact_by_id]
        publishers = {str(source.get("access_publisher", "")) for source in used_sources if source}
        out.warn(len(publishers) >= 2, f"{code} 采用事实来源发布者过度集中")
    return out


def price_audit_check(obj: dict[str, Any], codes: Iterable[str], as_of: str) -> Result:
    out = Result("price-verification")
    audit = obj.get("codex_audit") or {}
    table = audit.get("price_verification_by_code") or {}
    for code in codes:
        row = table.get(str(code)) or {}
        out.check(row.get("as_of") == as_of, f"{code} 验价 T 与结果 T 不一致")
        usable = row.get("usable_sources") or {}
        values = [_num(x) for x in usable.values()]
        values = [x for x in values if x is not None]
        out.check(len(values) >= 2, f"{code} 跨源验价不足两个同 T 独立源")
        if len(values) >= 2:
            dev = (max(values) - min(values)) / max(values) * 100
            out.check(_close(row.get("max_dev_pct"), round(dev, 3), 0.011), f"{code} 验价偏差重算失败")
            out.check(dev <= 1.0 and row.get("status") == "verified", f"{code} 价格未通过≤1%跨源一致性")
    return out


BOTTOM_WEIGHTS = {
    "defensive": 8.6,
    "above_ma10": 5.2,
    "dif_up": 4.5,
    "rsv_recover": 3.9,
    "dd_sweet": 3.7,
    "above_ma5": 3.7,
    "gap_reclaim": 4.4,
    "rsv_deep": -7.4,
    "downstk4": -6.3,
    "zt20": -5.4,
    "atr_hi": -3.5,
    "fresh_low": -3.1,
}


def validate_bottom(obj: dict[str, Any], strict: bool = True, require_search: bool = False) -> Result:
    out = Result("bottom-fishing")
    candidates = list(obj.get("candidates") or [])
    observe = list(obj.get("observe") or [])
    all_rows = candidates + observe
    t = str(obj.get("T", ""))
    out.check(bool(DATE_RE.match(t)), "T 缺失或格式错误")
    market = obj.get("market") or {}
    if market.get("T"):
        out.check(str(market.get("T")) == t, "market.T 与顶层 T 不一致")
    threshold = obj.get("threshold") or {}
    out.check(_close(threshold.get("total"), 18.0, 0.001), "总分阈值不是 18")
    out.check(_close(threshold.get("stock"), 15.0, 0.001), "个股分阈值不是 15")
    out.check(_close(threshold.get("atr"), 4.0, 0.001), "ATR gate 不是 4")

    for row in all_rows:
        code = str(row.get("code", "?"))
        hits = row.get("hits") or {}
        calc_total = sum(weight for name, weight in BOTTOM_WEIGHTS.items() if hits.get(name) is True)
        calc_stock = calc_total - (BOTTOM_WEIGHTS["defensive"] if hits.get("defensive") else 0)
        out.check(_close(row.get("score"), round(calc_total, 1), 0.051), f"{code} score 重算失败")
        out.check(_close(row.get("stock_score"), round(calc_stock, 1), 0.051), f"{code} stock_score 重算失败")
        defensive = bool(hits.get("defensive"))
        score_pass = calc_total >= 18 if defensive else calc_stock >= 15
        atr = _num(row.get("atr"))
        expected = score_pass and atr is not None and atr <= 4 and not bool(row.get("cooldown"))
        out.check(bool(row.get("qualified")) == expected, f"{code} qualified 与双路径/ATR/冷却 gate 不符")
        out.check(str(row.get("T", t)) == t, f"{code} 的 T 与顶层不一致")
    out.check(all(bool(x.get("qualified")) for x in candidates), "candidates 中出现未过线票")
    out.check(not any(bool(x.get("qualified")) for x in observe), "observe 中出现过线票")

    if obj.get("adjudicated"):
        allowed = {"✓", "?", "✗"}
        for row in candidates:
            out.check(row.get("judge") in allowed, f"{row.get('code')} 裁定不在 ✓/?/✗")
            if row.get("judge") != "✓":
                out.check(not row.get("plan") and all(row.get(k) in (None, "", "—")
                                                       for k in ("buy_low", "buy_high", "stop", "target")),
                          f"{row.get('code')} 非买入票仍保留操作价位")
        layer = {"✓": 0, "?": 1, "✗": 2}
        actual = [(layer.get(x.get("judge"), 9), -float(x.get("score", 0))) for x in candidates]
        out.check(actual == sorted(actual), "裁定版不是 ✓→?→✗ 且层内分数降序")
    if strict:
        search_enabled = isinstance((obj.get("codex_audit") or {}).get("bottom_search"), dict)
        out.merge(audit_common(obj, "bottom-fishing", [] if search_enabled else _code_list(candidates)))
        t_date = _date(t)
        for idx, fact in enumerate((obj.get("codex_audit") or {}).get("facts") or []):
            published = _date(fact.get("published_at"))
            out.check(t_date is not None and published is not None and published <= t_date,
                      f"bottom facts[{idx}] 使用了 T 日之后信息（前视违规）")
        if not search_enabled:
            out.merge(f10_cross_check(obj, "bottom-fishing"))
        buy_codes = [str(x.get("code")) for x in candidates if x.get("judge") == "✓"]
        if buy_codes:
            out.merge(price_audit_check(obj, buy_codes, t))
        if require_search or search_enabled:
            out.merge(validate_bottom_search(obj, required=require_search))
    return out


def _agent_scores(obj: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in obj.get("agent_scores") or []:
        dim = str(row.get("dim", ""))
        m = re.match(r"^([①②③④⑤])", dim)
        score = _num(row.get("score"))
        if m and score is not None:
            result[m.group(1)] = score
    return result


def _scorecard_check(audit: dict[str, Any], scores: dict[str, float]) -> Result:
    out = Result("stock.scorecards")
    cards = audit.get("scorecards") or {}
    for dim in ("②", "③", "④"):
        card = cards.get(dim) or {}
        out.check(_close(card.get("start"), 50, 0.001), f"Agent{dim} 必须从 50 分起")
        items = card.get("items")
        out.check(isinstance(items, list) and bool(items), f"Agent{dim} 缺少逐项加减分")
        total = 50.0
        for idx, item in enumerate(items or []):
            delta = _num(item.get("delta"))
            out.check(delta is not None, f"Agent{dim}.items[{idx}] delta 无效")
            total += delta or 0
            out.check(bool(str(item.get("reason", "")).strip()), f"Agent{dim}.items[{idx}] reason 为空")
            out.check(_valid_http_url(item.get("source_url")),
                      f"Agent{dim}.items[{idx}] 缺来源 URL")
            out.check(bool(DATE_RE.match(str(item.get("published_at", "")))),
                      f"Agent{dim}.items[{idx}] 缺来源日期")
        out.check(_close(total, card.get("final"), 0.011), f"Agent{dim} 加减算术错误")
        out.check(_close(card.get("final"), scores.get(dim), 0.011), f"Agent{dim} scorecard 与 agent_scores 不符")
    return out


CONF_ORDER = {"低": 0, "回避": 0, "中低": 1, "中": 2, "中高": 3, "高": 4}


def _normalize_conf(value: Any) -> str:
    text = str(value or "")
    for key in ("中高", "中低", "回避", "高", "中", "低"):
        if key in text:
            return key
    return ""


def _stock_conf_cap(final: float, bullish: list[float], hard_gate: bool) -> str:
    if hard_gate:
        return "低"
    spread = max(bullish) - min(bullish)
    same = sum(x >= 60 for x in bullish)
    if final >= 75 and all(x >= 60 for x in bullish):
        return "高"
    if final >= 60 and same >= 3:
        return "中高"
    if 45 <= final < 60 or spread > 30:
        return "中"
    return "低"


def _stock_objective_hard_gates(obj: dict[str, Any]) -> list[str]:
    gates: list[str] = []
    as_of = _date(obj.get("as_of"))
    f10 = obj.get("f10") or {}
    fc = f10.get("forecast") or {}
    fc_date = _date(fc.get("notice_date"))
    fc_type = str(fc.get("type", ""))
    if as_of and fc_date and 0 <= (as_of - fc_date).days <= 370 and \
       any(x in fc_type for x in ("预亏", "首亏", "续亏")):
        gates.append("业绩预亏")
    for lift in f10.get("lift") or []:
        days = int(lift.get("days_to", 9999) or 9999)
        ratio = _num(lift.get("ratio_pct"), 0)
        if days <= 30 and ratio is not None and ratio >= 5:
            gates.append("30天内大额解禁")
            break
    return gates


def _stock_formula_audit(obj: dict[str, Any], scores: dict[str, float]) -> Result:
    out = Result("stock.formulas")
    audit = obj.get("codex_audit") or {}
    tech = audit.get("technical_score") or {}
    stance = _num((obj.get("engine_verdict") or {}).get("stance"))
    out.check(_close(tech.get("engine_stance"), stance, 0.011), "① engine_stance 与引擎不符")
    out.check(_close(tech.get("multiplier"), 0.85, 0.001), "① multiplier 不是 0.85")
    adj = _num(tech.get("subjective_adjustment"))
    out.check(adj is not None and -10 <= adj <= 10, "① 主观校正必须在 -10~+10")
    if stance is not None and adj is not None:
        calc = round(stance * 0.85 + adj, 1)
        out.check(_close(tech.get("final"), calc, 0.11), "① 技术分算术错误")
        out.check(_close(scores.get("①"), calc, 0.11), "① technical_score 与 agent_scores 不符")
    out.check(bool(str(tech.get("reason", "")).strip()), "① 主观校正缺理由")

    risk = audit.get("risk_breakdown") or {}
    engine_tech = _num(obj.get("risk_score"), 0)
    engine_event = _num((obj.get("f10") or {}).get("risk_bump"), 0)
    out.check(_close(risk.get("engine_technical_risk"), engine_tech, 0.011), "⑤ 引擎技术风险不符")
    out.check(_close(risk.get("engine_event_risk"), engine_event, 0.011), "⑤ F10事件风险不符")
    subjective = risk.get("subjective_items") or []
    out.check(isinstance(subjective, list), "⑤ subjective_items 必须是数组")
    subtotal = 0.0
    for idx, item in enumerate(subjective if isinstance(subjective, list) else []):
        delta = _num(item.get("delta"))
        out.check(delta is not None and delta >= 0, f"⑤ subjective_items[{idx}] delta 必须非负")
        subtotal += delta or 0
        out.check(bool(str(item.get("reason", "")).strip()), f"⑤ subjective_items[{idx}] 缺理由")
        out.check(_valid_http_url(item.get("source_url")), f"⑤ subjective_items[{idx}] 缺 URL")
        out.check(bool(DATE_RE.match(str(item.get("published_at", "")))), f"⑤ subjective_items[{idx}] 缺发布日期")
    expected = min(100.0, float(engine_tech or 0) + float(engine_event or 0) + subtotal)
    out.check(_close(risk.get("final"), expected, 0.11), "⑤ 风险分算术错误")
    out.check(_close(scores.get("⑤"), expected, 0.11), "⑤ risk_breakdown 与 agent_scores 不符")
    return out


def validate_stock(obj: dict[str, Any], strict: bool = True) -> Result:
    out = Result("stock-diagnostic")
    out.check(obj.get("schema") == "stock-diagnostic/v1", "stock JSON schema 不是 stock-diagnostic/v1")
    code = str(obj.get("code", ""))
    out.check(bool(re.fullmatch(r"\d{6}", code)), "股票代码无效")
    out.check(bool(DATE_RE.match(str(obj.get("as_of", "")))), "as_of 缺失或无效")
    scores = _agent_scores(obj)
    out.check(set(scores) == {"①", "②", "③", "④", "⑤"}, "完整版必须有 ①~⑤ 五个结构化分数")
    for dim, score in scores.items():
        out.check(0 <= score <= 100, f"Agent{dim} 分数越界")
    verification = obj.get("verification") or {}
    out.check(isinstance(verification, dict), "缺少 Agent⑥ verification")
    out.check(not verification.get("failed"), "Agent⑥ verification.failed 非空")
    out.check(len(verification.get("passed") or []) >= 8, "Agent⑥ 机械检查不足 8 项")

    if len(scores) == 5:
        base = scores["①"] * 0.28 + scores["②"] * 0.22 + scores["③"] * 0.22 + scores["④"] * 0.28
        pre = base - scores["⑤"] * 0.15
        audit = obj.get("codex_audit") or {}
        if strict:
            adjustment = _num(audit.get("market_adjustment"))
            out.check(adjustment is not None and -5 <= adjustment <= 5,
                      "codex_audit.market_adjustment 必须在 -5~+5")
        else:
            adjustment = (_num(obj.get("final_score"), pre) or pre) - pre
            out.check(-5 <= adjustment <= 5,
                      f"旧样本隐含市场调整超出 -5~+5: {adjustment:.1f}")
        if adjustment is not None:
            calc = round(pre + adjustment, 1)
            if strict and (audit.get("hard_gates") or []):
                calc = min(40.0, calc)
            final = _num(obj.get("final_score"))
            out.check(final is not None and _close(calc, final, 0.11),
                      f"最终分重算失败: expected {calc}, actual {final}")

    pm = obj.get("price_map") or {}
    stop = _num(pm.get("stop"))
    support = _num(pm.get("key_support"))
    add = pm.get("add_zone") or []
    take = pm.get("take_profit") or []
    if stop is not None and len(add) >= 2:
        out.check(stop < float(add[0]) <= float(add[1]), "止损/加仓区关系错误")
    if support is not None and stop is not None:
        out.check(stop < support, "止损没有低于结构支撑")
    if len(take) >= 2 and len(add) >= 2:
        out.check(float(add[1]) <= float(take[1]), "加仓区高于最终目标")
    rr = _num(pm.get("rr"))
    action_text = f"{obj.get('final_action','')} {json.dumps(obj.get('operation_plan') or [], ensure_ascii=False)}"
    if rr is not None and rr < 1.5:
        out.check("现价加" not in action_text and "现价补" not in action_text,
                  "R:R<1.5 仍出现现价加仓/补仓")
    close = _num((obj.get("technical") or {}).get("close"))
    atr14 = _num((obj.get("technical") or {}).get("atr14"))
    if None not in (close, stop, atr14):
        out.check(float(close) - float(stop) >= 1.2 * float(atr14) - 0.02,
                  "止损距离不足 1.2×日ATR")
    ca = obj.get("cost_analysis") or {}
    flow5 = _num((obj.get("fund_flow") or {}).get("main_net_5d_yi"), 0)
    verdict = obj.get("engine_verdict") or {}
    if _num(ca.get("pnl_pct"), 0) is not None and (_num(ca.get("pnl_pct"), 0) or 0) <= -15 and \
       verdict.get("downtrend") and (flow5 or 0) < 0 and not verdict.get("avg_down_ok"):
        positive_add = any(any(word in str(row.get("action", "")) for word in ("补仓", "加仓"))
                           for row in obj.get("operation_plan") or [])
        final_action = str(obj.get("final_action", ""))
        if any(word in final_action for word in ("补仓", "加仓")) and \
           not any(word in final_action for word in ("不补", "禁止补", "严禁补", "不加", "禁止加", "严禁加")):
            positive_add = True
        out.check(not positive_add, "深套+下跌趋势+资金流出仍给出补仓/加仓")

    if strict:
        common = audit_common(obj, "stock-diagnostic", [code])
        out.merge(common)
        audit = obj.get("codex_audit") or {}
        out.merge(f10_cross_check(obj, "stock-diagnostic"))
        out.merge(price_audit_check(obj, [code], str(obj.get("as_of", ""))))
        out.merge(_stock_formula_audit(obj, scores))
        out.merge(_scorecard_check(audit, scores))
        hard_gates = audit.get("hard_gates") or []
        out.check(isinstance(hard_gates, list), "hard_gates 必须是数组")
        objective_gates = _stock_objective_hard_gates(obj)
        hard_text = "|".join(str(x) for x in hard_gates)
        for gate in objective_gates:
            out.check(gate in hard_text, f"客观硬 gate 漏判: {gate}")
        if objective_gates:
            out.check(scores.get("②", 101) <= 30 or "业绩预亏" not in objective_gates,
                      "预亏硬下限下 ②分仍高于30")
        final_num = _num(obj.get("final_score"))
        final = final_num if final_num is not None else -999
        if hard_gates:
            out.check(final <= 40, "命中硬 gate 后 final_score 未封顶 40")
            out.check(any(x in str(obj.get("final_action", "")) for x in ("减", "清", "止损")),
                      "命中硬 gate 后动作未至少降到减仓")
        warnings = "|".join(str(x) for x in obj.get("data_warnings") or [])
        out.check("K线最新仅到" not in warnings, "K线滞后仍尝试发布最终报告")
        out.check("跨源价格不一致" not in warnings, "引擎已报跨源价差却仍尝试发布最终报告")
        declared = _normalize_conf(audit.get("confidence_level") or obj.get("final_confidence"))
        cap = _stock_conf_cap(final, [scores[x] for x in ("①", "②", "③", "④")], bool(hard_gates))
        out.check(declared in CONF_ORDER, "未声明可识别的置信度")
        if declared in CONF_ORDER:
            out.check(CONF_ORDER[declared] <= CONF_ORDER[cap], f"置信度 {declared} 超过机械上限 {cap}")
    return out


def validate_stock_engine(obj: dict[str, Any]) -> Result:
    """只校验 Agent① 原始引擎产物；最终裁定仍必须走 validate_stock(strict=True)。"""
    out = Result("stock-engine")
    out.check(obj.get("schema") == "stock-diagnostic/v1", "stock 引擎 schema 错误")
    out.check(bool(re.fullmatch(r"\d{6}", str(obj.get("code", "")))), "stock 引擎代码无效")
    out.check(_date(obj.get("as_of")) is not None, "stock 引擎 as_of 无效")
    out.check("+08:00" in str(obj.get("generated_at", "")), "stock 引擎 generated_at 非北京时间")
    breakdown = obj.get("factor_breakdown") or {}
    required = {"动量", "量能", "技术", "盘口", "回调", "惩罚"}
    out.check(set(breakdown) == required, "stock 因子分解字段变化")
    if set(breakdown) == required:
        calc = sum((_num(breakdown.get(x), 0) or 0) for x in required - {"惩罚"}) - \
               (_num(breakdown.get("惩罚"), 0) or 0)
        out.check(_close(obj.get("quant_score"), calc, 0.21), "stock quant_score 与因子分解不符")
    risk = _num(obj.get("risk_score"), 0) or 0
    event = _num((obj.get("f10") or {}).get("risk_bump"), 0) or 0
    out.check(_close((obj.get("engine_verdict") or {}).get("total_risk"), min(100, risk + event), 0.001),
              "stock 总风险不等于技术风险+F10事件风险")
    close = _num((obj.get("technical") or {}).get("close"))
    quote = _num((obj.get("quote") or {}).get("price"))
    out.check(_close(close, quote, 0.011), "stock quote.price 与技术收盘不一致")
    pm = obj.get("price_map") or {}
    stop = _num(pm.get("stop"))
    support = _num(pm.get("key_support"))
    add = pm.get("add_zone") or []
    target = pm.get("take_profit") or []
    out.check(None not in (stop, support, close) and stop < support < close, "stock 止损/支撑/现价关系错误")
    if len(add) >= 2 and len(target) >= 2 and stop is not None:
        out.check(stop < float(add[0]) <= float(add[1]) < float(target[1]), "stock 加仓/目标价位链错误")
    atr = _num((obj.get("technical") or {}).get("atr14"))
    if None not in (close, stop, atr):
        out.check(float(close) - float(stop) >= 1.2 * float(atr) - 0.02, "stock 引擎止损不足1.2×ATR")
    warnings = "|".join(str(x) for x in obj.get("data_warnings") or [])
    out.check("K线最新仅到" not in warnings, "stock 联网烟测得到滞后K线")
    out.check("跨源价格不一致" not in warnings, "stock 联网烟测引擎已报跨源价差")
    out.check((obj.get("f10") or {}).get("available") is True, "stock 联网烟测 F10 不可用")
    out.check(bool((obj.get("fund_flow") or {}).get("source")), "stock 联网烟测资金流无来源")
    plans = obj.get("operation_plan") or []
    out.check(any("T+" in str(x.get("trigger", "")) for x in plans), "stock 操作矩阵缺 T+N 到期行")
    return out


def _weekly_components(c: dict[str, Any], weights: dict[str, Any]) -> tuple[float, float, float, float, float, float, float]:
    ret5 = _num(c.get("ret5"), 0) or 0
    ret20 = _num(c.get("ret20"), 0) or 0
    vr = _num(c.get("vr"), 1) or 1
    vol_today = _num(c.get("vol_today"), 1) or 1
    tail = _num(c.get("tail_strength"), 0.5) or 0.5
    dist = _num(c.get("dist_ma10"), 0) or 0
    mom = min(max(ret5, -5), 12) / 12 * 18 + min(max(ret20, -15), 25) / 25 * 17
    vol = min(max(vr - 1, 0), 1.2) / 1.2 * 15 + min(max(vol_today - 1, 0), 2) / 2 * 10
    tech = (12 if c.get("bull") else 0) + (7 if c.get("macd_gold") else 0) + (6 if -2 <= dist <= 6 else 0)
    tape = tail * 8 + (4 if c.get("is_yang") else 0) + (3 if (_num(c.get("body_ratio"), 0) or 0) > 0.4 else 0)
    pull = 10 if c.get("pullback") else 0
    chg1 = _num(c.get("chg1"), 0) or 0
    pen = (10 if dist > 15 else 5 if dist > 10 else 0)
    pen += 8 if (_num(c.get("rng_pos"), 0) or 0) > 95 else 0
    pen += 10 if int(c.get("limit_streak", 0) or 0) >= 3 else 0
    pen += 20 if chg1 >= 9.5 else 12 if chg1 >= 7 else 0
    mom *= _num(weights.get("mom"), 1) or 1
    vol *= _num(weights.get("vol"), 1) or 1
    tech *= _num(weights.get("tech"), 1) or 1
    tape *= _num(weights.get("tape"), 1) or 1
    pull *= _num(weights.get("pull"), 1) or 1
    score = max(0.0, min(100.0, mom + vol + tech + tape + pull - pen))
    return mom, vol, tech, tape, pull, pen, score


def _weekly_risk(c: dict[str, Any]) -> int:
    rs = 0
    rp = _num(c.get("rng_pos"), 50) or 50
    rs += 25 if rp > 95 else 15 if rp > 85 else 5 if rp > 70 else 0
    dist = _num(c.get("dist_ma10"), 0) or 0
    rs += 20 if dist > 15 else 12 if dist > 10 else 5 if dist > 5 else 0
    atr = _num(c.get("atr_pct"), 0) or 0
    rs += 15 if atr > 8 else 10 if atr > 6 else 5 if atr > 4 else 0
    streak = int(c.get("limit_streak", 0) or 0)
    rs += 25 if streak >= 3 else 12 if streak == 2 else 0
    rs += 10 if c.get("oneword") else 0
    amount = _num(c.get("amount_yi"), _num(c.get("amt_yi_kline"), 99))
    amount = amount if amount is not None else 99
    rs += 10 if amount < 2 else 0
    cap = _num(c.get("float_cap_yi"))
    rs += 8 if cap is not None and cap < 50 else 0
    return min(100, rs)


def _ic_cap(validation: dict[str, Any]) -> str:
    ic = _num(validation.get("rank_ic"), -999) or -999
    verdict = str(validation.get("verdict", ""))
    if ic < 0.02:
        return "中低"
    if ic < 0.04:
        return "中"
    if "未通过" in verdict or "不足" in verdict:
        return "中"
    if ic >= 0.06 and (_num(validation.get("excess_vs_market"), -999) or -999) > 1 and \
       (_num(validation.get("top_win_rate"), -999) or -999) > 55 and "通过" in verdict:
        return "高"
    if ic >= 0.04 and "通过" in verdict:
        return "中高"
    return "中"


def _market_gate_calc(gate: dict[str, Any]) -> tuple[float, str, int]:
    score = 50.0
    indices = gate.get("indices") or {}
    for key in ("sh000001", "sz399006"):
        row = indices.get(key)
        if not row:
            continue
        score += 8 if row.get("above_ma20") else -10
        chg = _num(row.get("chg"), 0)
        chg = chg if chg is not None else 0
        if chg <= -2:
            score -= 12
        elif chg <= -0.7:
            score -= 6
        elif chg >= 1:
            score += 4
        if row.get("heavy_sell"):
            score -= 8
        if int(row.get("down_streak", 0) or 0) >= 3:
            score -= 5
    senti = gate.get("sentiment")
    if senti:
        zt = int(senti.get("zt_count", 0) or 0)
        zbr = _num(senti.get("zb_rate"), 0)
        zbr = zbr if zbr is not None else 0
        dtc = int(senti.get("dt_count", 0) or 0)
        if zt >= 80:
            score += 10
        elif zt >= 40:
            score += 5
        elif zt < 25:
            score -= 10
        if zbr > 40:
            score -= 10
        elif zbr < 20 and zt >= 40:
            score += 5
        if dtc >= 20:
            score -= 10
        if dtc > zt:
            score -= 10
        if int(senti.get("max_streak", 0) or 0) >= 6 and zbr > 35:
            score -= 5
        if senti.get("zt_shrink"):
            score -= 8
        prev = int(senti.get("zt_prev", 0) or 0)
        if prev and zt >= 80 and zt >= prev * 1.7 and zbr >= 22:
            score -= 10
    score = max(0.0, min(100.0, round(score, 1)))
    effective = score
    for boundary in (40.0, 55.0, 70.0):
        if boundary <= score < boundary + 3:
            effective = boundary - 0.1
            break
    if effective >= 70:
        return score, "进攻(risk-on)", 60
    if effective >= 55:
        return score, "中性", 50
    if effective >= 40:
        return score, "防守(risk-off)", 30
    return score, "观望(risk-off重度)", 15


def market_gate_check(obj: dict[str, Any]) -> Result:
    out = Result("weekly.market-gate")
    env = obj.get("market_env") or {}
    gate_path = DATA / "market_gate_latest.json"
    out.check(gate_path.is_file(), "缺 market_gate_latest.json")
    if not gate_path.is_file():
        return out
    gate = _load(gate_path)
    out.merge(validate_market_gate_artifact(gate))
    gate_date = str((gate.get("sentiment") or {}).get("date") or
                    ((gate.get("indices") or {}).get("sh000001") or {}).get("date") or "")
    out.check(gate_date == str(obj.get("as_of", "")), "market_gate T 与选股 T 不一致")
    for field in ("score", "regime", "max_total_position_pct", "plan"):
        out.check(env.get(field) == gate.get(field), f"报告 market_env.{field} 与 Agent⓪ 输出不一致")
    out.check(str(env.get("as_of", "")) == str(obj.get("as_of", "")), "报告 market_env T 与选股 T 不一致")
    return out


def validate_market_gate_artifact(gate: dict[str, Any]) -> Result:
    out = Result("market-gate-artifact")
    score, regime, position = _market_gate_calc(gate)
    out.check(_close(gate.get("score"), score, 0.001), "Agent⓪ 环境分重算失败")
    out.check(gate.get("regime") == regime, "Agent⓪ regime 与分档/U4临界带不符")
    out.check(_close(gate.get("max_total_position_pct"), position, 0.001), "Agent⓪ 总仓上限不符")
    dates = {str(x.get("date", "")) for x in (gate.get("indices") or {}).values() if x.get("date")}
    out.check(len(dates) == 1, "Agent⓪ 四大指数 T 不一致")
    if gate.get("sentiment"):
        out.check(str((gate.get("sentiment") or {}).get("date", "")) in dates,
                  "Agent⓪ 情绪池 T 与指数 T 不一致")
    out.check(bool(re.search(r"(?:\+08:00|北京时间)", str(gate.get("cn_time", "")) + str(gate.get("generated_at", "")))),
              "Agent⓪ 时间戳未明确北京时间")
    for idx, row in enumerate(gate.get("overnight") or []):
        age = _num(row.get("age_h"))
        out.check(age is not None and age >= 0, f"外盘[{idx}] age_h 无效")
        out.check(bool(re.match(r"^20\d{2}-\d{2}-\d{2} \d{2}:\d{2}$", str(row.get("quote_time_cn", "")))),
                  f"外盘[{idx}] 缺北京时间报价戳")
    return out


def validate_weekly(obj: dict[str, Any], strict: bool = True) -> Result:
    out = Result("weekly-ashare-rank")
    candidates = list(obj.get("candidates") or [])
    out.check(bool(DATE_RE.match(str(obj.get("as_of", "")))), "as_of 缺失或无效")
    out.check(0 < len(candidates) <= 8, "最终 candidates 必须保留 1~8 只")
    out.check(len(_code_list(candidates)) == len(set(_code_list(candidates))), "candidates 代码重复")
    weights = obj.get("weights") if isinstance(obj.get("weights"), dict) else {}
    for idx, c in enumerate(candidates, 1):
        code = str(c.get("code", "?"))
        out.check(str(c.get("last_date", obj.get("as_of", ""))) == str(obj.get("as_of", "")),
                  f"{code} K线 T 与顶层 as_of 不一致")
        comps = _weekly_components(c, weights)
        for field_name, value in zip(("mom", "vol_score", "tech", "tape_score", "pull_score"), comps[:5]):
            out.check(_close(c.get(field_name), round(value, 1), 0.11), f"{code} {field_name} 重算失败")
        out.check(_close(c.get("penalty"), round(comps[5], 1), 0.11), f"{code} penalty 重算失败")
        out.check(_close(c.get("score"), round(comps[6], 1), 0.11), f"{code} quant score 重算失败")
        out.check(_close(c.get("risk_score"), _weekly_risk(c), 0.001), f"{code} risk_score 重算失败")
        expected_rank = round(float(c.get("score", 0)) + float(c.get("event_adj", 0))
                              - (6 if c.get("flow_x") == 1 else 0)
                              - (8 if c.get("cashout_soft") else 0)
                              - (6 if c.get("upsh_soft") else 0), 1)
        out.check(_close(c.get("rank_score"), expected_rank, 0.051), f"{code} rank_score 重算失败")
        sources = (c.get("verify") or {}).get("sources") or {}
        status = str((c.get("verify") or {}).get("status", ""))
        values = [_num(x) for x in sources.values()]
        values = [x for x in values if x is not None]
        dev = (max(values) - min(values)) / max(values) * 100 if len(values) >= 2 else None
        if dev is not None:
            out.check(_close((c.get("verify") or {}).get("dev_pct"), round(dev, 2), 0.011),
                      f"{code} 跨源偏差重算失败")
        if status.startswith("一致"):
            out.check(len(values) >= 2 and dev is not None and dev <= 1.0,
                      f"{code} 标一致但不足两源或偏差>1%")
        if status.startswith("存疑"):
            out.check(dev is not None and dev > 1.0, f"{code} 标存疑但偏差未超过1%")
        if status.startswith("单源"):
            out.check("✓已验证" not in str(c.get("risk_note", "")), f"{code} 单源却标 ✓已验证")
        if c.get("entry_status") == "可买":
            stop, buy_low, target = _num(c.get("stop")), _num(c.get("buy_low")), _num(c.get("target"))
            out.check(None not in (stop, buy_low, target) and stop < buy_low < target,
                      f"{code} 可买票价位链错误")
            rr_value = _rr_num(c.get("rr"))
            out.check(rr_value is not None and rr_value >= 1.8, f"{code} 可买票 R:R<1.8")
            fresh_text = str(c.get("p13_fresh7") or c.get("fresh_override") or "")
            dates = re.findall(r"20\d{2}-\d{2}-\d{2}", fresh_text)
            as_of_date = _date(obj.get("as_of"))
            out.check(bool(dates) and as_of_date is not None and any(
                _date(x) is not None and 0 <= (as_of_date - _date(x)).days <= 7 for x in dates
            ), f"{code} 可买票缺可机械核定的≤7天催化日期")
        atr = _num(c.get("atr_pct"), 99)
        ret5 = _num(c.get("ret5"), 99)
        vr = _num(c.get("vr"), 99)
        rng = _num(c.get("rng_pos"), 99)
        steady = int(c.get("weak_n", 0) or 0) == 0 and atr is not None and atr <= 4 \
            and ret5 is not None and ret5 <= 10 and vr is not None and vr <= 2 \
            and rng is not None and rng <= 85
        out.check(bool(c.get("steady_ok")) == steady, f"{code} P14 steady_ok 计算错误")
        if c.get("p14_pick"):
            out.check(idx <= 2 and steady and bool(c.get("p13_fresh7") or c.get("fresh_override")),
                      f"{code} P14 首选不满足前2/稳健/新鲜催化")

    p14 = [c for c in candidates if c.get("p14_pick")]
    out.check(len({str(c.get("industry")) for c in p14}) == len(p14), "P14 两票行业重复")
    env = obj.get("market_env") or {}
    regime = str(env.get("regime", ""))
    per_stock_cap = 0 if "观望" in regime else 6 if "防守" in regime else 8 if "中性" in regime else None
    for c in candidates:
        if per_stock_cap is not None:
            out.check((_num(c.get("position_pct"), 0) or 0) <= per_stock_cap,
                      f"{c.get('code')} 单票仓位超过 Agent⓪ 上限 {per_stock_cap}%")
    if regime and "进攻" not in regime:
        for idx, c in enumerate(candidates, 1):
            if c.get("entry_status") == "可买":
                out.check(idx <= 3 and bool(c.get("p13_fresh7") or c.get("fresh_override")),
                          f"{c.get('code')} 非进攻档未通过 P13-1 双门槛")

    validation = obj.get("validation") or {}
    if strict:
        out.check(bool(validation), "缺少 validation，策略未验证")
        out.check(validation.get("fwd_days") == obj.get("hold_days"), "validation fwd_days 与持有天数不符")
        run_date = _date(obj.get("generated_at"))
        val_date = _date(validation.get("generated_at"))
        out.check(run_date is not None and val_date is not None and 0 <= (run_date - val_date).days <= 7,
                  "validation 已过期、晚于本次运行或日期无效")
        out.merge(market_gate_check(obj))
        out.merge(audit_common(obj, "weekly-ashare-rank", _code_list(candidates)))
        out.merge(f10_cross_check(obj, "weekly-ashare-rank"))
        audit = obj.get("codex_audit") or {}
        final_codes = [str(x) for x in audit.get("final_codes") or []]
        out.check(final_codes == _code_list(candidates), "文字 final_codes 与 candidates 物理顺序不一致")
        expected_verified = [str(c.get("code")) for c in candidates
                             if str((c.get("verify") or {}).get("status", "")).startswith("一致")]
        actual_verified = [str(x) for x in audit.get("verified_codes") or []]
        out.check(actual_verified == expected_verified, "verified_codes 与原引擎跨源结果不一致")
        for c in candidates:
            code = str(c.get("code"))
            mark = str(c.get("risk_note", ""))
            if code in expected_verified:
                out.check("✓已验证" in mark, f"{code} 缺 ✓已验证 可见标记")
            else:
                out.check("⚠" in mark and "✓已验证" not in mark, f"{code} 存疑/单源缺降级标记")
        cap = _ic_cap(validation)
        confs = audit.get("confidence_by_code") or {}
        for code in _code_list(candidates):
            declared = _normalize_conf(confs.get(code))
            out.check(declared in CONF_ORDER, f"{code} 缺结构化置信度")
            if declared in CONF_ORDER:
                out.check(CONF_ORDER[declared] <= CONF_ORDER[cap], f"{code} 置信度 {declared} 超过 IC 上限 {cap}")
        if validation.get("val_market_regime") == "上涨段" and (_num(env.get("score"), 999) or 999) < 55:
            warning = str(audit.get("strategy_warning", ""))
            out.check("未在" in warning and "退潮" in warning, "顺风 IC 用于退潮环境但缺强制警示")
    return out


def validate_weekly_engine(obj: dict[str, Any], gate: dict[str, Any]) -> Result:
    """校验 Agent① 默认全市场原始结果（裁剪/人工排名前），覆盖全部候选。"""
    out = Result("weekly-engine")
    candidates = list(obj.get("candidates") or [])
    out.check(_date(obj.get("as_of")) is not None, "weekly 引擎 as_of 无效")
    out.check("+08:00" in str(obj.get("generated_at", "")), "weekly 引擎 generated_at 非北京时间")
    # 默认 top=20；SKILL 的稀缺候选安全阀允许显式 top=30 后重验，禁止继续无界扩池。
    out.check(1 <= len(candidates) <= 30, "weekly 原始候选数不在 1~30（默认20，安全阀最多30）")
    out.check(int(obj.get("universe_after_filter", 0) or 0) >= len(candidates), "weekly universe 计数错误")
    out.check(int(obj.get("scored", 0) or 0) >= len(candidates), "weekly scored 计数错误")
    out.check(bool(obj.get("spot_source")), "weekly 缺行情源")
    weights = obj.get("weights") or {}
    for c in candidates:
        code = str(c.get("code", ""))
        out.check(bool(WEEKLY_TRADABLE_CODE_RE.fullmatch(code)),
                  f"weekly 最终/原始候选含不可交易代码 {code or '<缺失>'}：仅允许 00/30/60 前缀；"
                  "科创板 68*（含 688/689）必须排除")
        persisted_stop = _num(c.get("stop"))
        out.check(persisted_stop is not None and persisted_stop > 0,
                  f"{code} 缺失已持久化的正值 stop；拒绝使用 recheck 旧工件兼容回退")
        out.check(str(c.get("last_date", "")) == str(obj.get("as_of", "")), f"{code} K线 T 不一致")
        comps = _weekly_components(c, weights)
        for field, value in zip(("mom", "vol_score", "tech", "tape_score", "pull_score"), comps[:5]):
            out.check(_close(c.get(field), round(value, 1), 0.11), f"{code} {field} 重算失败")
        out.check(_close(c.get("penalty"), round(comps[5], 1), 0.11), f"{code} penalty 重算失败")
        out.check(_close(c.get("score"), round(comps[6], 1), 0.11), f"{code} score 重算失败")
        out.check(_close(c.get("risk_score"), _weekly_risk(c), 0.001), f"{code} risk_score 重算失败")
        rank = round(float(c.get("score", 0)) + float(c.get("event_adj", 0))
                     - (6 if c.get("flow_x") == 1 else 0)
                     - (8 if c.get("cashout_soft") else 0)
                     - (6 if c.get("upsh_soft") else 0), 1)
        out.check(_close(c.get("rank_score"), rank, 0.051), f"{code} rank_score 重算失败")
        verify = c.get("verify") or {}
        raw_values = [_num(x) for x in (verify.get("sources") or {}).values()]
        values = [x for x in raw_values if x is not None and x > 0]
        out.check(len(values) == len(raw_values), f"{code} 跨源验价含缺失或非正数价格")
        dev = (max(values) - min(values)) / max(values) * 100 if len(values) >= 2 else None
        cross_source_ok = (len(values) >= 2 and dev is not None and dev <= 1.0 and
                           str(verify.get("status", "")).startswith("一致"))
        out.check(cross_source_ok, f"{code} 联网烟测跨源验价未通过")
        if dev is not None:
            out.check(_close(verify.get("dev_pct"), round(dev, 2), 0.011), f"{code} dev_pct 重算失败")
        if cross_source_ok:
            close = _num(c.get("close"))
            consensus = statistics.median(values) if values else None
            close_ok = close is not None and close > 0
            consensus_ok = consensus is not None and consensus > 0
            out.check(close_ok, f"{code} 引擎 close 缺失或非正数，无法核对跨源共识")
            out.check(consensus_ok, f"{code} 跨源中位共识缺失或非正数")
            if close_ok and consensus_ok:
                close_dev = abs(close - consensus) / consensus * 100
                out.check(close_dev <= 0.5,
                          f"{code} 引擎 close 与跨源中位共识偏离 {close_dev:.2f}% > 0.5%；"
                          "疑似 stale-cache，请使用 --refresh 重拉行情")
        out.check("recent_notices" in c and "forecast" in c and "has_fresh_event" in c,
                  f"{code} 公告/F10 种子字段缺失")
    out.merge(validate_market_gate_artifact(gate))
    gate_date = str((gate.get("sentiment") or {}).get("date") or
                    ((gate.get("indices") or {}).get("sh000001") or {}).get("date") or "")
    out.check(gate_date == str(obj.get("as_of", "")), "weekly 引擎与 Agent⓪ T 不一致")
    env = obj.get("market_env") or {}
    for field in ("score", "regime", "max_total_position_pct", "plan"):
        out.check(env.get(field) == gate.get(field), f"weekly market_env.{field} 不一致")
    if "观望" in str(env.get("regime", "")):
        out.check(all((_num(c.get("position_pct"), 0) or 0) == 0 for c in candidates),
                  "观望档仍有非零个股仓位")
        out.check(all(c.get("entry_status") != "可买" for c in candidates), "观望档仍有可买票")
    return out


def validate_html(skill: str, obj: dict[str, Any], html_path: pathlib.Path, strict: bool = True,
                  require_bottom_search: bool = False) -> Result:
    out = Result(f"{skill}.html")
    out.check(html_path.is_file(), f"HTML 不存在: {html_path}")
    if not html_path.is_file():
        return out
    raw = html_path.read_text(encoding="utf-8-sig")
    plain = _plain_html(raw)
    link_targets = _html_link_targets(raw)
    if strict:
        if skill == "bottom-fishing":
            stamp_field = "adjudicated_at" if obj.get("adjudicated") else "generated_at"
            stamp = _beijing_datetime(obj.get(stamp_field))
            expected_name = None
            if stamp is not None:
                tag = "_裁定版" if obj.get("adjudicated") else ""
                expected_name = f"bottom_cn_{stamp.strftime('%Y-%m-%d_%H-%M-%S')}{tag}.html"
            out.check(expected_name is not None and html_path.name == expected_name,
                      f"bottom 报告文件名不是严格北京时间戳: {html_path.name} != {expected_name}")
        elif skill == "weekly-ashare-rank":
            match = re.search(r"生成\(中国时间\).*?(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", plain)
            expected_name = None
            if match:
                expected_name = f"ashare_rank_cn_{match.group(1)}_{match.group(2).replace(':', '-')}.html"
            out.check(expected_name is not None and html_path.name == expected_name,
                      f"weekly 报告文件名不是严格北京时间戳: {html_path.name} != {expected_name}")
        out.check("Claude" not in plain and "WebSearch" not in plain, "最终 HTML 仍含 Claude/WebSearch 品牌残留")
        out.check(AUDIT_START in raw and AUDIT_END in raw, "最终 HTML 缺 Codex 可见审计附录")
        for idx, fact in enumerate((obj.get("codex_audit") or {}).get("facts") or []):
            url = str(fact.get("source_url", ""))
            out.check(url in link_targets, f"HTML 缺 facts[{idx}] 来源 URL")
            out.check(str(fact.get("published_at", "")) in plain, f"HTML 缺 facts[{idx}] 发布日期")
    out.check("非投资建议" in plain or "不构成投资" in plain, "HTML 缺风险/非投资建议声明")
    if skill == "bottom-fishing":
        search = (obj.get("codex_audit") or {}).get("bottom_search")
        if require_bottom_search or isinstance(search, dict):
            out.check(isinstance(search, dict), "HTML 最终验收要求 bottom_search")
            if isinstance(search, dict):
                out.check("搜索覆盖与时点增量" in plain and "bottom-search-audit/v1" in plain,
                          "HTML 缺搜索覆盖与时点增量附录")
                for code in search.get("coverage_by_code") or {}:
                    out.check(str(code) in plain, f"HTML 搜索附录缺候选代码 {code}")
                for idx, source in enumerate(search.get("sources") or []):
                    url = str(source.get("access_url", ""))
                    out.check(url in link_targets,
                              f"HTML 搜索附录缺 sources[{idx}] access_url")
        expected = _code_list(obj.get("candidates") or [])
        positions = [plain.find(code) for code in expected]
        out.check(all(x >= 0 for x in positions), "HTML 缺候选代码")
        out.check(positions == sorted(positions), "HTML 候选顺序与 JSON 不一致")
        for row in obj.get("candidates") or []:
            if row.get("judge") != "✓":
                m = re.search(rf"{re.escape(str(row.get('code')))}.*?(?=(?:00|30|60|68)\d{{4}}|观察池|$)", plain)
                if m:
                    out.check("买入区" not in m.group(0), f"{row.get('code')} 非✓仍显示买入区")
    elif skill == "stock-diagnostic":
        out.check(str(obj.get("code", "")) in plain, "HTML 缺股票代码")
        out.check(str(obj.get("final_score", "")) in plain, "HTML 缺 final_score")
        for dim, score in _agent_scores(obj).items():
            out.check(dim in plain and str(int(score) if score.is_integer() else score) in plain,
                      f"HTML 缺 Agent{dim} 分数")
        out.check(not (obj.get("verification") or {}).get("failed"), "verification failed 时不得发布 HTML")
        if strict:
            for dim, card in ((obj.get("codex_audit") or {}).get("scorecards") or {}).items():
                for idx, item in enumerate(card.get("items") or []):
                    url = str(item.get("source_url", ""))
                    out.check(url in link_targets,
                              f"HTML 缺 Agent{dim} item[{idx}] 来源 URL")
                    out.check(str(item.get("published_at", "")) in plain,
                              f"HTML 缺 Agent{dim} item[{idx}] 来源日期")
    elif skill == "weekly-ashare-rank":
        expected = _code_list(obj.get("candidates") or [])
        actual = re.findall(r"class=tk>(\d{6})", raw)
        out.check(actual == expected, f"HTML 卡片顺序与 candidates 不一致: {actual} != {expected}")
        out.check("市场环境" in plain and "策略验证" in plain, "HTML 缺市场横幅或策略验证")
        regime = str((obj.get("market_env") or {}).get("regime", ""))
        if "观望" in regime:
            out.check(not re.search(r"<(?:i|b)>\s*(?:参考价|买入价).*?\d", raw, re.S),
                      "观望档 HTML 仍显示操作价位")
            out.check("P13-3" in plain or "不给价位" in plain, "观望档缺 P13-3 不给价位说明")
        for c in obj.get("candidates") or []:
            out.check(str(c.get("entry_status", "")) in plain, f"HTML 缺 {c.get('code')} 状态徽标")
            out.check(str(c.get("risk_note", "")) in plain, f"HTML 缺 {c.get('code')} 核验标记/风险说明")
    return out


def brand_report(path: pathlib.Path) -> Result:
    out = Result("brand-report")
    out.check(path.is_file(), f"报告不存在: {path}")
    if not path.is_file():
        return out
    raw = path.read_text(encoding="utf-8-sig")
    protected: list[str] = []

    def protect_url(match: re.Match[str]) -> str:
        protected.append(match.group(3))
        return f"{match.group(1)}{match.group(2)}__CODEX_URL_{len(protected) - 1}__{match.group(2)}"

    branded = re.sub(r"(?is)(\b(?:href|src)\s*=\s*)(['\"])(.*?)\2", protect_url, raw)
    branded = branded.replace("Claude Code", "Codex").replace("Claude WebSearch", "Codex 网页检索")
    branded = branded.replace("Claude", "Codex").replace("WebSearch", "网页检索").replace("WebFetch", "网页打开")
    for idx, url in enumerate(protected):
        branded = branded.replace(f"__CODEX_URL_{idx}__", url)
    if branded != raw:
        path.write_text(branded, encoding="utf-8")
    visible = _plain_html(branded)
    out.check("Claude" not in visible and "WebSearch" not in visible, "品牌替换不完整")
    return out


AUDIT_START = "<!-- codex-audit-v1:start -->"
AUDIT_END = "<!-- codex-audit-v1:end -->"


def augment_report(path: pathlib.Path, obj: dict[str, Any], skill: str) -> Result:
    """在原 renderer 产物尾部追加可见审计附录；不改变原卡片/字段/排序。"""
    out = Result("augment-report")
    out.check(path.is_file(), f"报告不存在: {path}")
    audit = obj.get("codex_audit")
    out.check(isinstance(audit, dict), "JSON 缺 codex_audit，无法生成审计附录")
    if not out.passed:
        return out
    for idx, fact in enumerate(audit.get("facts") or []):
        out.check(_valid_http_url(fact.get("source_url")), f"facts[{idx}].source_url 无效，拒绝写入 HTML")
    if skill == "stock-diagnostic":
        for dim, card in (audit.get("scorecards") or {}).items():
            for idx, item in enumerate(card.get("items") or []):
                out.check(_valid_http_url(item.get("source_url")),
                          f"scorecards.{dim}.items[{idx}].source_url 无效，拒绝写入 HTML")
    search = audit.get("bottom_search")
    if isinstance(search, dict):
        for idx, source in enumerate(search.get("sources") or []):
            out.check(_valid_http_url(source.get("access_url")),
                      f"bottom_search.sources[{idx}].access_url 无效，拒绝写入 HTML")
            canonical = source.get("canonical_url")
            if canonical:
                out.check(_valid_http_url(canonical),
                          f"bottom_search.sources[{idx}].canonical_url 无效，拒绝写入 HTML")
        for idx, query in enumerate(search.get("queries") or []):
            for url in query.get("reviewed_urls") or []:
                out.check(_valid_http_url(url),
                          f"bottom_search.queries[{idx}].reviewed_urls 含无效 URL，拒绝写入 HTML")
    if not out.passed:
        return out
    raw = path.read_text(encoding="utf-8-sig")
    raw = re.sub(re.escape(AUDIT_START) + r".*?" + re.escape(AUDIT_END), "", raw, flags=re.S)

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""), quote=True)

    fact_rows = []
    for fact in audit.get("facts") or []:
        url = str(fact.get("source_url", ""))
        source = f"<a href='{esc(url)}'>{esc(fact.get('source_name') or url)}</a>"
        delta = _num(fact.get("rubric_delta"), 0)
        fact_rows.append(
            "<tr>"
            f"<td>{esc(fact.get('code'))}</td><td>{esc(fact.get('fact'))}</td>"
            f"<td>{source}<br>{esc(fact.get('published_at'))} 发布<br>"
            f"{esc(fact.get('retrieved_at_beijing'))} 北京时间检索</td>"
            f"<td>{esc(fact.get('f10_match'))}</td><td>{delta:+g}</td>"
            f"<td>{esc(fact.get('reasoning'))}<br><b>反方：</b>{esc(fact.get('counter_argument'))}<br>"
            f"<b>结论：</b>{esc(fact.get('conclusion'))}</td></tr>"
        )
    score_html = ""
    if skill == "stock-diagnostic":
        score_rows = []
        for dim in ("②", "③", "④"):
            card = (audit.get("scorecards") or {}).get(dim) or {}
            details = "<br>".join(
                f"{_num(x.get('delta'), 0):+g} {esc(x.get('reason'))} "
                f"[<a href='{esc(x.get('source_url'))}'>{esc(x.get('published_at'))}</a>]"
                for x in card.get("items") or []
            )
            score_rows.append(f"<tr><td>{dim}</td><td>50</td><td>{details}</td><td>{esc(card.get('final'))}</td></tr>")
        score_html = (
            "<h3>①~⑤ 公式与 ②③④ 从50分起的加减</h3>"
            f"<pre>{esc(json.dumps({'technical_score': audit.get('technical_score'), 'risk_breakdown': audit.get('risk_breakdown'), 'market_adjustment': audit.get('market_adjustment'), 'hard_gates': audit.get('hard_gates')}, ensure_ascii=False, indent=2))}</pre>"
            "<table><tr><th>角色</th><th>起点</th><th>逐项加减与来源</th><th>最终</th></tr>"
            + "".join(score_rows) + "</table>"
        )
    search_html = ""
    if skill == "bottom-fishing" and isinstance(search, dict):
        coverage_rows = []
        for code, block in (search.get("coverage_by_code") or {}).items():
            categories = block.get("categories") or {}
            status_text = "；".join(
                f"{name}={esc((categories.get(name) or {}).get('status'))}"
                for name in BOTTOM_SEARCH_CATEGORIES
            )
            latest = block.get("official_latest_check") or {}
            ruling = block.get("ruling_evidence") or {}
            coverage_rows.append(
                f"<tr><td>{esc(code)}</td><td>{status_text}</td>"
                f"<td>{esc(latest.get('latest_pre_t'))}<br>{esc('；'.join(str(x) for x in latest.get('checked_sources') or []))}</td>"
                f"<td>{esc('；'.join(str(x) for x in ruling.get('unresolved_query_ids') or []) or '无')}</td></tr>"
            )
        source_rows = []
        for source in search.get("sources") or []:
            canonical = str(source.get("canonical_url") or "")
            canonical_link = (f"<br>官方：<a href='{esc(canonical)}'>{esc(source.get('canonical_publisher') or canonical)}</a>"
                              if canonical and canonical != str(source.get("access_url") or "") else "")
            source_rows.append(
                f"<tr><td>{esc(source.get('source_ref'))}</td><td>{esc(source.get('source_kind'))}</td>"
                f"<td>{esc(source.get('origin_id'))}</td>"
                f"<td><a href='{esc(source.get('access_url'))}'>{esc(source.get('access_publisher'))}</a>{canonical_link}</td></tr>"
            )
        post_rows = []
        for code, post in (search.get("post_t_safety_by_code") or {}).items():
            items = "<br>".join(
                f"{esc(item.get('published_at'))} {esc(item.get('summary'))} "
                f"[{esc(item.get('polarity'))}/{esc(item.get('effect'))}]"
                for item in post.get("items") or []
            ) or "无 T 后增量"
            post_rows.append(
                f"<tr><td>{esc(code)}</td><td>{esc(post.get('base_verdict_asof_t'))}</td>"
                f"<td>{esc(post.get('effective_verdict'))}</td><td>{esc(post.get('checked_through_beijing'))}</td>"
                f"<td>{items}</td></tr>"
            )
        query_rows = []
        for query in search.get("queries") or []:
            urls = " ".join(
                f"<a href='{esc(url)}'>链接{idx + 1}</a>"
                for idx, url in enumerate(query.get("reviewed_urls") or [])
            ) or "无链接"
            query_rows.append(
                f"<tr><td>{esc(query.get('query_id'))}</td><td>{esc(query.get('code'))}</td>"
                f"<td>{esc(query.get('phase'))}/{esc(query.get('category'))}/{esc(query.get('query_mode'))}</td>"
                f"<td>{esc(query.get('date_from'))} 至 {esc(query.get('date_to'))}</td>"
                f"<td>{esc(query.get('outcome'))}<br>{urls}<br>{esc(query.get('notes'))}</td></tr>"
            )
        ledger_rows = []
        for seed in search.get("f10_seed_ledger") or []:
            ledger_rows.append(
                f"<tr><td>{esc(seed.get('seed_key'))}</td><td>{esc(seed.get('timing'))}</td>"
                f"<td>{esc(seed.get('disposition'))}</td>"
                f"<td>{esc('；'.join(str(x) for x in seed.get('fact_ids') or []) or '无')}</td>"
                f"<td>{esc('；'.join(str(x) for x in seed.get('delta_ids') or []) or '无')}</td>"
                f"<td>{esc(seed.get('reason'))}</td></tr>"
            )
        empty_html = (f"<p><b>零候选说明：</b>{esc(search.get('empty_reason'))}</p>"
                      if search.get("empty_reason") else "")
        search_html = (
            "<h3>搜索覆盖与时点增量</h3>"
            f"<p><b>协议：</b>{esc(search.get('version'))}　<b>T：</b>{esc(search.get('T'))}　"
            f"<b>裁定截止：</b>{esc(search.get('cutoff_beijing'))}　"
            f"<b>末端检查：</b>{esc(search.get('retrieved_at_beijing'))}</p>"
            + empty_html
            + "<table><tr><th>代码</th><th>六维状态</th><th>最新T前官方回扫</th><th>未决查询</th></tr>"
            + "".join(coverage_rows) + "</table>"
            "<h4>来源血缘</h4><table><tr><th>source_ref</th><th>类型</th><th>origin_id</th><th>访问与官方原文</th></tr>"
            + "".join(source_rows) + "</table>"
            "<h4>T 后安全增量（只维持或降级）</h4>"
            "<table><tr><th>代码</th><th>as-of-T</th><th>有效裁定</th><th>检查至</th><th>增量</th></tr>"
            + "".join(post_rows) + "</table>"
            "<details><summary>F10 逐条对账</summary>"
            "<table><tr><th>seed_key</th><th>时点</th><th>处置</th><th>facts</th><th>deltas</th><th>理由</th></tr>"
            + "".join(ledger_rows) + "</table></details>"
            "<details><summary>查询日志</summary>"
            "<table><tr><th>ID</th><th>代码</th><th>阶段/类别/模式</th><th>日期窗</th><th>结果/链接/说明</th></tr>"
            + "".join(query_rows) + "</table></details>"
        )
    price_rows = []
    for code, row in (audit.get("price_verification_by_code") or {}).items():
        price_rows.append(f"<tr><td>{esc(code)}</td><td>{esc(row.get('as_of'))}</td>"
                          f"<td>{esc(json.dumps(row.get('usable_sources') or {}, ensure_ascii=False))}</td>"
                          f"<td>{esc(row.get('max_dev_pct'))}</td><td>{esc(row.get('status'))}</td></tr>")
    challenge = audit.get("contrarian_challenge") or {}
    review = audit.get("auditor_review") or {}
    appendix = (
        AUDIT_START
        + "<section id='codex-audit-v1' style='margin:28px auto;padding:20px;max-width:1180px;"
          "border:2px solid #334155;border-radius:14px;background:#f8fafc;color:#0f172a'>"
          "<style>#codex-audit-v1 table{width:100%;border-collapse:collapse;font-size:12px}"
          "#codex-audit-v1 th,#codex-audit-v1 td{border:1px solid #cbd5e1;padding:7px;vertical-align:top}"
          "#codex-audit-v1 th{background:#e2e8f0}#codex-audit-v1 a{color:#0369a1}"
          "#codex-audit-v1 pre{white-space:pre-wrap;background:#eef2ff;padding:10px}</style>"
          "<h2>Codex 可审计裁定附录</h2>"
        + f"<p><b>证据截止：</b>{esc(audit.get('evidence_cutoff_beijing'))}　"
          f"<b>检索日：</b>{esc(audit.get('retrieved_on_beijing'))}</p>"
        + score_html
        + search_html
        + "<h3>事实、来源、日期、rubric 与反方解释</h3>"
          "<table><tr><th>代码</th><th>事实</th><th>来源/日期</th><th>F10比对</th><th>加减</th><th>推理/反方/结论</th></tr>"
        + "".join(fact_rows) + "</table>"
        + ("<h3>跨源收盘价</h3><table><tr><th>代码</th><th>T</th><th>同T来源</th><th>最大偏差%</th><th>状态</th></tr>"
           + "".join(price_rows) + "</table>" if price_rows else "")
        + f"<h3>反方挑战</h3><p><b>观点：</b>{esc('；'.join(str(x) for x in challenge.get('arguments') or []))}</p>"
          f"<p><b>回应：</b>{esc('；'.join(str(x) for x in challenge.get('responses') or []))}</p>"
          f"<h3>审计官复核</h3><p><b>状态：</b>{'通过' if review.get('passed') else '未通过'}　"
          f"<b>检查：</b>{esc('；'.join(str(x) for x in review.get('checks') or []))}</p>"
          "</section>"
        + AUDIT_END
    )
    if "</body>" in raw.lower():
        match = re.search(r"</body>", raw, flags=re.I)
        assert match is not None
        raw = raw[:match.start()] + appendix + raw[match.start():]
    else:
        raw += appendix
    path.write_text(raw, encoding="utf-8")
    out.check(AUDIT_START in raw and AUDIT_END in raw, "审计附录写入失败")
    return out


def attach_audit(result_path: pathlib.Path, audit_path: pathlib.Path) -> Result:
    """Copy the append-only codex_audit object into an engine result JSON."""
    out = Result("attach-audit")
    out.check(result_path.is_file(), f"结果 JSON 不存在: {result_path}")
    out.check(audit_path.is_file(), f"审计 JSON 不存在: {audit_path}")
    if not out.passed:
        return out
    result_obj = _load(result_path)
    audit_obj = _load(audit_path)
    audit = audit_obj.get("codex_audit")
    out.check(isinstance(audit, dict), "来源 JSON 缺少 codex_audit")
    if isinstance(audit, dict):
        result_obj["codex_audit"] = audit
        result_path.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def fixture_check() -> Result:
    out = Result("fixtures")
    out.merge(baseline_check())
    report_root = SKILLS_SOURCE
    html_count = json_count = 0
    for path in report_root.glob("*/reports/*"):
        if path.suffix.lower() == ".html":
            html_count += 1
            raw = path.read_text(encoding="utf-8-sig")
            wrapped = "<html" in raw.lower() and "</html>" in raw.lower()
            engine_fragment = "<meta" in raw.lower() and "<title" in raw.lower() and \
                              "<style" in raw.lower() and ("非投资建议" in raw or "不构成投资" in raw)
            out.check(wrapped or engine_fragment, f"HTML 既非完整文档也非原 renderer 合法片段: {path.name}")
        elif path.suffix.lower() == ".json":
            json_count += 1
            try:
                _load(path)
            except (OSError, json.JSONDecodeError) as exc:
                out.errors.append(f"JSON 无法解析: {path.name}: {exc}")
    out.check(html_count >= 56, f"迁移前历史 HTML 有缺失: {html_count} < 56")
    out.check(json_count >= 14, f"迁移前历史 JSON 有缺失: {json_count} < 14")

    bottom = _load(DATA / "bottom_latest.json")
    stock = _load(DATA / "diag_latest.json")
    weekly = _load(DATA / "rank_latest.json")
    out.merge(validate_bottom(bottom, strict=False))
    out.merge(validate_stock(stock, strict=False))
    out.merge(validate_weekly(weekly, strict=False))

    # In-memory failure injection: these three corruptions must be rejected.
    bad_bottom = copy.deepcopy(bottom)
    bad_bottom["market"]["T"] = "1999-01-01"
    out.check(not validate_bottom(bad_bottom, strict=False).passed, "负例失效: bottom T mismatch 未被拦截")
    bad_stock = copy.deepcopy(stock)
    bad_stock["final_score"] = 99
    out.check(not validate_stock(bad_stock, strict=False).passed, "负例失效: stock 算术错误未被拦截")
    bad_weekly = copy.deepcopy(weekly)
    if len(bad_weekly.get("candidates") or []) >= 2:
        bad_weekly["candidates"][0]["rank_score"] = -999
    out.check(not validate_weekly(bad_weekly, strict=False).passed, "负例失效: weekly rank_score 错误未被拦截")
    return out


def _test_fact(code: str, published: str, retrieved: str, url: str, f10_match: str = "not_applicable") -> dict[str, Any]:
    return {
        "code": code,
        "fact": "验收夹具事实（仅测试校验器，不作为投资证据）",
        "source_name": "验收夹具",
        "source_url": url,
        "published_at": published,
        "retrieved_at_beijing": retrieved,
        "event_date": published,
        "source_tier": "公告" if f10_match != "not_applicable" else "其他",
        "f10_match": f10_match,
        "reasoning": "用于验证结构化证据与原 rubric 的机械约束",
        "rubric_delta": 0,
        "counter_argument": "该事实可能不改变结论",
        "conclusion": "采用" if f10_match == "confirmed" else "无证据",
        "uncertainties": [],
    }


def _test_audit(skill: str, cutoff: str, retrieved: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "codex-trading-audit/v1",
        "skill": skill,
        "evidence_cutoff_beijing": f"{cutoff} 23:59:59+08:00",
        "retrieved_on_beijing": retrieved,
        "facts": facts,
        "contrarian_challenge": {"completed": True, "arguments": ["最强反方夹具"], "responses": ["已回应夹具"]},
        "auditor_review": {
            "passed": True,
            "checks": ["数据新鲜度", "跨源价格", "算术", "rubric", "价位逻辑", "引用"],
            "failed": [],
            "notes": "严格通过路径自测",
        },
    }


def _test_bottom_search_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Small complete graph used only to prove bottom-search positive/negative paths."""
    t, retrieved, code = "2026-01-10", "2026-01-12", "600000"
    notices = [
        "2026-01-10 测试公司:关于股东减持计划的公告",
        "2026-01-10 测试公司:关于重大诉讼进展的公告",
        "2026-01-11 测试公司:关于股份回购进展的公告",
    ]
    forecast = {"notice_date": "2026-01-09", "type": "预增", "content": "预计扣非净利润增长20%",
                "fresh": True}
    result_obj = {
        "generated_at": f"{retrieved}T09:55:00+08:00",
        "adjudicated": True,
        "adjudicated_at": f"{retrieved}T10:00:00+08:00",
        "T": t,
        "candidates": [{"code": code, "judge": "✓", "forecast": forecast, "notices": notices,
                        "f10_flag": ""}],
    }
    facts = [
        _test_fact(code, "2026-01-09", retrieved, "https://example.com/perf?x=1&y=2", "confirmed"),
        _test_fact(code, t, retrieved, "https://example.com/corporate", "confirmed"),
        _test_fact(code, t, retrieved, "https://example.com/governance", "confirmed"),
        _test_fact("MARKET", t, retrieved, "https://example.com/market"),
    ]
    graph_fields = [
        ("fact-perf", "performance_operations", "source-perf", [f"{code}:forecast"]),
        ("fact-corporate", "corporate_events", "source-corporate", [f"{code}:notices:0"]),
        ("fact-governance", "governance_regulatory", "source-governance", [f"{code}:notices:1"]),
        ("fact-market", "market_regime", "source-market", []),
    ]
    for fact, (fact_id, category, source_ref, seed_refs) in zip(facts, graph_fields):
        fact.update({"fact_id": fact_id, "category": category, "source_ref": source_ref,
                     "seed_refs": seed_refs})

    def query(query_id: str, query_code: str, category: str, mode: str, outcome: str,
              facts_selected: list[str] | None = None, deltas_selected: list[str] | None = None,
              phase: str = "as_of_t", date_from: str = "2025-01-01",
              date_to: str = t, reviewed_url: str = "https://example.com/search?x=1&y=2") -> dict[str, Any]:
        return {
            "query_id": query_id,
            "code": query_code,
            "category": category,
            "phase": phase,
            "query_mode": mode,
            "query_text": f"{query_code} {category} {mode}",
            "date_from": date_from,
            "date_to": date_to,
            "executed_at_beijing": f"{retrieved} 10:00:00+08:00",
            "outcome": outcome,
            "reviewed_urls": [reviewed_url],
            "selected_fact_ids": facts_selected or [],
            "selected_delta_ids": deltas_selected or [],
            "notes": "已检查官方入口和查询变体，未发现可核实重大证据" if outcome != "selected" else "采用",
        }

    queries = [
        query("q-perf", code, "performance_operations", "official", "selected", ["fact-perf"],
              reviewed_url="https://example.com/perf?x=1&y=2"),
        query("q-fin", code, "financial_credit", "broad_web", "no_relevant_hit"),
        query("q-fin-2", code, "financial_credit", "regulator", "no_relevant_hit"),
        query("q-gov", code, "governance_regulatory", "regulator", "selected", ["fact-governance"],
              date_from="2024-01-01", reviewed_url="https://example.com/governance"),
        query("q-corp", code, "corporate_events", "exact_title", "selected", ["fact-corporate"],
              reviewed_url="https://example.com/corporate"),
        query("q-industry", code, "industry_policy_domestic", "industry", "no_relevant_hit"),
        query("q-industry-2", code, "industry_policy_domestic", "broad_web", "no_relevant_hit"),
        query("q-global", code, "external_global_peer", "overseas", "no_relevant_hit"),
        query("q-global-2", code, "external_global_peer", "ah_cross_listing", "no_relevant_hit"),
        query("q-drop", code, "performance_operations", "drop_cause", "no_relevant_hit"),
        query("q-market", "MARKET", "market_regime", "broad_web", "selected", ["fact-market"],
              reviewed_url="https://example.com/market"),
        query("q-delta", code, "corporate_events", "freshness_delta", "selected", [], ["delta-positive"],
              "post_t_safety", "2026-01-11", retrieved, "https://example.com/delta"),
    ]
    categories: dict[str, dict[str, Any]] = {}
    mapping = {
        "performance_operations": ("hit", ["q-perf"], ["fact-perf"]),
        "financial_credit": ("no_relevant_hit", ["q-fin", "q-fin-2"], []),
        "governance_regulatory": ("hit", ["q-gov"], ["fact-governance"]),
        "corporate_events": ("hit", ["q-corp"], ["fact-corporate"]),
        "industry_policy_domestic": ("no_relevant_hit", ["q-industry", "q-industry-2"], []),
        "external_global_peer": ("no_relevant_hit", ["q-global", "q-global-2"], []),
    }
    for category, (status, query_ids, fact_ids) in mapping.items():
        categories[category] = {"status": status, "query_ids": query_ids, "fact_ids": fact_ids,
                                "reason": "夹具命中" if status == "hit" else "完成官方入口与查询变体，未命中"}
    sources = []
    for ref, url, publisher, origin in (
        ("source-perf", "https://example.com/perf?x=1&y=2", "交易所", "origin-perf"),
        ("source-corporate", "https://example.com/corporate", "公司IR", "origin-corporate"),
        ("source-governance", "https://example.com/governance", "监管机构", "origin-governance"),
        ("source-market", "https://example.com/market", "统计机构", "origin-market"),
        ("source-delta", "https://example.com/delta", "公司IR", "origin-delta"),
    ):
        sources.append({"source_ref": ref, "access_url": url, "access_publisher": publisher,
                        "source_kind": "official_direct", "origin_id": origin,
                        "canonical_url": url, "canonical_publisher": publisher, "match_basis": []})
    audit = _test_audit("bottom-fishing", t, retrieved, facts)
    audit["bottom_search"] = {
        "version": "bottom-search-audit/v1",
        "T": t,
        "cutoff_beijing": f"{t} 23:59:59+08:00",
        "retrieved_at_beijing": f"{retrieved} 10:00:00+08:00",
        "required_categories": list(BOTTOM_SEARCH_CATEGORIES),
        "sources": sources,
        "queries": queries,
        "coverage_by_code": {code: {
            "aliases": ["测试股份有限公司", "测试公司"],
            "profile_tags": ["内需"],
            "categories": categories,
            "official_latest_check": {"query_ids": ["q-perf"], "checked_sources": ["交易所", "公司IR"],
                                      "latest_pre_t": "2026-01-09"},
            "ruling_evidence": {"supporting_fact_ids": ["fact-perf", "fact-corporate"],
                                "adverse_fact_ids": ["fact-governance"],
                                "decision_fact_ids": ["fact-perf"], "unresolved_query_ids": []},
        }},
        "market_coverage": {"status": "hit", "query_ids": ["q-market"],
                            "fact_ids": ["fact-market"], "reason": "系统性踩踏核查"},
        "f10_seed_ledger": [
            {"seed_key": f"{code}:forecast", "code": code, "kind": "forecast", "raw_index": None,
             "seed_text": "2026-01-09 预增 预计扣非净利润增长20%", "raw_date": "2026-01-09",
             "timing": "pre_t", "disposition": "adjudicated_pre_t", "query_ids": ["q-perf"],
             "fact_ids": ["fact-perf"], "delta_ids": [], "reason": "逐条确认"},
            {"seed_key": f"{code}:notices:0", "code": code, "kind": "notice", "raw_index": 0,
             "seed_text": notices[0], "raw_date": t, "timing": "pre_t", "disposition": "adjudicated_pre_t",
             "query_ids": ["q-corp"], "fact_ids": ["fact-corporate"], "delta_ids": [], "reason": "逐条确认"},
            {"seed_key": f"{code}:notices:1", "code": code, "kind": "notice", "raw_index": 1,
             "seed_text": notices[1], "raw_date": t, "timing": "pre_t", "disposition": "adjudicated_pre_t",
             "query_ids": ["q-gov"], "fact_ids": ["fact-governance"], "delta_ids": [], "reason": "逐条确认"},
            {"seed_key": f"{code}:notices:2", "code": code, "kind": "notice", "raw_index": 2,
             "seed_text": notices[2], "raw_date": "2026-01-11", "timing": "post_t",
             "disposition": "quarantined_post_t", "query_ids": ["q-delta"], "fact_ids": [],
             "delta_ids": ["delta-positive"], "reason": "晚于T，隔离"},
        ],
        "post_t_safety_by_code": {code: {
            "checked_through_beijing": f"{retrieved} 10:00:00+08:00",
            "base_verdict_asof_t": "✓", "effective_verdict": "✓",
            "items": [{"delta_id": "delta-positive", "source_ref": "source-delta",
                       "published_at": "2026-01-11", "event_date": "2026-01-11",
                       "query_ids": ["q-delta"], "summary": notices[2], "polarity": "positive",
                       "effect": "none", "used_in_asof_t_verdict": False, "uncertainties": []}],
        }},
    }
    audit_doc = {"T": t, "rulings": {code: {"verdict": "✓"}}, "codex_audit": audit}
    return result_obj, audit_doc


def strict_self_test() -> Result:
    """用当前 schema 的内存夹具覆盖严格通过路径和关键失败注入，不污染生产 JSON。"""
    out = Result("self-test")
    out.merge(baseline_check())

    bottom = copy.deepcopy(_load(DATA / "bottom_latest.json"))
    b_date = str(bottom.get("T"))
    b_facts: list[dict[str, Any]] = []
    for row in bottom.get("candidates") or []:
        code = str(row.get("code"))
        fc = row.get("forecast") or {}
        published = str(fc.get("notice_date") or b_date)
        match = "conflict" if fc.get("notice_date") else "not_applicable"
        b_facts.append(_test_fact(code, published, b_date, f"https://example.com/bottom/{code}/{published}", match))
    bottom["codex_audit"] = _test_audit("bottom-fishing", b_date, b_date, b_facts)
    bottom["codex_audit"]["price_verification_by_code"] = {}
    for row in bottom.get("candidates") or []:
        if row.get("judge") == "✓":
            code = str(row.get("code"))
            close = float(row.get("close"))
            bottom["codex_audit"]["price_verification_by_code"][code] = {
                "as_of": b_date, "usable_sources": {"源A": close, "源B": close},
                "max_dev_pct": 0.0, "status": "verified",
            }
    out.merge(validate_bottom(bottom, strict=True))

    search_result, search_audit_doc = _test_bottom_search_case()
    out.merge(validate_bottom_search(search_result, search_audit_doc, required=True))

    bad_search = copy.deepcopy(search_audit_doc)
    del bad_search["codex_audit"]["bottom_search"]["coverage_by_code"]["600000"]["categories"][
        "external_global_peer"]
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: bottom_search 缺六维未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    ledger = bad_search["codex_audit"]["bottom_search"]["f10_seed_ledger"]
    bad_search["codex_audit"]["bottom_search"]["f10_seed_ledger"] = [
        row for row in ledger if row["seed_key"] != "600000:notices:1"
    ]
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: 同日 F10 公告漏一条未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    post_seed = next(row for row in bad_search["codex_audit"]["bottom_search"]["f10_seed_ledger"]
                     if row["seed_key"] == "600000:notices:2")
    post_seed["fact_ids"] = ["fact-perf"]
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: T 后 seed 混入主 fact 未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    block = bad_search["codex_audit"]["bottom_search"]["coverage_by_code"]["600000"]
    block["categories"]["financial_credit"].update({"status": "blocked", "query_ids": ["q-fin"]})
    q_fin = next(row for row in bad_search["codex_audit"]["bottom_search"]["queries"]
                 if row["query_id"] == "q-fin")
    q_fin["outcome"] = "blocked"
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: blocked 维度仍给 ✓ 未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    decisive = next(row for row in bad_search["codex_audit"]["bottom_search"]["sources"]
                    if row["source_ref"] == "source-perf")
    decisive.update({"source_kind": "verified_official_mirror",
                     "canonical_url": "https://official.example.com/perf", "match_basis": ["title"]})
    decisive.pop("document_id", None)
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: 镜像血缘不足仍作决定性证据")

    bad_search = copy.deepcopy(search_audit_doc)
    duplicate = copy.deepcopy(bad_search["codex_audit"]["bottom_search"]["sources"][0])
    duplicate.update({"source_ref": "source-fake-duplicate", "access_url": "https://mirror.example.com/perf",
                      "source_kind": "verified_official_mirror", "origin_id": "fake-origin",
                      "canonical_url": "https://example.com/perf?x=1&y=2",
                      "canonical_publisher": "交易所", "document_id": "doc-001",
                      "match_basis": ["title", "published_at"]})
    bad_search["codex_audit"]["bottom_search"]["sources"].append(duplicate)
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: 同 canonical_url 伪造独立 origin 未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    post = bad_search["codex_audit"]["bottom_search"]["post_t_safety_by_code"]["600000"]
    post["base_verdict_asof_t"] = "?"
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: T 后利好把 ? 升为 ✓ 未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    delta_source = next(row for row in bad_search["codex_audit"]["bottom_search"]["sources"]
                        if row["source_ref"] == "source-delta")
    delta_source["source_kind"] = "unverified_secondary"
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: 未核实二级 T 后线索被当作已验证利好")

    bad_search = copy.deepcopy(search_audit_doc)
    bad_search["codex_audit"]["bottom_search"]["coverage_by_code"]["600000"]["categories"][
        "financial_credit"]["query_ids"] = ["q-fin"]
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: no_relevant_hit 仅一条查询变体未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    bad_search["codex_audit"]["bottom_search"]["coverage_by_code"]["600000"]["categories"][
        "performance_operations"]["fact_ids"] = []
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: 孤儿候选 fact 未被拦截")

    bad_search = copy.deepcopy(search_audit_doc)
    bad_search["codex_audit"]["bottom_search"]["coverage_by_code"]["600000"]["ruling_evidence"][
        "decision_fact_ids"] = ["fact-governance"]
    out.check(not validate_bottom_search(search_result, bad_search, required=True).passed,
              "严格负例失效: ✓ 只引用 adverse 决定事实未被拦截")

    zero_t = "2026-01-10"
    zero_result = {"T": zero_t, "candidates": []}
    zero_audit = _test_audit("bottom-fishing", zero_t, zero_t, [])
    zero_audit["bottom_search"] = {
        "version": "bottom-search-audit/v1", "T": zero_t,
        "cutoff_beijing": f"{zero_t} 23:59:59+08:00",
        "retrieved_at_beijing": f"{zero_t} 20:00:00+08:00",
        "required_categories": list(BOTTOM_SEARCH_CATEGORIES), "empty_reason": "引擎本轮信号为零",
        "sources": [], "queries": [], "coverage_by_code": {}, "f10_seed_ledger": [],
        "post_t_safety_by_code": {},
        "market_coverage": {"status": "no_relevant_hit", "query_ids": [], "fact_ids": [],
                            "reason": "无候选，不启动个股网页裁定"},
    }
    zero_doc = {"T": zero_t, "rulings": {}, "codex_audit": zero_audit}
    out.merge(validate_bottom_search(zero_result, zero_doc, required=True))

    stock = copy.deepcopy(_load(DATA / "diag_latest.json"))
    s_date = str(stock.get("as_of"))
    code = str(stock.get("code"))
    fc_date = str(((stock.get("f10") or {}).get("forecast") or {}).get("notice_date") or s_date)
    s_audit = _test_audit("stock-diagnostic", s_date, s_date,
                          [_test_fact(code, fc_date, s_date, f"https://example.com/stock/{code}/{fc_date}", "confirmed")])
    scores = _agent_scores(stock)
    stance = _num((stock.get("engine_verdict") or {}).get("stance"), 0) or 0
    tech_adj = scores["①"] - stance * 0.85
    s_audit["technical_score"] = {"engine_stance": stance, "multiplier": 0.85,
                                    "subjective_adjustment": tech_adj, "reason": "严格路径夹具",
                                    "final": scores["①"]}
    engine_risk = _num(stock.get("risk_score"), 0) or 0
    event_risk = _num((stock.get("f10") or {}).get("risk_bump"), 0) or 0
    extra_risk = scores["⑤"] - engine_risk - event_risk
    s_audit["risk_breakdown"] = {
        "engine_technical_risk": engine_risk,
        "engine_event_risk": event_risk,
        "subjective_items": ([{"delta": extra_risk, "reason": "严格路径夹具风险",
                                "source_url": f"https://example.com/stock/{code}/risk", "published_at": s_date}]
                              if extra_risk else []),
        "final": scores["⑤"],
    }
    pre = scores["①"] * .28 + scores["②"] * .22 + scores["③"] * .22 + scores["④"] * .28 - scores["⑤"] * .15
    s_audit["market_adjustment"] = round(float(stock.get("final_score")) - pre, 3)
    s_audit["hard_gates"] = _stock_objective_hard_gates(stock)
    s_audit["confidence_level"] = _stock_conf_cap(float(stock.get("final_score")),
                                                   [scores[x] for x in ("①", "②", "③", "④")],
                                                   bool(s_audit["hard_gates"]))
    s_audit["scorecards"] = {}
    for dim in ("②", "③", "④"):
        s_audit["scorecards"][dim] = {
            "start": 50,
            "items": [{"delta": scores[dim] - 50, "reason": f"Agent{dim}严格路径夹具",
                       "source_url": f"https://example.com/stock/{code}/{dim}", "published_at": s_date}],
            "final": scores[dim],
        }
    price = float((stock.get("technical") or {}).get("close"))
    s_audit["price_verification_by_code"] = {code: {
        "as_of": s_date,
        "sources": {"源A": {"date": s_date, "price": price}, "源B": {"date": s_date, "price": price}},
        "usable_sources": {"源A": price, "源B": price}, "max_dev_pct": 0.0, "status": "verified",
    }}
    stock["codex_audit"] = s_audit
    out.merge(validate_stock(stock, strict=True))

    weekly = copy.deepcopy(_load(DATA / "rank_latest.json"))
    w_date = str(weekly.get("as_of"))
    retrieved = str(weekly.get("generated_at", w_date))[:10]
    w_facts: list[dict[str, Any]] = []
    for row in weekly.get("candidates") or []:
        code = str(row.get("code"))
        material = False
        for notice in row.get("recent_notices") or []:
            if notice.get("fresh") and MATERIAL_NOTICE_RE.search(str(notice.get("title", ""))):
                material = True
                w_facts.append(_test_fact(code, str(notice.get("date")), retrieved,
                                          str(notice.get("url")), "confirmed"))
        fc = row.get("forecast") or {}
        if fc.get("fresh") and fc.get("notice_date") and not any(
                x["code"] == code and x["published_at"] == str(fc.get("notice_date")) for x in w_facts):
            material = True
            date = str(fc.get("notice_date"))
            w_facts.append(_test_fact(code, date, retrieved, f"https://example.com/weekly/{code}/{date}", "confirmed"))
        if not material:
            w_facts.append(_test_fact(code, w_date, retrieved, f"https://example.com/weekly/{code}"))
        status = str((row.get("verify") or {}).get("status", ""))
        row["risk_note"] = ("✓已验证 " if status.startswith("一致") else "⚠价格未验证 ") + str(row.get("risk_note", ""))
    w_audit = _test_audit("weekly-ashare-rank", retrieved, retrieved, w_facts)
    codes = _code_list(weekly.get("candidates") or [])
    cap = _ic_cap(weekly.get("validation") or {})
    w_audit.update({
        "final_codes": codes,
        "confidence_by_code": {code: cap for code in codes},
        "strategy_warning": "未在退潮段证明" if (weekly.get("validation") or {}).get("val_market_regime") == "上涨段" else "",
        "verified_codes": [str(x.get("code")) for x in weekly.get("candidates") or []
                           if str((x.get("verify") or {}).get("status", "")).startswith("一致")],
    })
    weekly["codex_audit"] = w_audit
    out.merge(validate_weekly(weekly, strict=True))

    weekly_gate = copy.deepcopy(_load(DATA / "market_gate_latest.json"))
    out.merge(validate_weekly_engine(weekly, weekly_gate))
    if weekly.get("candidates"):
        top30_engine = copy.deepcopy(weekly)
        template = top30_engine["candidates"][0]
        top30_engine["candidates"] = []
        for idx in range(30):
            row = copy.deepcopy(template)
            row["code"] = f"600{idx:03d}"
            top30_engine["candidates"].append(row)
        top30_engine["universe_after_filter"] = max(int(top30_engine.get("universe_after_filter", 0) or 0), 30)
        top30_engine["scored"] = max(int(top30_engine.get("scored", 0) or 0), 30)
        out.check(validate_weekly_engine(top30_engine, weekly_gate).passed,
                  "严格正例失效: weekly top30 安全阀被候选数门禁误拒绝")
        top31_engine = copy.deepcopy(top30_engine)
        extra = copy.deepcopy(template)
        extra["code"] = "600030"
        top31_engine["candidates"].append(extra)
        top31_engine["universe_after_filter"] = max(int(top31_engine.get("universe_after_filter", 0) or 0), 31)
        top31_engine["scored"] = max(int(top31_engine.get("scored", 0) or 0), 31)
        top31_result = validate_weekly_engine(top31_engine, weekly_gate)
        out.check(not top31_result.passed and any("安全阀最多30" in error for error in top31_result.errors),
                  "严格负例失效: weekly top31 未被候选数门禁拒绝")

        for blocked_code in ("688001", "689001"):
            bad_engine = copy.deepcopy(weekly)
            bad_engine["candidates"][0]["code"] = blocked_code
            bad_code_result = validate_weekly_engine(bad_engine, weekly_gate)
            out.check(not bad_code_result.passed and any(
                "仅允许 00/30/60" in error and "688/689" in error for error in bad_code_result.errors
            ), f"严格负例失效: weekly 科创板候选 {blocked_code} 未被个人可交易前缀门禁拦截")

        bad_engine = copy.deepcopy(weekly)
        bad_engine["candidates"][0].pop("stop", None)
        missing_stop_result = validate_weekly_engine(bad_engine, weekly_gate)
        out.check(not missing_stop_result.passed and any(
            "缺失已持久化的正值 stop" in error and "旧工件兼容回退" in error
            for error in missing_stop_result.errors
        ), "严格负例失效: weekly 缺失 stop 未被复核兼容回退门禁拦截")

        bad_engine = copy.deepcopy(weekly)
        verify_prices = [
            value for value in (
                _num(x) for x in (bad_engine["candidates"][0].get("verify") or {}).get("sources", {}).values()
            ) if value is not None and value > 0
        ]
        if len(verify_prices) >= 2:
            bad_engine["candidates"][0]["close"] = statistics.median(verify_prices) * 1.006
            stale_close_result = validate_weekly_engine(bad_engine, weekly_gate)
            out.check(not stale_close_result.passed and any(
                "stale-cache" in error and "--refresh" in error for error in stale_close_result.errors
            ), "严格负例失效: weekly 引擎 close 偏离跨源中位共识未被拦截")
        else:
            out.check(False, "严格夹具失效: weekly 缺少至少两路正值验价，无法测试 close 共识门禁")
    else:
        out.check(False, "严格夹具失效: weekly 无候选，无法测试原始引擎硬门禁")

    # 严格负例：每类关键门禁至少注入一次错误且必须被拒绝。
    bad = copy.deepcopy(bottom)
    non_buy = next((row for row in bad.get("candidates") or [] if row.get("judge") != "✓"), None)
    if non_buy is not None:
        non_buy["plan"] = {"buy_low": 1}
        out.check(not validate_bottom(bad, strict=True).passed, "严格负例失效: bottom 非买入票价位未拦截")
    bad = copy.deepcopy(stock)
    bad["codex_audit"]["technical_score"]["multiplier"] = 1.0
    out.check(not validate_stock(bad, strict=True).passed, "严格负例失效: stock ①公式未拦截")
    bad = copy.deepcopy(weekly)
    if codes:
        bad["codex_audit"]["confidence_by_code"][codes[0]] = "高"
        out.check(not validate_weekly(bad, strict=True).passed, "严格负例失效: weekly IC置信上限未拦截")
    bad = copy.deepcopy(weekly)
    bad["codex_audit"]["facts"][0]["source_url"] = ""
    out.check(not validate_weekly(bad, strict=True).passed, "严格负例失效: 缺证据URL未拦截")
    with tempfile.TemporaryDirectory(prefix="codex-trading-audit-") as td:
        sample = pathlib.Path(td) / "sample.html"
        sample.write_text("<html><body><p>Claude WebSearch</p></body></html>", encoding="utf-8")
        out.merge(augment_report(sample, weekly, "weekly-ashare-rank"))
        out.merge(augment_report(sample, weekly, "weekly-ashare-rank"))
        out.merge(brand_report(sample))
        rendered = sample.read_text(encoding="utf-8")
        out.check(rendered.count(AUDIT_START) == 1 and rendered.count(AUDIT_END) == 1,
                  "HTML 审计附录重复生成不幂等")
        out.check("Claude" not in rendered and "WebSearch" not in rendered, "HTML 品牌归一自测失败")
        out.check(w_facts[0]["source_url"] in rendered, "HTML 审计附录缺证据 URL")

        search_sample = pathlib.Path(td) / "bottom_cn_2026-01-12_10-00-00_裁定版.html"
        search_sample.write_text("<html><body><p>600000</p><p>非投资建议</p></body></html>", encoding="utf-8")
        search_final = copy.deepcopy(search_result)
        search_final["codex_audit"] = copy.deepcopy(search_audit_doc["codex_audit"])
        out.merge(augment_report(search_sample, search_final, "bottom-fishing"))
        out.merge(validate_html("bottom-fishing", search_final, search_sample, strict=True,
                                require_bottom_search=True))
        search_rendered = search_sample.read_text(encoding="utf-8")
        out.check("https://example.com/perf?x=1&amp;y=2" in search_rendered,
                  "HTML URL 的 & 未正确转义")
        out.check("600000:notices:1" in _plain_html(search_rendered), "HTML 缺 F10 逐条 ledger")

        brand_sample = pathlib.Path(td) / "brand-url.html"
        brand_url = "https://example.com/Claude?tool=WebSearch&x=1"
        brand_sample.write_text(
            f"<html><body><a href='{brand_url}'>Claude WebSearch</a></body></html>", encoding="utf-8")
        out.merge(brand_report(brand_sample))
        branded_raw = brand_sample.read_text(encoding="utf-8")
        out.check(brand_url in _html_link_targets(branded_raw), "brand-report 破坏了 href URL")
        out.check("Claude" not in _plain_html(branded_raw) and "WebSearch" not in _plain_html(branded_raw),
                  "brand-report 未清理可见品牌文案")
    return out


def _load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rerender_test() -> Result:
    """用当前 schema JSON 在临时目录重渲染三种 HTML，并证明生产 JSON/核心引擎零写入。"""
    out = Result("rerender-test")
    out.merge(baseline_check())
    inputs = {
        "bottom-fishing": DATA / "bottom_latest.json",
        "stock-diagnostic": DATA / "diag_latest.json",
        "weekly-ashare-rank": DATA / "rank_latest.json",
    }
    before = {name: _sha256(path) for name, path in inputs.items()}
    with tempfile.TemporaryDirectory(prefix="codex-trading-rerender-") as td:
        temp = pathlib.Path(td)

        bottom = _load(inputs["bottom-fishing"])
        bottom_mod = _load_module(SKILLS_SOURCE / "bottom-fishing" / "bottom_fishing.py", "codex_test_bottom")
        bottom_mod.REPORTS = temp / "bottom"
        bottom_html = pathlib.Path(bottom_mod.render_html(copy.deepcopy(bottom)))
        out.merge(augment_report(bottom_html, bottom, "bottom-fishing"))
        out.merge(validate_html("bottom-fishing", bottom, bottom_html, strict=False))

        stock = _load(inputs["stock-diagnostic"])
        stock_mod = _load_module(SKILLS_SOURCE / "stock-diagnostic" / "stock_diagnostic.py", "codex_test_stock")
        stock_html = pathlib.Path(stock_mod.render_html(copy.deepcopy(stock), str(temp / "stock"), is_final=True))
        out.merge(validate_html("stock-diagnostic", stock, stock_html, strict=False))

        weekly = _load(inputs["weekly-ashare-rank"])
        weekly_mod = _load_module(SKILLS_SOURCE / "weekly-ashare-rank" / "ashare_weekly_rank.py", "codex_test_weekly")
        weekly_html = pathlib.Path(weekly_mod.render_html(copy.deepcopy(weekly), str(temp / "weekly")))
        out.merge(validate_html("weekly-ashare-rank", weekly, weekly_html, strict=False))

        out.check(bottom_html.parent == temp / "bottom", "bottom 重渲染逃逸临时目录")
        out.check(stock_html.parent == temp / "stock", "stock 重渲染逃逸临时目录")
        out.check(weekly_html.parent == temp / "weekly", "weekly 重渲染逃逸临时目录")

    for name, path in inputs.items():
        out.check(_sha256(path) == before[name], f"{name} 重渲染修改了生产 JSON")
    out.merge(baseline_check())
    return out


def install_check(skills_root: pathlib.Path) -> Result:
    out = Result("install")
    for name in ("bottom-fishing", "stock-diagnostic", "weekly-ashare-rank"):
        path = skills_root / name
        out.check(path.is_dir(), f"skill 未安装: {path}")
        out.check((path / "SKILL.md").is_file(), f"缺 SKILL.md: {name}")
        out.check((path / "agents" / "openai.yaml").is_file(), f"缺 agents/openai.yaml: {name}")
        if (path / "SKILL.md").is_file():
            text = (path / "SKILL.md").read_text(encoding="utf-8-sig")
            out.check(text.startswith("---\n") or text.startswith("---\r\n"), f"{name} 缺 YAML frontmatter")
            out.check("name: " + name in text[:2000], f"{name} frontmatter name 不符")
    return out


def _print_result(result: Result) -> int:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}: checks={result.checks} errors={len(result.errors)} warnings={len(result.warnings)}")
    for item in result.errors:
        print(f"  ERROR: {item}")
    for item in result.warnings:
        print(f"  WARN: {item}")
    return 0 if result.passed else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex A股交易 skill 独立验收器（0 API）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline", help="核对不可变引擎哈希")
    sub.add_parser("fixtures", help="读取全部历史工件并跑回归/负例")
    sub.add_parser("self-test", help="严格通过路径与关键失败注入（纯内存，不改生产结果）")
    sub.add_parser("rerender-test", help="三种当前 JSON 临时重渲染，证明生产结果零写入")
    gate = sub.add_parser("validate-gate", help="重算 Agent⓪ 市场闸门 JSON")
    gate.add_argument("--json", required=True)
    stock_engine = sub.add_parser("validate-stock-engine", help="校验 Agent① 原始 stock JSON（非最终报告）")
    stock_engine.add_argument("--json", required=True)
    weekly_engine = sub.add_parser("validate-weekly-engine", help="校验全市场原始 weekly JSON（非最终裁定）")
    weekly_engine.add_argument("--json", required=True)
    weekly_engine.add_argument("--gate", required=True)
    bottom_engine = sub.add_parser("validate-bottom-engine", help="校验原始 bottom JSON/HTML（非最终裁定）")
    bottom_engine.add_argument("--json", required=True)
    bottom_engine.add_argument("--html")
    bottom_search = sub.add_parser("validate-bottom-search", help="裁定写入前校验抄底搜索审计与时点隔离")
    bottom_search.add_argument("--result", required=True)
    bottom_search.add_argument("--audit", required=True)
    install = sub.add_parser("install-check", help="核对 .agents/skills 安装")
    install.add_argument("--skills-root", default=r"C:\Trading_analysis\.agents\skills")
    brand = sub.add_parser("brand-report", help="把最终 HTML 中的遗留品牌文案改为 Codex")
    brand.add_argument("--html", required=True)
    augment = sub.add_parser("augment-report", help="在原 HTML 末尾追加结构化 Codex 审计附录")
    augment.add_argument("--skill", required=True,
                         choices=("bottom-fishing", "stock-diagnostic", "weekly-ashare-rank"))
    augment.add_argument("--json", required=True)
    augment.add_argument("--html", required=True)
    attach = sub.add_parser("attach-audit", help="把独立裁定文件的 codex_audit 附加到结果 JSON")
    attach.add_argument("--result", required=True)
    attach.add_argument("--audit", required=True)
    val = sub.add_parser("validate", help="校验最终 JSON，可同时核对 HTML")
    val.add_argument("--skill", required=True, choices=("bottom-fishing", "stock-diagnostic", "weekly-ashare-rank"))
    val.add_argument("--json", required=True)
    val.add_argument("--html")
    val.add_argument("--require-bottom-search", action="store_true",
                     help="bottom-fishing 最终发布必须含通过验收的 bottom_search")
    args = parser.parse_args()

    if args.cmd == "baseline":
        result = baseline_check()
    elif args.cmd == "fixtures":
        result = fixture_check()
    elif args.cmd == "self-test":
        result = strict_self_test()
    elif args.cmd == "rerender-test":
        result = rerender_test()
    elif args.cmd == "validate-gate":
        result = validate_market_gate_artifact(_load(pathlib.Path(args.json)))
    elif args.cmd == "validate-stock-engine":
        result = validate_stock_engine(_load(pathlib.Path(args.json)))
    elif args.cmd == "validate-weekly-engine":
        result = validate_weekly_engine(_load(pathlib.Path(args.json)), _load(pathlib.Path(args.gate)))
    elif args.cmd == "validate-bottom-engine":
        bottom_obj = _load(pathlib.Path(args.json))
        result = validate_bottom(bottom_obj, strict=False)
        if args.html:
            result.merge(validate_html("bottom-fishing", bottom_obj, pathlib.Path(args.html), strict=False))
        result.merge(baseline_check())
    elif args.cmd == "validate-bottom-search":
        bottom_obj = _load(pathlib.Path(args.result))
        audit_obj = _load(pathlib.Path(args.audit))
        result = validate_bottom_search(bottom_obj, audit_obj, required=True)
        combined = copy.deepcopy(bottom_obj)
        combined["codex_audit"] = audit_obj.get("codex_audit")
        result.merge(audit_common(combined, "bottom-fishing", []))
        result.merge(validate_bottom(bottom_obj, strict=False))
        result.merge(baseline_check())
    elif args.cmd == "install-check":
        result = install_check(pathlib.Path(args.skills_root))
    elif args.cmd == "brand-report":
        result = brand_report(pathlib.Path(args.html))
    elif args.cmd == "augment-report":
        result = augment_report(pathlib.Path(args.html), _load(pathlib.Path(args.json)), args.skill)
    elif args.cmd == "attach-audit":
        result = attach_audit(pathlib.Path(args.result), pathlib.Path(args.audit))
    else:
        obj = _load(pathlib.Path(args.json))
        strict = True
        if args.skill == "bottom-fishing":
            result = validate_bottom(obj, strict, require_search=args.require_bottom_search)
        elif args.skill == "stock-diagnostic":
            result = validate_stock(obj, strict)
        else:
            result = validate_weekly(obj, strict)
        result.merge(baseline_check())
        if args.html:
            result.merge(validate_html(args.skill, obj, pathlib.Path(args.html), strict,
                                       require_bottom_search=(args.skill == "bottom-fishing" and
                                                              args.require_bottom_search)))
    raise SystemExit(_print_result(result))


if __name__ == "__main__":
    main()
