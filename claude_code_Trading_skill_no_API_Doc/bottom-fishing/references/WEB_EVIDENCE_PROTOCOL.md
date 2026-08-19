# 抄底网页证据协议（bottom-search-audit/v1）

本协议只约束 Agent② 对过线票的消息面搜索、时点隔离与裁定证据，不修改量化分数、ATR gate、冷却或执行价位。
Agent③ 的市场级毒月 Web 风险 nowcast 使用独立的
`TOXIC_RISK_WARNING_PROTOCOL.md`；两套审计共享 T 日前视纪律，但不得相互代替覆盖。

## 目录

1. 时间与身份锚定
2. 固定检索顺序
3. 六类覆盖矩阵
4. 官方源、动态站与来源血缘
5. 日期冲突、旧闻与未命中
6. F10 逐条对账
7. T 后安全增量
8. 查询模板
9. 结构化 JSON 契约
10. 裁定与发布门禁
11. Agent③ 联动

## 1. 时间与身份锚定

先记录两个不同的“最新”，不得混写：

- **裁定可用最新**：信号日 T 的 `23:59:59+08:00`。只有首次公开时间不晚于该截止的事实可进入
  `codex_audit.facts[]` 和 as-of-T 裁定。
- **检索发现更新至**：实际完成末端扫描的北京时间。T 后信息只进入
  `codex_audit.bottom_search.post_t_safety_by_code`。

每票先核对 A 股代码、交易所、公司全称、常用简称、曾用名、英文名、H 股代码、控股股东、重要子公司、
主要产品和海外暴露。查询必须至少覆盖代码、公司全称、简称；有 H 股或重要海外业务时再覆盖英文名、品牌名和子公司名。

日期含义必须分开：

- `published_at`：首次可验证公开日。
- `event_date`：事件发生日。
- `report_period`：财报或经营数据所属期间。
- `effective_date`：政策、交易或监管措施生效日。
- `retrieved_at_beijing`：本次检索时点。

欧美来源换算为北京时间。若只有日期，保留 `published_time_precision=date`；若两个可靠来源分别显示 T 和 T+1
且无法确认首次公开时间，按 T 后隔离，不能用较早日期强行通过截止。

## 2. 固定检索顺序

对每只过线票依次完成：

1. 回扫交易所/巨潮/HKEXnews/公司 IR 在 T 前 30 日的公告清单，记录最新一条 T 前官方公告。
2. 找到最新季报、半年报、年报、业绩预告或快报，建立最新财务锚，并与上一报告期比较。
3. 将引擎 `forecast` 和 `notices[]` 每一条写入 F10 ledger；同日多条公告也必须逐条对账。
4. 完成六类覆盖矩阵，不以事实总数代替分类覆盖。
5. 对重大负面反向搜索其最新进展：是否已解决、是否一次性、是否仍在进行。
6. 用精确公告标题、公告编号、代码和完整公司名回溯动态站遗漏，并从镜像追到官方原文。
7. 单独搜索 T 日跌因；媒体猜测只作线索，必须回溯到公告、政策、行业数据或多个独立来源。
8. 最后从 T+1 到检索时点做一次末端新鲜度扫描。这里的 T+1 是**自然日**；交易执行章节的 T+1 才是交易日。
   新发现的 `≤T` 信息必须回填并重做裁定；`>T` 信息进入安全增量。若所有末端查询都 `blocked`，不得宣称已更新到检索日。

最新财务锚、官方公告回扫或任一可能改变结论的来源受阻时，不得给 `✓`，按不确定性降为 `?`。

## 3. 六类覆盖矩阵

`coverage_by_code[代码].categories` 必须精确覆盖下列六类，每类状态只能是
`hit | no_relevant_hit | blocked`：

