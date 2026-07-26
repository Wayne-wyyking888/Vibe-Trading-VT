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
      "published_time_precision": "date|datetime（bottom search 可选）",
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
bottom-fishing 启用 `bottom_search` 后按其 F10 ledger 执行：T 前实质种子仍必须关联 fact；例行种子可只写
`logged_routine_pre_t`，T 后/无日期种子必须隔离且不得造主事实。

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

旧版、未启用 bottom search 的工件仍要求 `facts[].code` 覆盖每个过线票；`rulings` 写在
`bottom_adjudication.json` 原位置，verdict 只能是 `✓/?/✗`。
新生成的 bottom-fishing 裁定必须在 `codex_audit.bottom_search` 写入
`bottom-search-audit/v1` 搜索审计；完整的检索、时间和来源规则见
`claude_code_Trading_skill_no_API_Doc/bottom-fishing/references/WEB_EVIDENCE_PROTOCOL.md`。启用该审计时，
`facts[]` 还必须有唯一 `fact_id`、六维之一的
`category`（市场事实可用 `market_regime`）、可回指 `sources[]` 的 `source_ref`，以及逐条 F10 回指 `seed_refs[]`。
这四项是启用 bottom search 后的增量字段，不要求 stock-diagnostic 或 weekly-ashare-rank 添加。
启用 bottom search 后，候选级“已查全”由六维 coverage/query 图证明；若某 `?` 票所有维度均为
`no_relevant_hit/blocked`，允许该票没有 fact，严禁为满足旧的 facts 覆盖规则虚构“无证据事实”。

同时必须写入 Agent③ 的
`codex_audit.toxic_risk_warning`，版本固定为 `bottom-toxic-risk-warning/v2`。完整五域覆盖、
截至实际运行时点的最新搜索、T/T后隔离、逐项证据约束推断、来源门槛、候选暴露和 HTML alert 联动见
`claude_code_Trading_skill_no_API_Doc/bottom-fishing/references/TOXIC_RISK_WARNING_PROTOCOL.md`。
该对象固定 `mode=shadow`，只增加风险提示，不改变分数、推荐线、✓/?/✗、仓位或预算熔断。
每条 warning/delta 和 `runtime_evaluation` 的五个域都必须写事实、共识、基准情景与置信度、
 上下行情景、传导链、观察变量、失效条件和推断边界；不能只写“方向不确定”。
五域完成后还必须写 `ashare_runtime_outlook`：逐域解释如何反映到A股，并明确下一交易日、未来1—5日、
指数/风格、相对受益与承压板块、开盘触发和一句白话走势结论。该综合只允许定性置信度，禁止未经校准的
涨跌概率、涨跌幅或指数目标点位；HTML白话卡片必须位于顶部“市况”正下方。

```json
{
  "fact_id": "fact-600000-001",
  "category": "performance_operations",
  "source_ref": "source-001",
  "seed_refs": ["600000:forecast"]
}
```

六个必查维度的固定枚举为：

```text
performance_operations
financial_credit
governance_regulatory
corporate_events
industry_policy_domestic
external_global_peer
```

审计骨架如下。`coverage_by_code` 和 `post_t_safety_by_code` 必须与引擎候选代码一一对应；六维不得缺项。

```json
{
  "bottom_search": {
    "version": "bottom-search-audit/v1",
    "T": "YYYY-MM-DD",
    "cutoff_beijing": "YYYY-MM-DD 23:59:59+08:00",
    "retrieved_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
    "required_categories": [
      "performance_operations",
      "financial_credit",
      "governance_regulatory",
      "corporate_events",
      "industry_policy_domestic",
      "external_global_peer"
    ],
    "sources": [
      {
        "source_ref": "source-001",
        "access_url": "https://...",
        "access_publisher": "访问页发布者",
        "source_kind": "official_direct|verified_official_mirror|independent_media|independent_research|unverified_secondary",
        "origin_id": "同一原始文件的稳定去重键",
        "canonical_url": "https://...",
        "canonical_publisher": "原始发布者",
        "document_id": "官方文档号或公告编号",
        "match_basis": ["title", "published_at", "document_id", "full_text"]
      }
    ],
    "queries": [
      {
        "query_id": "query-600000-001",
        "code": "600000",
        "category": "performance_operations",
        "phase": "as_of_t|post_t_safety",
        "query_mode": "official|exact_title|broad_web|regulator|industry|overseas|ah_cross_listing|drop_cause|freshness_delta",
        "query_text": "实际执行的查询",
        "date_from": "YYYY-MM-DD",
        "date_to": "YYYY-MM-DD",
        "executed_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "outcome": "selected|no_relevant_hit|blocked",
        "reviewed_urls": ["https://..."],
        "selected_fact_ids": ["fact-600000-001"],
        "selected_delta_ids": [],
        "notes": "无命中/受阻时必须解释"
      }
    ],
    "coverage_by_code": {
      "600000": {
        "aliases": ["公司全称", "证券简称"],
        "profile_tags": ["A/H", "出口", "周期"],
        "categories": {
          "performance_operations": {
            "status": "hit|no_relevant_hit|blocked",
            "query_ids": ["query-600000-001"],
            "fact_ids": ["fact-600000-001"],
            "reason": "覆盖结论"
          }
        },
        "official_latest_check": {
          "query_ids": ["query-600000-official"],
          "checked_sources": ["上交所", "公司IR"],
          "latest_pre_t": "YYYY-MM-DD|null（全部受阻时）"
        },
        "ruling_evidence": {
          "supporting_fact_ids": [],
          "adverse_fact_ids": [],
          "decision_fact_ids": [],
          "unresolved_query_ids": []
        }
      }
    },
    "market_coverage": {
      "status": "hit|no_relevant_hit|blocked",
      "query_ids": ["query-MARKET-001"],
      "fact_ids": ["fact-MARKET-001"],
      "reason": "市场或行业踩踏核查"
    },
    "f10_seed_ledger": [
      {
        "seed_key": "600000:notices:0",
        "code": "600000",
        "kind": "notice",
        "raw_index": 0,
        "seed_text": "引擎原始种子全文",
        "raw_date": "YYYY-MM-DD|null",
        "timing": "pre_t|post_t|undated",
        "disposition": "adjudicated_pre_t|logged_routine_pre_t|quarantined_post_t|quarantined_undated",
        "query_ids": [],
        "fact_ids": [],
        "delta_ids": [],
        "reason": "采用、例行或隔离理由"
      }
    ],
    "post_t_safety_by_code": {
      "600000": {
        "checked_through_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "base_verdict_asof_t": "✓|?|✗",
        "effective_verdict": "✓|?|✗",
        "items": [
          {
            "delta_id": "delta-600000-001",
            "source_ref": "source-002",
            "published_at": "YYYY-MM-DD",
            "event_date": "YYYY-MM-DD|null",
            "query_ids": ["query-600000-delta"],
            "summary": "T后安全增量",
            "polarity": "positive|neutral|negative|unverified",
            "effect": "none|cap_at_question|cap_at_reject",
            "used_in_asof_t_verdict": false,
            "uncertainties": []
          }
        ]
      }
    }
  }
}
```

