# Agent③ 毒月 Web 风险预警协议（bottom-toxic-risk-warning/v3）

本协议把毒月研究接入日常扫描，但只产生 `shadow warning`：不修改量化分数、推荐线、排序、仓位、
✓/?/✗ 裁定或预算熔断。Agent③同时维护两本账：截至 T 的无前视风险 nowcast，以及截至本次实际检索
完成时点的最新五域评估。后一本账必须纳入 T 后公开信息并给出证据约束下的推断，但不得倒灌 T 日裁定。
不承诺预测尚未公开的黑天鹅或“下一个毒月”。

运行 v3 时还必须完整阅读同目录 `AGENT3_SECTOR_MAPPING_PROTOCOL.md`。本文件负责五域风险、T/T后隔离和
warning；该文件负责外盘/宏观预测输入、分窗口A股板块调用与候选股票双向下沉。两份协议共同构成发布契约。

## 目录

1. 角色边界
2. 五个固定风险域
3. T 日与 T 后隔离
4. 完整检索、重大排期事件预期与证据约束推断
5. 五域合并后的A股走势映射
6. warning 等级和来源门槛
7. 候选暴露映射与 HTML
8. 结构化 JSON 契约
9. 发布门禁

## 1. 角色边界

Agent③ 在每次 bottom 扫描中独立执行市场级 Web 检索：

- 识别未来已排期的波动窗口；
- 识别 T 日已公开且仍在演化的系统性/行业压力；
- 将市场风险映射到有明确暴露的过线候选；
- 从 T+1 搜索到实际运行时点，把新事实隔离为执行时点安全增量；
- 对每条 warning、每条 T 后 delta 和五个风险域分别输出事实、共识、基准情景、上下行情景、
  传导链、观察变量、失效条件、置信度和推断边界；
- 合并五域，明确输出这些信息反映到A股下一交易日及未来1—5个交易日的大概路径；
- 没有命中时保存完整 coverage 和 `clear_reason`。

Agent②继续负责公司基本面、财务信用、治理和资本事件裁定。Agent③不得以泛化市场叙事否决个股，
也不得把公司红旗重复包装成“毒月预警”。两者可引用同一官方原文，但必须各自满足结构化契约。

## 2. 五个固定风险域

`required_domains` 和 `coverage` 必须精确覆盖：

| domain | 必查内容 | 优先来源 |
|---|---|---|
| `scheduled_macro_policy` | 国家统计局数据、LPR、FOMC、已公布政策生效日 | 统计局、央行、美联储、政府公告 |
| `domestic_regulatory_liquidity` | ST/退市规则、异常交易、融资收缩、小微盘踩踏、市场流动性 | 证监会、交易所、官方市场数据 |
| `external_geopolitics_trade` | 战争、航运阻断、关税、制裁、出口管制 | 政府/监管原文、事件主体、独立媒体 |
| `cross_asset_stress` | 油价、汇率、利率、黄金、全球股市等跨资产强制去杠杆 | 央行、交易所、官方数据、独立媒体 |
| `holiday_information_gap` | A股长假闭市期间海外仍交易、已知信息累积窗口 | 上交所休市日历及海外交易日历 |

每个风险域至少保留一条 as-of-T 查询。写 `no_relevant_hit` 时必须有两种不同查询文本；
`blocked` 与未命中分开，不能因来源打不开就写 clear。五域是最低覆盖，不是事件白名单：FOMC、PMI
只是 `scheduled_macro_policy` 的例子。实际运行必须继续搜索每个域当时最新的重大变化，不能只复述上次
报告已经出现的事件。

## 3. T 日与 T 后隔离

- `cutoff_beijing` 固定为 `T 23:59:59+08:00`。
- `warnings[]` 只能引用 `phase=as_of_t` 且 `published_at<=T` 的来源。
- 已排期事件允许 `event_start>T`，但必须是 `scheduled + med + direction_certainty=uncertain`。
- 活跃事件必须 `event_start<=T`；已经结束的事件不得冒充当前风险。
- 若实际检索日晚于 T，五个风险域都必须执行 `post_t_safety` 末端扫描；每域至少保留两种不同
  查询文本，日期窗从 T+1 连续覆盖至实际检索日，防止用单轮泛搜冒充“最新搜全”。