| category | 必查内容 | 基础时间窗 |
|---|---|---|
| `performance_operations` | 最新财报/预告/快报、营收、归母、扣非、毛利率、经营现金流、销量/产量/订单 | 最新报告＋T前180日 |
| `financial_credit` | 现金与短债、逾期/展期、评级、担保、偿债、大存大贷、持续负现金流 | T前12个月；存续违约追至结案 |
| `governance_regulatory` | 立案、问询、监管措施、审计意见、差错更正、诉讼执行、ST、董监高异常 | T前24个月并查最新进展 |
| `corporate_events` | 减持、解禁、质押、定增、配股、可转债、回购、资产交易、减值、H股配售 | T前180日及截至T已知安排 |
| `industry_policy_domestic` | 国内价格、需求、产能、政策、补贴、招标、同业经营、进出口 | T前90日；周期品重点T前30日 |
| `external_global_peer` | 关税、制裁、出口管制、FDA/EMA、FEOC、OPEC、海外同业和终端需求 | T前90日及仍生效政策 |

周期、出口、医药、A/H 两地上市或有重要海外子公司的公司，国内与海外两向都必须实搜。其他公司也必须留下
`external_global_peer` 查询；无显著暴露时写 `no_relevant_hit` 和理由，不能静默跳过。

`no_relevant_hit` 只表示“在列明渠道和查询中未找到截至 T 可核实的相关重大证据”，不等于确认不存在。
`blocked` 表示来源打不开、动态内容无法核验或日期无法确认；它不是未命中。

## 4. 官方源、动态站与来源血缘

来源优先级：

1. 上交所、深交所、北交所、巨潮、HKEXnews、公司 IR 的公告原文/PDF。
2. 证监会、交易所监管、政府、法院/执行平台、海关、统计部门、行业监管机构。
3. 海外政府/监管机构、当地交易所、公司或同业 IR、评级机构。
4. 主流财经媒体和证券报。
5. 新浪、东方财富、搜狐、雪球等镜像或聚合页。

搜索摘要只用于发现，不能直接进入 `facts[]`。动态页打不开时依次：

1. 用“代码＋完整标题＋日期/公告编号”搜索。
2. 搜索官方静态 PDF 域名或文件名。
3. 检查公司 IR、另一上市地或政府原始页面。
4. 用镜像取得标题、编号和日期，再回溯官方原文。
5. 确实无法回溯时，使用两个真正独立的二级来源，并标记 `unverified_secondary`；不能据此作决定性 `✓/✗` 证据。

同一公告的交易所原文、公司转载、新浪和搜狐镜像只算一个 `origin_id`。不得用多个镜像凑“多源”。

`source_kind`：

- `official_direct`
- `verified_official_mirror`
- `independent_media`
- `independent_research`
- `unverified_secondary`

`verified_official_mirror` 必须保存不同于访问链接的 `canonical_url`、官方发布者、非空 `document_id`
（稳定文档编号或公告编号），并至少用标题、发布日期、文档编号、全文中的两项完成匹配。

## 5. 日期冲突、旧闻与未命中

首次发布日期判定优先级：

1. 官方 PDF/公告详情元数据。
2. 交易所公告列表。
3. 公司 IR。
4. 主流媒体原稿。
5. 聚合站。
6. 搜索摘要，仅作线索。

遇到冲突：

- 保留两种日期和来源，在 `notes/uncertainties` 解释。
- 能确认首次官方公开日时采用该日。
- 冲突跨越 T 且无法确认时，放入 T 后隔离并降置信度。
- 后续转载日不能替代首次发布日期；事件日也不能冒充发布日期。
- 旧立案、减值、诉讼或政策只有查到截至 T 仍存续/仍生效，才能写成当前风险。
- 无法核到年份的红旗一律不采用。

只有完成候选级官方入口，并在该维执行至少两条不同的查询语句后，才能写 `no_relevant_hit`。标准措辞：

> 截至T，已检查列明的官方渠道及分类查询，未发现可核实的重大相关公开证据；这表示本次公开检索未命中，
> 不等于确认该事项不存在。

## 6. F10 逐条对账

从原始 `bottom_latest.json` 为每条种子建立唯一键：

- 预告：`{code}:forecast`
- 公告：`{code}:notices:{raw_index}`

