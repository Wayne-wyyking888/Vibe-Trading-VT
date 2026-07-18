# Codex 人工裁定审计契约

最终 JSON 顶层必须包含 `codex_audit`。该对象是增量审计字段，不替换原有 schema。

```json
{
  "version": "codex-trading-audit/v1",
  "skill": "bottom-fishing|stock-diagnostic|weekly-ashare-rank",
  "evidence_cutoff_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
  "retrieved_on_beijing": "YYYY-MM-DD",
  "facts": [
    {
      "code": "600000",
      "fact": "可核查事实；查无证据时明确写无证据",
      "source_name": "来源名称",
      "source_url": "https://...",
      "published_at": "YYYY-MM-DD",
      "retrieved_at_beijing": "YYYY-MM-DD",
      "event_date": "YYYY-MM-DD|null",
      "source_tier": "公告|交易所|主流媒体|研报|其他",
      "f10_match": "confirmed|conflict|missing|not_applicable",
      "reasoning": "事实如何映射到原 rubric",
      "rubric_delta": 0,
      "counter_argument": "最强反方解释",
      "conclusion": "采用/证伪/未确定/无证据",
      "uncertainties": []
    }
  ],
  "contrarian_challenge": {
    "completed": true,
    "arguments": ["至少一个最强反方观点"],
    "responses": ["逐项回应或据此改分/降级"]
  },
  "auditor_review": {
    "passed": true,
    "checks": [],
    "failed": [],
    "notes": ""
  },
  "price_verification_by_code": {
    "600000": {
      "as_of": "YYYY-MM-DD",
      "sources": {
        "东方财富": {"date": "YYYY-MM-DD", "price": 10.01},
        "腾讯": {"date": "YYYY-MM-DD", "price": 10.02}
      },
      "usable_sources": {"东方财富": 10.01, "腾讯": 10.02},
      "max_dev_pct": 0.1,
      "status": "verified|single|conflict|stale_or_missing"
    }
  }
}
```

`price_verification_by_code` 可直接复制
`python C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\verify_prices.py ...` 的同名对象。
stock-diagnostic 必须覆盖标的；bottom-fishing 必须覆盖每个 `✓` 票。只有同一 T 日至少两个独立源且
最大偏差≤1% 才能写 `verified`；单源、日期不符或偏差大不得标“已验证”。weekly-ashare-rank 由原引擎
`candidates[].verify` 提供同等结构化证据。

每条 `facts[]` 还必须满足：`source_name` 非空，`source_tier` 属于模板枚举，`f10_match` 属于模板枚举，
`rubric_delta` 是数值，发布日期不得晚于证据截止日，检索日期必须与顶层北京时间检索日期一致。
F10 中的实质性新公告/预告必须逐条在 facts 留痕；即使判定为例行公告或不采用，也要写出理由，不能静默漏查。

## stock-diagnostic 增量字段

`scorecards` 必须至少包含 ②③④。每张卡从 50 分起，`items[].delta` 之和必须等于最终分，每项必须带 URL 与日期。

```json
{
  "market_adjustment": -3,
  "hard_gates": [],
  "confidence_level": "中",
  "technical_score": {
    "engine_stance": 50,
    "multiplier": 0.85,
    "subjective_adjustment": 0,
    "reason": "校正理由",
    "final": 42.5
  },
  "risk_breakdown": {
    "engine_technical_risk": 15,
    "engine_event_risk": 8,
    "subjective_items": [
      {"delta": 5, "reason": "风险事实", "source_url": "https://...", "published_at": "YYYY-MM-DD"}
    ],
    "final": 28
  },
  "scorecards": {
    "②": {"start": 50, "items": [], "final": 50},
    "③": {"start": 50, "items": [], "final": 50},
    "④": {"start": 50, "items": [], "final": 50}
  }
}
```

①必须等于 `engine_stance×0.85 + subjective_adjustment`，主观校正在 −10~+10；⑤必须等于
引擎技术风险 + F10 事件风险 + 主观风险逐项（上限 100）。②③④仍全部从 50 分起逐项相加。

## weekly-ashare-rank 增量字段

`final_codes` 是文字报告的最终代码顺序，必须与 `candidates[]` 物理顺序完全相同。`confidence_by_code` 供 IC 上限机械审计。

```json
{
  "final_codes": ["600000"],
  "confidence_by_code": {"600000": "中"},
  "strategy_warning": "",
  "verified_codes": ["600000"]
}
```

`verified_codes` 只能包含原引擎 `verify.status=一致(...)`、至少两个价格源且重算偏差≤1%的代码。

## bottom-fishing 增量字段

`facts[].code` 必须覆盖每个过线票；`rulings` 仍写在 `bottom_adjudication.json` 原位置，verdict 只能是 `✓/?/✗`。