- T 后事件只进入 `post_t_safety_items[]`，并固定
  `used_in_asof_t_warning=false`；不得回填到 T 日 warning。
- `runtime_evaluation` 必须综合本域全部 as-of-T 与 post-T 查询，评估时点等于
  `retrieved_at_beijing`；T 后阶段的查询日期窗必须一直覆盖到该检索日。

战争突然爆发、临时制裁名单、未公开财务造假等属于不可预知事件。只能在公开后作为 T 后安全增量，
不能以事后新闻声称 Agent③ 已事前发现。

## 4. 完整检索与证据约束推断

Agent③不能用“方向不确定”“待观察”结束分析。每条 `warnings[]`、每条
`post_t_safety_items[]`，以及 `runtime_evaluation` 的每个风险域，都必须填写统一的
`evaluation`：

- `fact_basis`：已核验事实，只写来源真正支持的内容；
- `consensus` + `consensus_source_refs`：当前最普遍的公开预期及其来源；找不到可靠共识时明确写
  “未找到可靠共识”，不能自造多数意见；
- `base_case` + `confidence=low|medium|high`：基于现有证据最可能的路径和主观置信度；
- `upside_scenario` / `downside_scenario`：条件式的缓和与恶化情景，不是无条件涨跌口号；
- `transmission_paths`：事件如何传至风险偏好、利率/汇率/商品、行业收入成本和候选暴露；
- `watch_variables`：下一次刷新时必须核对的官方决定、数据、价格或市场定价；
- `invalidators`：哪些新事实出现后当前基准情景失效；
- `inference_boundary`：明确为 shadow、不改分数/裁定/仓位，并说明运行时点信息不倒灌 T 日裁定。

所有文字必须区分“事实—共识—推断”。出现概率、百分比、基点等精确预期时必须有
`consensus_source_refs` 支撑；没有市场定价、调查或可靠机构共识来源时，只能给定性置信度，不能编造精确概率。
只用于共识或运行时点综合、未触发 warning/delta 的来源也可进入 `sources[]`，但必须被本域
`runtime_evaluation.source_refs` 引用，并出现在本域同阶段查询的 `reviewed_urls` 中。
`no_relevant_hit` 也必须写运行时点基准判断，例如“截至检索时点未发现重大新增压力，但不等于风险不存在”，
并列出观察变量和失效条件。

### 4.1 重大排期事件预期台账

Agent③必须主动检索 T+1 起未来10个自然日的重大统计、央行、政策和长假排期。`warnings[]` 中每一条
`status=scheduled` 事件都必须一对一进入 `scheduled_event_expectations[]`；不得有漏项或孤儿记录。

- `official_source_refs` 只证明事件名称和排期，必须至少有一个官方直达或经核官方镜像；
- `consensus_source_refs` 证明一致预期，只能使用调查、市场定价或可靠机构/媒体预览，并与对应
  `warning.evaluation.consensus_source_refs` 精确一致；
- 每个非 `not_applicable` 事件必须以 `expectation_search_coverage` 精确覆盖三条互补检索路径：
  `survey_consensus`（调查/市场定价）、`economic_calendar`（经济日历）和
  `institution_preview`（可靠机构或媒体预览）。每路都记录 `hit|no_relevant_hit|blocked`、非空且互不挪用的
  `query_ids`、本路实际采用的 `source_refs` 与理由；查询必须回指该 warning 的实际 as-of-T 查询。`hit` 的来源 URL
  必须进入本路 reviewed URL，非 `hit` 不得夹带来源；三路来源并集必须等于事件预期来源。`blocked` 必须换源重试，
  不能据此声称无共识；
- `consensus_query_ids` 必须等于三路检索 `query_ids` 的去重并集；采用来源 URL 必须出现在这些查询的
  `reviewed_urls`。写 `no_reliable_consensus` 时三路均须完成、均不得 blocked，每路至少审阅一个 URL，且总计至少
  三种不同查询文本、两个不同来源域名；仅做泛搜两次不再合格；
- `scheduled_for` 优先写完整 `+08:00` 北京时间；官方只给日期或窗口时必须分别标
  `time_precision=date|window`，不能伪造分钟；