预告的 `raw_index=null`，`seed_text` 固定按 `notice_date + 空格 + type + 空格 + content` 拼接；公告的
`raw_index` 等于原始数组下标，`seed_text` 必须逐字等于原始 `notices[raw_index]`。
原始 seed 集在本次裁定中不可变：末端搜索新发现但原 JSON 没有的公告只建 delta，不得补造 seed。

Ledger 必须与原始种子一一相等，不能漏、不能多造：

- 预告的“实质”由验收器重算：`forecast.fresh=true`，或 `type` 命中
  `预亏/预减/略减/续亏/首亏`；不得拿可能由另一条公告点亮的整票 `f10_flag` 代替逐条判断。
- 公告的“实质”由标题/原文命中以下风险或重大事件词重算：
  `业绩/预告/减值/诉讼/立案/问询/监管/处罚/解禁/减持/增持/回购/中标/合同/订单/重组/收购/批件/批复/担保/质押/可转债/停牌/退市/风险警示`。
- 未命中上述机械口径的 T 前种子可标 `logged_routine_pre_t`；仍须逐条保留 reason，但无需虚构 fact。

- T 日及以前的实质种子：`adjudicated_pre_t`，至少关联一个 `fact_id`，事实
  `f10_match` 为 `confirmed` 或 `conflict`。
- T 日及以前的例行种子：`logged_routine_pre_t`，写明不影响裁定的理由。
- T 日以后：`quarantined_post_t`，不得关联主事实，必须关联末端扫描 query 与 delta。
- 无日期或日期无法确认：`quarantined_undated`，不得进入主事实。

同日四条公告必须有四条 ledger；旧校验器按日期命中不代表完成逐条核对。

## 7. T 后安全增量

T 后信息不进入 as-of-T `facts[]`。每票保存：

- `checked_through_beijing`
- `base_verdict_asof_t`
- `effective_verdict`
- delta 标题/摘要、首次发布日期、事件日、来源血缘、查询 ID、不确定性

安全增量只能维持或降低裁定上限：

- 正面或中性：`effect=none`
- 负面：`none | cap_at_question | cap_at_reject`
- 未核实但可能改变结论：可 `cap_at_question`

`unverified` 不能使用 `cap_at_reject`；来源为 `unverified_secondary` 的 delta 必须标 `polarity=unverified`，
且 `effect` 只能是 `none` 或 `cap_at_question`。未经核实的 T 后线索最多把上限压到 `?`。

`effective_verdict` 必须等于 base 与全部 `cap` 共同形成的最低上限，不得任意上调或额外下调。T 后利好不得把
`?→✓` 或 `✗→?`。每条 delta 必须
`used_in_asof_t_verdict=false`；它只提供执行时点的保守安全上限。报告必须同时显示 as-of-T 裁定和检索时点有效裁定。
`rulings[code].verdict` 必须等于 `effective_verdict`；adjudicate 只执行有效裁定，T 日原裁定留在审计层供复盘。

## 8. 查询模板

按公司替换，不要求机械逐字运行：

```text
"<代码>" "<公司全称>" 公告 <T年份> <T月份>
site:sse.com.cn "<代码>" "<公告完整标题>"
site:szse.cn "<代码>" "<公告完整标题>"
site:cninfo.com.cn "<代码>" "<公告完整标题>"
"<公司名>" 业绩预告 OR 业绩快报 OR 扣非 OR 毛利率 OR 经营现金流
"<公司名>" 违约 OR 展期 OR 评级下调 OR 担保 OR 短期债务
"<公司名>" 立案 OR 问询函 OR 审计意见 OR 差错更正 OR 诉讼 OR 被执行
"<公司名>" 减持 OR 解禁 OR 质押 OR 定增 OR 配股 OR H股 OR 减值
"<产品/行业>" 价格 OR 需求 OR 产量 OR 政策 OR 招标 <近期月份>
"<英文名/产品>" tariff sanctions export control recall demand
site:hkexnews.hk "<H股代码或英文名>" placing OR monthly return OR issue
"<代码或公司名>" 大跌 OR 跌停 OR 异动 "<T日期>"
```