同一公告的转载必须共享 `origin_id`；镜像只有在至少两项 `match_basis` 匹配且回溯到官方 `canonical_url` 后，才可标
`verified_official_mirror`，且必须填写非空 `document_id`。`facts[].source_url` 必须等于所引 `source_ref` 的
`access_url`；`official_direct` 的 `canonical_url` 必须等于 `access_url`。主事实只能使用 `published_at <= T`；
T 后信息只进入 `post_t_safety_by_code.items[]`，利好不得
把有效裁定向上调，负面只能维持或降级；`effective_verdict` 必须等于 base 与全部 cap 共同形成的最低上限。
`rulings[code].verdict` 必须等于 `effective_verdict`；
`base_verdict_asof_t` 只保留 T 日裁定，不直接交给 adjudicate。市场查询固定使用 `code=MARKET`、
`category=market_regime`。

`ruling_evidence.decision_fact_ids` 必须来自 supporting/adverse 的并集；`✓` 至少有一个 supporting 决定事实，
`✗` 至少有一个 adverse 决定事实，不能用无关的官方事实替二手负面“洗白”来源层级。

F10 seed key 只有两种：forecast 固定为 `{code}:forecast`，`raw_index=null`，`seed_text` 按
`notice_date + 空格 + type + 空格 + content` 确定性拼接；公告为 `{code}:notices:{raw_index}`，逐字保留原始
`notices[raw_index]`。只有公告按数组索引；不能用同日一条事实批量覆盖多条公告。实质/例行的机械判定词表以
`WEB_EVIDENCE_PROTOCOL.md` §6 为准，由验收器从原始种子重算，不能人工声明。

裁定写入前先运行：

```powershell
python C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py validate-bottom-search --result C:\Trading_analysis\data\bottom_latest.json --audit C:\Trading_analysis\data\bottom_adjudication.json
```

最终验收还必须给 `validate` 增加 `--require-bottom-search`；任一搜索门禁失败都不得发布为最终报告。
若引擎 0 只过线票，`bottom_search` 仍须保留版本、T、两个时点和固定分类，并以非空 `empty_reason` 说明空手；
`sources=[]`、`queries=[]`、`coverage_by_code={}`、`f10_seed_ledger=[]`、`post_t_safety_by_code={}`，
`market_coverage={"status":"no_relevant_hit","query_ids":[],"fact_ids":[],"reason":"非空的空手说明"}`。
不得虚构候选、查询或事实凑 schema。Agent③不因零候选而跳过：`toxic_risk_warning.by_code={}`，
但仍须完成五个市场风险域并把全局 shadow warning 写入顶层 `alerts`；若确无命中则
`overall_status=clear`、`warnings=[]` 且 `clear_reason` 非空。即便没有命中，
`runtime_evaluation` 仍须精确覆盖五域，说明截至实际检索时点的基准判断、观察变量和失效条件；
`ashare_runtime_outlook` 仍须给出“无明确单边风险驱动”条件下的A股基准路径，不能省略。

Agent③ alert 最小格式：

```json
{
  "warning_id": "toxic-warning-001",
  "level": "med|high",
  "text": "必须明确写 shadow/影子；T后条目还必须写“T后”",
  "shadow": true,
  "post_t": false
}
```

每条 T 日 warning/T 后 delta 都必须以同一 ID 进入顶层 `alerts`；映射到候选的还必须进入
`rulings[code].alerts`。`by_code.exposure` 按关联项最高等级机械重算为 `none|watch|high`。