- `consensus_status` 分为：`available`（完整调查/市场一致预期）、`forecast_available`（无调查共识但有完整可靠
  机构预测）、`mixed_available`（四项完整但调查/市场定价与机构预测混合）、`partial_available`（只找到部分可靠数值）、
  `qualitative_only`（仅有可靠定性预览）、
  `no_reliable_consensus` 和 `not_applicable`。不得把机构预测冒充调查共识，也不得因没有完整调查共识而丢弃
  已找到的数值或定性预览；
- 每个数值指标必须同时写 `consensus`、`previous`、`unit`、`metric_definition`、
  `estimate_kind=survey_consensus|market_pricing|institution_forecast` 和指标级来源；`consensus` 字段保留为兼容字段，
  展示时必须按 `estimate_kind` 标成“一致预期”或“机构预测”；指标来源还必须落在对应检索路径：机构预测只能来自
  `institution_preview`，调查/市场定价来自 `survey_consensus` 或有实际采用来源的 `economic_calendar`；
- CPI 固定四项：`cpi_headline_mom/cpi_headline_yoy/cpi_core_mom/cpi_core_yoy`；PPI 固定四项：
  `ppi_final_demand_mom/ppi_final_demand_yoy/ppi_core_mom/ppi_core_yoy`。PPI 的 `metric_definition` 必须明确最终需求及
  核心项实际剔除范围，禁止复制核心 CPI 口径；就业报告固定
  `payroll_change/unemployment_rate/wage_growth_mom`；LPR 固定 `lpr_1y/lpr_5y`；
- 固定指标事件无论是否找到数值，都必须让 `required_metric_ids` 保留完整固定集合，并以
  `metric_search_ledger` 一对一记录每项 `available|no_reliable_estimate`、查询、来源和理由；可用指标必须与
  `metrics[]` 精确对应，缺失指标必须明确记录完成过的搜索，不得用空数组抹掉检查责任；
- 三路检索和指标逐项搜索仍无结果时，才写 `no_reliable_consensus`；`qualitative_only` 必须填写
  `qualitative_expectation` 和来源。不得让用户提醒、补数或把“待补”带入报告；
- 每条记录还必须有可读摘要与 `watch_after_release`，并在 HTML 顶部展示排期、预期/前值、基准/上下行情景和来源。

### 4.2 排期事件发布后对账

`scheduled_event_expectations[]` 保存截至 T 的事前预期，不得被 T 后实际结果覆盖；但每条事件必须以同一
`event_id` 一对一进入 `scheduled_event_reconciliations[]`，反映截至 `retrieved_at_beijing` 的最新状态：

- 未到官方排期时点写 `pending`，不得夹带实际值、T 后来源或板块调用；
- 已到精确排期时点后禁止继续写 `pending`，必须登记 `released|delayed|cancelled|blocked`；
- `released` 必须保存 `actual_released_at_beijing`、官方实际来源、实际摘要、相对事前预期、关联
  `delta_ids/signal_ids/query_ids`、`sector_call_ids` 和 shadow/T 后隔离边界；固定指标事件的
  `actual_metrics` 必须逐项覆盖 `required_metric_ids`；
- `delayed|cancelled` 必须由 T 后 delta 和可核来源支撑；`blocked` 必须保留实际受阻查询，不得伪装成未命中；
- 顶部同一事件卡必须并列显示“事前预期”和“运行时实际/状态/A股映射”，不能只在底部附录另列一条 delta。

## 5. 五域合并后的A股走势映射

完成五个 `runtime_evaluation` 和八类 `predictive_input_coverage` 后，必须再写一个顶层
`ashare_runtime_outlook`。它不是重复宏观事件预测，
而是回答：“这些已知信息综合反映到A股，大概是什么走势和结构？”

- `domain_impacts` 必须精确覆盖五域；每域写 `positive|neutral|negative|mixed`，并用 `mechanism` 直说如何传到A股；
  同时写 `sector_disposition=linked|neutral|not_applicable`、`sector_call_ids` 与 `sector_reason`，把本域全部 warning、
  delta、排期预期和运行时信息的综合结果明确承接到板块 call，或说明为何不形成相对板块方向。不能停在
  “全球风险资产可能波动”。