每票至少有一个 `query_mode=official` 和一个 `query_mode=drop_cause`；其余模式可用
`exact_title | broad_web | regulator | industry | overseas | ah_cross_listing | freshness_delta`。
跨维查询按它直接检验的主要风险归入一个 category；官方最新财务/公告锚通常归
`performance_operations`，跌因和 freshness 查询归触发该事项的维度，不得用一条查询冒充多个维度的覆盖。
`as_of_t` 的 `date_to` 必须等于 T；`post_t_safety` 必须从 T+1 查到检索日。所有执行时点、T 后发布日期和
`checked_through_beijing` 都不得晚于 `retrieved_at_beijing`。被采用 fact/delta 的 `access_url` 必须出现在对应查询的
`reviewed_urls`；查询的 `date_to` 不得晚于实际执行日。每票 `checked_through_beijing` 必须覆盖到检索日，且不得早于
任何已记录 delta 的发布日期。

## 9. 结构化 JSON 契约

完整字段由
`C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\JUDGE_SCHEMA.md`
定义。最小关系如下：

```text
facts[].fact_id/category/source_ref/seed_refs
        │             │
        │             └── bottom_search.sources[].source_ref
        ├── bottom_search.queries[].selected_fact_ids
        ├── bottom_search.coverage_by_code.*.categories.*.fact_ids
        └── bottom_search.coverage_by_code.*.ruling_evidence.*

原始 forecast/notices ──逐条──> bottom_search.f10_seed_ledger
T后 seed/query ──────────> bottom_search.post_t_safety_by_code.*.items[]
```

查询日志只保存查询语句、窗口、状态、审阅 URL、采用的 fact/delta 和必要 notes；不保存无关搜索结果全文。

若引擎为 **0 只过线票**，仍保留 `bottom_search` 的 `version`、`T`、`cutoff_beijing`、
`retrieved_at_beijing`、`required_categories`，另写非空 `empty_reason`；`sources=[]`、`queries=[]`、
`coverage_by_code={}`、`f10_seed_ledger=[]`、`post_t_safety_by_code={}`；`market_coverage` 固定写成
`{"status":"no_relevant_hit","query_ids":[],"fact_ids":[],"reason":"非空的空手说明"}`。不得为满足 schema
虚构候选、查询或事实。存在候选时，市场查询和事实固定使用 `code=MARKET`、`category=market_regime`。

## 10. 裁定与发布门禁

只有以下条件全部满足才可给 `✓`：

- 最新财务锚已核实。
- 六类均完成且无 `blocked`。
- `performance_operations=hit`，至少三类为 `hit`。
- 决策事实至少包含一个官方直源或已验证官方镜像。
- 采用事实至少覆盖三个不同 `origin_id`。
- 官方 T 前公告回扫、F10 ledger 和跌因搜索完成。
- 未决项不会改变“基本面无持续恶化”的结论。

`✗` 的决定性恶化事实不得来自 `unverified_secondary`。`?` 必须明确记录未解决查询、来源受阻或正反冲突。
若官方最新回扫全部受阻，`official_latest_check.latest_pre_t=null`，只能给 `?/✗`；不得编造日期。若搜索完成时 T 已非
最近已收盘日，或 T+1 交易日的计划入场窗口已经错过，必须重跑扫描，不能把旧 T 报告当当前最终报告。

写完 `bottom_adjudication.json` 后运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_latest.json" `
  --audit "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_adjudication.json"
```

非零退出时不得运行 `--adjudicate`。最终验收必须再加 `--require-bottom-search`，防止 attach 或 HTML 阶段丢失搜索审计。

## 11. Agent③ 联动

完成本协议的候选级搜索后，必须再按 `TOXIC_RISK_WARNING_PROTOCOL.md` 完成五个市场风险域。
`validate-bottom-search` 会同时要求 `codex_audit.bottom_search` 和
`codex_audit.toxic_risk_warning`。市场级 `warning_id` 若映射到某候选，必须写入该票
`rulings[code].alerts`；但 Agent③ 只增加 shadow warning，不改变 Agent② 的 ✓/?/✗。