- 每域另以 `considered_warning_ids/considered_delta_ids` 精确承接本域运行时点评估中的全部 warning 与 T 后 delta；
  `ashare_runtime_outlook.risk_item_disposition_ledger` 必须与两类风险项一对一，逐条记录所属域、
  `linked|neutral|not_applicable`、板块调用和理由，`unmapped_risk_item_ids` 必须为空。
- `opening_auction` 明确竞价/开盘缺口的 `bias`、路径和定性置信度。
- `intraday_followthrough` 明确开盘后延续或回吐的 `bias`、路径和定性置信度。
- `next_session` 保留下一交易日综合 `bias`、路径和定性置信度，不得替代两个日内窗口。
- `next_1_5_sessions` 明确未来1—5个交易日的 `bias`、路径和定性置信度。
- `index_style_implications` 写指数与风格相对强弱，例如大盘/小盘、价值/成长、防御/进攻。
- `sector_calls` 作为板块结论唯一事实源，每条都写方向、行业键、预测窗口、括号原因、驱动/反向信号、
  `considered_signal_ids`、`considered_domain_ids`、主导驱动、置信度、来源、失效条件和关联候选；二者分别与全信号
  台账和五域板块处置双向闭合。
- `unmapped_signal_ids`、`unmapped_risk_item_ids` 与 `unmapped_domain_ids` 必须机械重算为空；非空拒绝发布。
- `sector_beneficiaries` / `sector_pressures` 只能由 `sector_calls` 机械派生为
  `板块名（原因1；原因2）`；没有明确映射时写协议规定的未识别占位，不得手写第二套结论。
- `opening_triggers` 写开盘前和开盘后优先核对的海外股指、商品、汇率、利率、官方消息等。
- `upside_conditions` / `downside_conditions` / `invalidators` 写偏强、偏弱及推翻当前判断的条件。
- `plain_language_verdict` 必须包含“A股”，用一句白话明确偏强、偏弱、震荡、分化、修复或承压。
- `inference_boundary` 明确为运行时点 shadow 推断，不改市况、分数、裁定、仓位或熔断，也不倒灌T日。

允许方向判断，不允许伪精确。Agent③尚无经过预注册验证的A股择时概率模型，因此不得在
`ashare_runtime_outlook` 编造上涨/下跌概率、预计涨跌幅或指数目标点位；只使用
`low|medium|high` 定性置信度。若五域信号互相抵消，就写“震荡/分化/mixed”并说明主导条件，
不能退回“方向不确定”。

## 6. warning 等级和来源门槛

风险族枚举：

```text
macro_calendar
holiday_gap
war_energy
trade_control
liquidity
delisting_regulation
cross_asset
other_market
```

- `med`：已排期波动窗口，或有根据但方向/传导尚不确定的活跃压力。
- `high`：T 日已公开、仍活跃且存在明确系统性传导的压力。
- 排期事件一律只允许 `med`，不能因事件“重要”直接标红。
- 普通 warning 至少需要一个官方源；若暂无官方源，至少需要两个独立 `origin_id` 的可靠媒体源。
- `high` 必须同时具备至少一个官方直源/已验证官方镜像，以及至少两个独立 `origin_id`。
- 同一公告的转载、镜像和摘要共享一个 `origin_id`，不得用转载数量伪造多源确认。
- `unverified_secondary` 不得单独支撑 `high`。

预警描述必须写清“公开时间—仍在演化的事件—可能的传导链”，同时用 `evaluation` 给出证据约束下
的基准情景和条件情景。`direction_certainty=uncertain` 表示事件结果未落定，不等于免除分析。
不能只写“外围不稳”“消息面偏空”等无法核验的泛化判断。

## 7. 候选暴露映射与 HTML

每个候选都在 `by_code` 中占一项：

- `none`：没有可核验的直接暴露；
- `watch`：关联的最高等级为 `med`；
- `high`：至少关联一条 `high`。

行业预警只有在候选行业、产品、成本或海外收入暴露能够明确对应时才下沉。市场恐慌不能机械复制成
所有候选的同一条个股红色警示。

v3 的 `by_code` 另须包含 `sector_context[]`。当 `sector_calls[].industry_matches` 精确命中候选行业，或
`candidate_codes` 通过客户、供应商、竞争者、成本或海外收入关系显式纳入候选时，必须把同一 `call_id`
唯一双向下沉到股票卡片。股票 context 必须显示方向、产业关系、窗口、置信度、股票级原因、来源和失效条件；
正面或一般板块映射使用独立中性信息块，不得伪装成 warning alert。完整契约见
`AGENT3_SECTOR_MAPPING_PROTOCOL.md`。

每条 T 日 warning 和 T 后 delta 都必须：

1. 以同一 `warning_id`/`delta_id` 写入裁定文件顶层 `alerts`，形成报告级横幅；
2. 若映射到候选，再写入 `rulings[code].alerts`，形成个股卡片 warning；
3. alert 带 `level`、`shadow=true`、非空 `text`；文本显式包含“shadow”或“影子”；
4. T 后 alert 另带 `post_t=true`，文本显式包含“T后”。

示例：

```json
{
  "warning_id": "toxic-20260318-war-energy",
  "level": "high",
  "text": "Agent③影子预警：截至T战争与油价冲击仍在演化；不改分数或裁定。",
  "shadow": true,
  "post_t": false
}
```

HTML 顶部“市况”正下方必须先显示“Agent③ A股走势映射（运行时点 shadow）”白话卡片，包括
开盘、开盘后延续/回吐、下一交易日综合、未来1—5日、指数/风格、相对受益/承压板块 bullet、
开盘触发和推断边界。底部继续单列
“A股走势综合审计（五域合并）”和“运行时点五域综合评估（最新公开信息；不倒灌 T 日裁定）”，
显示逐域A股机制、检索完成时点、最新采用来源日期、共识、基准情景、条件情景和失效条件。

## 8. 结构化 JSON 契约

`bottom_adjudication.json.codex_audit` 增加：

```json
{
  "toxic_risk_warning": {
    "version": "bottom-toxic-risk-warning/v3",
    "T": "YYYY-MM-DD",
    "cutoff_beijing": "YYYY-MM-DD 23:59:59+08:00",
    "retrieved_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
    "mode": "shadow",
    "overall_status": "clear|watch|elevated",
    "required_domains": [
      "scheduled_macro_policy",
      "domestic_regulatory_liquidity",
      "external_geopolitics_trade",
      "cross_asset_stress",
      "holiday_information_gap"
    ],
    "sources": [
      {
        "source_ref": "toxic-source-001",
        "access_url": "https://...",
        "publisher": "来源",
        "source_kind": "official_direct|verified_official_mirror|independent_media|independent_research|unverified_secondary",
        "origin_id": "稳定去重键",
        "published_at": "YYYY-MM-DD",
        "published_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "phase": "as_of_t|post_t_safety"
      }
    ],
    "market_signals": [
      {
        "signal_id": "market-signal-001",
        "coverage_category": "us_equity_sectors",
        "family": "overseas_equity_sector",
        "phase": "post_t_safety",
        "observed_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "market_session_date": "YYYY-MM-DD",
        "instrument": "海外行业指数或关键公司",
        "direction": "positive|neutral|negative|mixed",
        "value_text": "已观察事实",
        "benchmark": "相对基准",
        "surprise": "预期差或不适用",
        "shock_type": "demand|supply|discount_rate|policy|risk_aversion|mixed|not_applicable|unknown",
        "horizons": ["opening_auction"],
        "source_refs": ["toxic-source-001"],
        "query_ids": ["toxic-query-001"],
        "freshness": "fresh|stale",
        "summary": "对A股行业可能具有信息含量的原因"
      }
    ],
    "predictive_input_coverage": {
      "us_equity_sectors": {
        "status": "hit|no_relevant_hit|blocked|not_open",
        "signal_ids": ["market-signal-001"],
        "query_ids": ["toxic-query-001"],
        "as_of_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "reason": "覆盖结论"
      }
    },
    "queries": [
      {
        "query_id": "toxic-query-001",
        "domain": "scheduled_macro_policy",
        "phase": "as_of_t|post_t_safety",
        "query_text": "实际执行的查询",
        "date_from": "YYYY-MM-DD",
        "date_to": "YYYY-MM-DD",
        "executed_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "outcome": "selected|no_relevant_hit|blocked",
        "reviewed_urls": ["https://..."],
        "selected_warning_ids": [],
        "selected_delta_ids": [],
        "selected_signal_ids": ["market-signal-001"],
        "notes": "采用、未命中或受阻说明"
      }
    ],
    "coverage": {
      "scheduled_macro_policy": {
        "status": "hit|no_relevant_hit|blocked",
        "query_ids": [],
        "warning_ids": [],
        "reason": "覆盖结论"
      }
    },
    "warnings": [
      {
        "warning_id": "toxic-warning-001",
        "level": "med|high",
        "risk_family": "war_energy",
        "status": "scheduled|active",
        "scope": "market|industry",
        "first_public_at": "YYYY-MM-DD",
        "event_start": "YYYY-MM-DD",
        "event_end": null,
        "direction_certainty": "uncertain|negative",
        "why": "截至T可核验的因果链",
        "affected_industries": [],
        "codes": ["600000"],
        "source_refs": ["toxic-source-001"],
        "query_ids": ["toxic-query-001"],
        "evaluation": {
          "fact_basis": "来源直接支持的事实",
          "consensus": "当前主流预期；无可靠共识时明确说明",
          "consensus_source_refs": ["toxic-source-001"],
          "base_case": "证据约束下最可能的路径",
          "confidence": "low|medium|high",
          "upside_scenario": "条件式缓和情景",
          "downside_scenario": "条件式恶化情景",
          "transmission_paths": ["事件→资产/行业→候选暴露"],
          "watch_variables": ["下次刷新必须核对的变量"],
          "invalidators": ["使基准情景失效的新事实"],
          "inference_boundary": "shadow，不改交易字段"
        },
        "shadow": true
      }
    ],
    "scheduled_event_expectations": [
      {
        "event_id": "event-us-cpi-YYYY-MM",
        "warning_id": "toxic-warning-001",
        "event_name": "美国某月CPI",
        "event_class": "inflation_cpi|inflation_ppi|labor_report|central_bank_decision|central_bank_communication|pmi|gdp|retail_sales|lpr|policy_calendar|holiday|other",
        "scheduled_for": "YYYY-MM-DD HH:MM:SS+08:00",
        "time_precision": "datetime|date|window",
        "official_source_refs": ["official-source-ref"],
        "consensus_status": "available|forecast_available|mixed_available|partial_available|qualitative_only|no_reliable_consensus|not_applicable",
        "consensus_source_refs": ["consensus-source-ref"],
        "consensus_query_ids": ["toxic-query-consensus-001"],
        "expectation_search_coverage": {
          "survey_consensus": {
            "status": "hit|no_relevant_hit|blocked|not_applicable",
            "query_ids": ["toxic-query-consensus-001"],
            "source_refs": ["consensus-source-ref"],
            "reason": "调查或市场定价检索结论"
          },
          "economic_calendar": {
            "status": "hit|no_relevant_hit|blocked|not_applicable",
            "query_ids": ["toxic-query-calendar-001"],
            "source_refs": [],
            "reason": "经济日历检索结论"
          },
          "institution_preview": {
            "status": "hit|no_relevant_hit|blocked|not_applicable",
            "query_ids": ["toxic-query-preview-001"],
            "source_refs": [],
            "reason": "机构/媒体预览检索结论"
          }
        },
        "required_metric_ids": ["cpi_headline_mom", "cpi_headline_yoy", "cpi_core_mom", "cpi_core_yoy"],
        "metrics": [
          {
            "metric_id": "cpi_headline_mom",
            "label": "总CPI环比",
            "consensus": "+0.1%",
            "previous": "+0.2%",
            "unit": "%",
            "estimate_kind": "survey_consensus",
            "metric_definition": "居民消费价格指数（CPI）总项月度环比",
            "source_refs": ["consensus-source-ref"]
          }
        ],
        "metric_search_ledger": [
          {
            "metric_id": "cpi_headline_mom",
            "status": "available|no_reliable_estimate",
            "query_ids": ["toxic-query-consensus-001"],
            "source_refs": ["consensus-source-ref"],
            "reason": "该指标的采用或未采用理由"
          }
        ],
        "qualitative_expectation": "可靠预览支持的定性预期；没有时说明三路检索边界",
        "consensus_note": "官方排期与一致预期的证据边界",
        "display_summary": "事件名：可直接展示的预期摘要",
        "watch_after_release": ["实际值相对预期差", "利率/汇率/风格反应"]
      }
    ],
    "scheduled_event_reconciliations": [
      {
        "event_id": "event-us-cpi-YYYY-MM",
        "status": "pending|released|delayed|cancelled|blocked",
        "checked_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "actual_released_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00|null",
        "actual_summary": "截至运行时点的实际内容或尚未发布/受阻边界",
        "actual_metrics": [
          {
            "metric_id": "cpi_headline_mom",
            "actual": "+0.2%",
            "unit": "%",
            "surprise": "高于一致预期0.1个百分点",
            "source_refs": ["actual-official-source-ref"]
          }
        ],
        "surprise_assessment": "实际相对事前预期的方向与幅度；未发布时写不适用",
        "delta_ids": ["toxic-delta-001"],
        "signal_ids": ["market-signal-actual-001"],
        "source_refs": ["actual-official-source-ref"],
        "query_ids": ["toxic-query-actual-001"],
        "sector_call_ids": ["sector-call-001"],
        "inference_boundary": "运行时点shadow对账；实际结果不倒灌T日裁定"
      }
    ],
    "by_code": {
      "600000": {
        "exposure": "none|watch|high",
        "warning_ids": [],
        "post_t_delta_ids": [],
        "reason": "暴露映射理由",
        "sector_context": [
          {
            "call_id": "sector-call-001",
            "relation": "direct_sector|customer|supplier|competitor|input_cost|overseas_revenue|sector_only",
            "direction": "beneficiary|pressure",
            "relevance": "direct|indirect|sector_only",
            "reason": "股票级映射理由",
            "source_refs": ["toxic-source-001"],
            "invalidators": ["股票级失效条件"]
          }
        ]
      }
    },
    "post_t_safety_items": [
      {
        "delta_id": "toxic-delta-001",
        "level": "med|high",
        "risk_family": "trade_control",
        "published_at": "YYYY-MM-DD",
        "why": "T后执行安全增量",
        "codes": [],
        "source_refs": [],
        "query_ids": [],
        "evaluation": {
          "fact_basis": "T后已核验事实",
          "consensus": "来源支持的当前共识或未找到可靠共识",
          "consensus_source_refs": [],
          "base_case": "运行时点基准情景",
          "confidence": "low|medium|high",
          "upside_scenario": "条件式缓和情景",
          "downside_scenario": "条件式恶化情景",
          "transmission_paths": ["传导链"],
          "watch_variables": ["观察变量"],
          "invalidators": ["失效条件"],
          "inference_boundary": "shadow，不改交易字段；T后信息不倒灌T日裁定"
        },
        "used_in_asof_t_warning": false,
        "shadow": true
      }
    ],
    "runtime_evaluation": {
      "scheduled_macro_policy": {
        "status": "hit|no_relevant_hit|blocked",
        "evaluated_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
        "latest_source_published_at": "YYYY-MM-DD|null",
        "query_ids": ["本域全部 as_of_t 与 post_t_safety 查询"],
        "warning_ids": [],
        "delta_ids": [],
        "signal_ids": ["market-signal-001"],
        "source_refs": [],
        "evaluation": {
          "fact_basis": "截至实际检索时点的事实综合",
          "consensus": "当前最普遍预期或未找到可靠共识",
          "consensus_source_refs": [],
          "base_case": "本域运行时点基准情景",
          "confidence": "low|medium|high",
          "upside_scenario": "条件式缓和情景",
          "downside_scenario": "条件式恶化情景",
          "transmission_paths": ["传导链"],
          "watch_variables": ["观察变量"],
          "invalidators": ["失效条件"],
          "inference_boundary": "shadow，不改交易字段；运行时点信息不倒灌T日裁定"
        }
      }
    },
    "ashare_runtime_outlook": {
      "evaluated_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
      "basis_domains": [
        "scheduled_macro_policy",
        "domestic_regulatory_liquidity",
        "external_geopolitics_trade",
        "cross_asset_stress",
        "holiday_information_gap"
      ],
      "source_refs": ["五域运行时点评估采用来源的完整并集"],
      "domain_impacts": {
        "scheduled_macro_policy": {
          "direction": "positive|neutral|negative|mixed",
          "mechanism": "本域如何反映到A股",
          "sector_disposition": "linked|neutral|not_applicable",
          "sector_call_ids": ["sector-call-001"],
          "sector_reason": "为何承接或为何不形成相对板块方向",
          "considered_warning_ids": ["toxic-warning-001"],
          "considered_delta_ids": ["toxic-delta-001"]
        }
      },
      "next_session": {
        "bias": "positive|neutral_positive|range_bound|neutral_negative|negative|mixed",
        "path": "A股下一交易日大概路径",
        "confidence": "low|medium|high"
      },
      "opening_auction": {
        "bias": "positive|neutral_positive|range_bound|neutral_negative|negative|mixed",
        "path": "A股竞价/开盘大概路径",
        "confidence": "low|medium|high"
      },
      "intraday_followthrough": {
        "bias": "positive|neutral_positive|range_bound|neutral_negative|negative|mixed",
        "path": "A股开盘后延续或回吐路径",
        "confidence": "low|medium|high"
      },
      "next_1_5_sessions": {
        "bias": "positive|neutral_positive|range_bound|neutral_negative|negative|mixed",
        "path": "A股未来1—5个交易日大概路径",
        "confidence": "low|medium|high"
      },
      "index_style_implications": ["指数与风格映射"],
      "sector_calls": ["完整对象见 AGENT3_SECTOR_MAPPING_PROTOCOL.md"],
      "signal_disposition_ledger": ["全部 market_signals 的唯一处置，完整对象见 AGENT3_SECTOR_MAPPING_PROTOCOL.md"],
      "risk_item_disposition_ledger": ["全部 warning/T后 delta 的唯一处置，完整对象见 AGENT3_SECTOR_MAPPING_PROTOCOL.md"],
      "unmapped_signal_ids": [],
      "unmapped_risk_item_ids": [],
      "unmapped_domain_ids": [],
      "sector_beneficiaries": ["由sector_calls派生的板块名（原因）"],
      "sector_pressures": ["由sector_calls派生的板块名（原因）"],
      "opening_triggers": ["开盘优先核对变量"],
      "upside_conditions": ["转强条件"],
      "downside_conditions": ["转弱条件"],
      "invalidators": ["推翻当前A股基准判断的条件"],
      "plain_language_verdict": "A股更可能……",
      "inference_boundary": "运行时点shadow推断，不改交易字段且不倒灌T日裁定"
    },
    "clear_reason": "clear 时必须非空；说明检索边界，不得声称风险不存在"
  }
}
```

`overall_status` 机械重算：存在 `high` 为 `elevated`；否则有 `med` 或任何风险域 `blocked` 为 `watch`；
其余为 `clear`。`by_code.exposure` 同样按关联条目的最高等级重算。

## 9. 发布门禁

写完裁定文件后运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_latest.json" `
  --audit "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_adjudication.json"
```

该命令同时验证 Agent② `bottom_search` 和 Agent③ `toxic_risk_warning`。Agent③缺五域运行时点评估、
缺八类预测输入覆盖、重大排期事件漏一对一预期台账、三路预期检索未闭合、把机构预测冒充一致预期、
CPI/PPI 漏事件专属四指标的逐项搜索台账、混用指标口径、指标缺前值/来源/口径定义、缺事件推断、
只写“方向不确定”、末端扫描未覆盖至实际运行日、无来源精确概率、
缺分窗口A股走势综合、存在未处置信号、板块 bullet 无括号原因、板块与候选股票未双向下沉、没有逐域说明如何反映到A股、
已到排期时点仍未对账实际结果、存在未处置 warning/T后 delta、编造A股精确涨跌概率/幅度/点位或发生 T 后倒灌时均失败。
最终发布只接受 v3；历史 v2 仅保留非严格读取兼容。
任一失败都不得运行 `--adjudicate`。最终 `augment-report` 会生成 Agent③ 独立审计表，
`validate --require-bottom-search` 还会检查A股白话综合确实位于顶部市况下、重大排期事件的北京时间、
全部指标预期/前值/情景/来源、运行时点共识/基准情景、
warning 来源链接和报告/个股 alert 文本确实出现在 HTML。不可变核心 HTML 中遗留的旧“61%”
集中窗口径由该附录强制校正为真实连续5个市场交易日的57.2%（严格10月）/55.3%（含边界月），
最终报告不得再引用旧数值。

Agent③ 在完成无前视 shadow 样本、误报率和机会成本评估前，不得升级为数值 gate、自动禁买、降级或仓位规则。
