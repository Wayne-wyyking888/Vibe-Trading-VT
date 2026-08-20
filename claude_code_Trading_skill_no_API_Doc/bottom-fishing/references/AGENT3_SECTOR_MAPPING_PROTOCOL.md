# Agent③ 全市场主线发现、外盘输入与A股板块映射协议（v4）

本协议定义 `bottom-toxic-risk-warning/v4` 的全市场主线发现、重大异动归因、预测输入层、A股分窗口板块映射和候选股票信息下沉。
它只产生运行时点 `shadow` 信息：不得修改量化分数、推荐线、排序、✓/?/✗、仓位或预算熔断。
Agent③不是收益预测模型；所有方向都是证据约束的条件式映射，必须保留来源、时点、反向因素和失效条件。

## 目录

1. 目标与时间窗口
2. 全市场主线发现与重大事件归因
3. 八类预测输入
4. 信号选择与因果映射
5. 时间、会话与新鲜度
6. 结构化市场信号
7. 板块综合调用
8. HTML bullet 格式
9. 候选股票双向下沉
10. 冲突裁定与置信度
11. 发布门禁与 shadow 验证

## 1. 目标与时间窗口

Agent③必须把运行时点信息分成三个不同目标，禁止用“下一交易日”混成一个判断：

- `opening_auction`：前收盘到次日竞价/开盘缺口，优先吸收隔夜海外、国内夜盘和开盘前资产价格；
- `intraday_followthrough`：开盘后延续或回吐，必须写量价、人民币、A50、国内事件等确认条件；
- `next_1_5_sessions`：未来1—5个交易日的基本面、政策、商品价差或产业趋势传导。

保留 `next_session` 作为三个窗口的白话综合兼容字段，但不得用它替代分窗口判断。外盘行业大涨可以是
强开盘输入，却不能自动推出A股收盘同幅上涨；若缺少日内确认，必须明确“主要映射开盘、存在回吐风险”。

## 2. 全市场主线发现与重大事件归因

八类预测输入是下游登记层，不能代替上游发现。Agent③必须同时采用“价格先行”和“事件先行”，避免只检索预设主题：

1. `us_sector_tape`：美股行业相对宽基、行业ETF、同业广度；宽基指数本身不能单独过关；
2. `global_movers`：全球重大单股、商品、运价、利率与跨资产异动；
3. `ashare_t_day_sector_tape`：A股T日行业涨跌、相对宽基、涨停/大涨家数和领涨结构；必须至少命中一条T日异动；
4. `asia_sector_tape`：日韩台及香港同业；未开盘只能写 `not_open`；
5. `event_first_scan`：临床、监管、财报指引、订单/Capex、产品发布、政策地缘、供给中断、商品和宏观利率等重大事件。

`market_discovery.sector_family_coverage` 还必须精确覆盖：
`healthcare_biotech`、`technology_media_telecom`、`consumer`、`financial_real_estate`、
`energy_materials`、`industrials_transport_defense`、`utilities_clean_energy`、`broad_market_style`。
每一族都写 `hit|no_relevant_hit|blocked|not_open`、查询、异动、事件、评估时点和理由。莫德纳/mRNA只是医疗族的
回归样例；芯片财报、消费指引、银行地产政策、油价/金属、军工订单、航运中断、新能源政策等必须走同一套门禁。
每条覆盖查询还必须以 `discovery_lanes/discovery_families` 显式声明自己核验的路径和行业族；行业族写
`no_relevant_hit` 时至少需要两种不同查询文本，禁止把一条泛化市场搜索复制给八个行业族凑覆盖。

每条 `material_movers[]` 至少保存 `mover_id/lane/market/market_session_date/observed_at_beijing/instrument/`
`sector_family/sector_name/direction/performance_text/benchmark/breadth_text/catalyst_status/event_ids/`
`signal_ids/source_refs/query_ids/materiality_reason`。`catalyst_status=attributed|multi_factor` 必须连接事件簇；
确实无法归因时写 `unresolved` 并进入 `unresolved_material_mover_ids`，不得静默丢弃或编造单因果。

每条 `event_clusters[]` 必须保存事件类型、首次公开北京时间、发现路径、行业族、关联异动/信号、主来源与佐证来源、
A股主题、传导链、`direct|supply_chain|conceptual` 分层映射、反证、失效条件、置信度和板块调用。查询通过
`selected_mover_ids/selected_event_ids` 与异动、事件双向闭合；异动与事件也必须双向引用。

### 2.1 新鲜度、A股兑现与旧消息排除

“消息重大”不等于“仍有新增信息量”。每个事件必须登记：

- `freshness=fresh|aging|stale` 与文字理由；
- `ashare_absorption.status=new_unpriced|partially_priced|priced_on_t|priced_before_t|stale|unclear`；
- `first_reaction_date/latest_confirmation_date/evidence/source_refs/query_ids/decision`；
- 机械派生 `tradability_flag=fresh_catalyst|continuation_watch|already_priced|stale_excluded|unresolved`。

硬规则：`priced_before_t` 或 `stale` 不得再生成板块受益调用；其相关信号只能作 `stale_excluded`、中性或反证。
`priced_on_t` 说明A股当日已经兑现，只允许低/中置信 `continuation_watch`，必须同时显示隔日追高、回吐与失效条件，
不得把同一旧消息包装成次日“新催化”。`unclear` 必须公开列入未决台账，不能为了给方向而强行归因。

## 3. 八类预测输入

`predictive_input_coverage` 必须精确覆盖以下八类。每类记录
`hit|no_relevant_hit|blocked|not_open`、查询、评估时点和理由；`hit` 只能承接 `fresh` 信号。

| coverage_category | 必查内容 | 主要A股映射 |
|---|---|---|
| `us_equity_sectors` | 最新完成美股行业相对宽基收益、SOX/软件/生物科技/能源等 | 次日竞价和开盘风格 |
| `global_peer_events` | 全球龙头财报、指引、订单、库存、Capex、产品价格 | 客户—供应商—竞争者链条 |
| `asia_equity_peers` | 日本、韩国、台湾开盘后的同业相对本地宽基收益 | 09:15前的同业确认；尚未开盘写 `not_open` |
| `china_linked_assets` | 中概股、H股/双重上市、离岸中国ETF、A50、CNH | 中国风险偏好与大盘风格确认 |
| `rates_fx_volatility` | 美债曲线、实际利率、DXY、CNH、VIX及异常波动 | 成长估值、外资敏感、黄金与高股息 |
| `commodities_freight` | 原油、金属、农产品、国内夜盘、运价、产品价差 | 上游收入、下游成本、航运与制造利润 |
| `macro_surprises` | 实际值减可靠一致预期及关键分项 | 公布后的周期、银行、消费、地产与小盘制造 |
| `domestic_policy_industry` | 部委、央行、证监会、交易所及产业主管部门新信息 | 政策直接覆盖行业；通常可压过外盘信号 |

`physical_supply_chain` 可作为 `market_signals[].family` 补充存储、面板、化工、光伏、锂电等公开库存、
价差和产销信号，但必须归入最贴近的 coverage 类，不新增未审计的第九类。

## 4. 信号选择与因果映射

按以下顺序工作：

1. 先核验最新已完成交易时段，计算或引用行业/个股相对当地宽基或行业基准的表现；不得只看纳指、标普裸涨跌。
2. 再核验财报、指引、Capex、订单、库存或产品价格，区分直接同业、客户、供应商、竞争者和投入成本。
3. 商品价格必须标 `shock_type`。需求改善、供给中断、通胀/利率或风险厌恶导致的同方向价格变化，A股含义不同。
4. 利率上涨必须区分增长、通胀、政策和风险溢价；不得机械写成“成长必跌”。
5. 宏观只使用 `actual-consensus` 和分项。发布前只能记录可靠共识和条件情景，发布后才能记录 actual surprise。
6. 国内官方政策和产业事件与外盘冲突时，明确谁是主导驱动及原因，不做简单票数表决。

禁止：

- “美股科技涨，所以所有A股科技都受益”；
- “PMI低于50，所以所有周期股都承压”；
- “油价上涨，所以油气受益且航空承压”，却不说明供需冲击来源；
- 使用搜索摘要、社交媒体传闻或陈旧行情作为唯一方向证据；
- 把观察到的海外涨幅改写成未经校准的A股预计涨幅或上涨概率。

## 5. 时间、会话与新鲜度

- `observed_at_beijing` 是信号实际可得时点，必须不晚于 `retrieved_at_beijing`。
- `market_session_date` 是原市场交易日，不得用北京时间自然日替代；美国T日收盘可能在北京时间T+1凌晨可得。
- v4来源同时保存本地 `published_at` 和 `published_at_beijing`；phase 一律按北京时间戳相对T日截止判断，
  禁止用美国当地发布日期把北京时间T+1凌晨才可得的收盘信息误归入 as-of-T。
- 07:xx北京时间运行时，美股/美债必须采用当时最新已完成的美国交易时段；若原市场交易日等于A股T日，
  该收盘或官方日值只能是 `post_t_safety`。验收器据此拒绝“美国T日收盘 + 北京时间T日观测”的伪时点组合。
- 美债盘中媒体报价与财政部官方日值要分开标注；官方日值优先进入顶部“已观察事实”，盘中值不得冒充收盘，
  且必须区分财政部回购、FOMC沟通等并存驱动，不把同日变化单因果归给某一事件。
- `phase=as_of_t` 的观测时点不得晚于T；`phase=post_t_safety` 的观测时点必须晚于T，且不得倒灌T日裁定。
- `freshness=fresh` 表示采用最新已完成的相应交易时段或最新可靠发布；`stale` 只能留作审计，不能进入板块主驱动。
- 07:xx运行至少核验最新美欧收盘、国内夜盘、商品、利率和汇率；若做09:15刷新，再纳入日韩台早盘、A50和CNH。
- 9:30才公布的数据属于盘中增量。7:xx报告只能保存一致预期和条件分支，禁止事后补入 actual 冒充盘前命中。
- 来源受阻写 `blocked`；亚洲市场尚未开盘写 `not_open`。两者都不得伪造方向信号。

## 6. 结构化市场信号

每条 `market_signals[]` 使用：

```json
{
  "signal_id": "market-signal-001",
  "coverage_category": "us_equity_sectors",
  "family": "overseas_equity_sector",
  "phase": "post_t_safety",
  "observed_at_beijing": "YYYY-MM-DD HH:MM:SS+08:00",
  "market_session_date": "YYYY-MM-DD",
  "instrument": "指数、公司、商品或宏观事件",
  "direction": "positive|neutral|negative|mixed",
  "value_text": "已观察事实，可含有来源支持的精确海外涨跌幅",
  "benchmark": "相对基准；不适用时明确写不适用",
  "surprise": "actual-consensus、财报预期差或不适用",
  "shock_type": "demand|supply|discount_rate|policy|risk_aversion|mixed|not_applicable|unknown",
  "horizons": ["opening_auction", "intraday_followthrough"],
  "source_refs": ["toxic-source-001"],
  "query_ids": ["toxic-query-001"],
  "freshness": "fresh|stale",
  "summary": "为何可能对A股行业具有信息含量"
}
```

查询必须以 `selected_signal_ids` 双向引用信号；信号来源必须出现在关联查询的 `reviewed_urls`，并进入
所属五域 `runtime_evaluation.signal_ids/source_refs`。精确数字只可描述已观测事实；A股方向继续使用定性置信度。

### 6.1 全信号处置台账

八类覆盖只证明“信号被登记”，不证明“板块结论考虑过信号”。因此
`ashare_runtime_outlook.signal_disposition_ledger` 必须与 `market_signals[]` 一对一：每个 signal 恰有一条处置，
不得只挑支持既有结论的信号。

- `direct_driver`：直接进入一个或多个 `sector_call.driver_signal_ids`；
- `direct_opposing`：直接进入一个或多个 `sector_call.opposing_signal_ids`；
- `direct_mixed`：在不同 call 中同时承担 driver 与 opposing；
- `mediated`：不重复进入 call，通过另一条已直接使用的信号传导，例如 CPI→美债利率→成长估值；必须写
  `mediated_by_signal_ids` 和实际承接的 `sector_call_ids`；
- `neutral`：已核验但没有足以形成相对板块方向的预期差；
- `not_applicable`：对当前A股板块窗口不适用，并说明边界；
- `stale_excluded`：陈旧信号仅留审计，不得进 call。

每条处置都写 `signal_id/disposition/sector_call_ids/mediated_by_signal_ids/reason`。直接处置的 call 集合必须与
driver/opposing 实际引用精确一致；`mediated` 的承接 call 必须由中介信号真实驱动或反向引用；其余处置不得夹带 call。
每个 call 的 `considered_signal_ids` 必须等于它直接引用和经台账传导到本 call 的信号并集。
`unmapped_signal_ids` 必须机械重算为空；任何 signal 缺台账、重复处置、伪中介、陈旧信号参战或无理由悬空都拒绝发布。

全信号台账之外，五个 `domain_impacts` 必须逐域写 `sector_disposition/sector_call_ids/sector_reason`：`linked` 精确
承接 call，`neutral|not_applicable` 不得夹带 call；这一步保证 scheduled warning、其他 warning、T后 delta 和五域
综合中没有生成独立 market_signal 的报告内容也被板块映射显式考虑。每个 call 的 `considered_domain_ids` 与这些引用
双向闭合，`unmapped_domain_ids` 必须为空。

为避免“域字段存在但具体风险项语义悬空”，每个 `domain_impacts` 还必须保存
`considered_warning_ids/considered_delta_ids`，并与对应 `runtime_evaluation` 精确一致；同时
`risk_item_disposition_ledger` 必须对每条 warning/T后 delta 唯一记录：

```json
{
  "item_id": "toxic-delta-001",
  "item_kind": "post_t_delta",
  "domain_ids": ["scheduled_macro_policy"],
  "disposition": "linked|neutral|not_applicable",
  "sector_call_ids": ["sector-call-001"],
  "reason": "该增量如何进入板块调用，或为何不形成相对行业方向"
}
```

`linked` 必须由所属域的真实 call 承接；`neutral|not_applicable` 不得夹带 call。
`unmapped_risk_item_ids` 必须机械重算为空，不能用泛化 `domain_impacts` 文案代替逐条处置。

## 7. 板块综合调用

`ashare_runtime_outlook.sector_calls[]` 是板块 bullet 的唯一事实源：

```json
{
  "call_id": "sector-call-001",
  "direction": "beneficiary|pressure",
  "sector_name": "半导体设备、存储",
  "sector_keys": [
    {"taxonomy": "SW2021", "level": "L2", "id": "行业ID", "name": "半导体"}
  ],
  "industry_matches": ["半导体", "电子化学品Ⅱ"],
  "horizon": "opening_auction|intraday_followthrough|next_1_5_sessions",
  "reasons": ["隔夜海外芯片同业相对宽基明显走强", "关键公司指引验证需求"],
  "driver_signal_ids": ["market-signal-001"],
  "opposing_signal_ids": ["market-signal-002"],
  "considered_signal_ids": ["market-signal-001", "market-signal-002", "market-signal-003"],
  "considered_domain_ids": ["scheduled_macro_policy", "cross_asset_stress"],
  "dominant_driver": "主导驱动及其胜过反向因素的原因",
  "confidence": "low|medium|high",
  "invalidators": ["竞价未跟随且开盘后行业相对收益迅速转负"],
  "source_refs": ["驱动与反向信号来源的完整并集"],
  "candidate_codes": ["600000"],
  "shadow": true
}
```

板块调用之外，走势对象还必须保存：

```json
{
  "signal_disposition_ledger": [
    {
      "signal_id": "market-signal-003",
      "disposition": "mediated",
      "sector_call_ids": ["sector-call-001"],
      "mediated_by_signal_ids": ["market-signal-001"],
      "reason": "宏观实际值先反映到利率，再通过折现率影响成长板块；避免重复计数"
    }
  ],
  "unmapped_signal_ids": []
}
```

`sector_beneficiaries` 和 `sector_pressures` 只作旧字段兼容，必须机械派生为
`板块名（原因1；原因2）`。没有调用时分别写 `未识别明确相对受益板块`、`未识别明确相对承压板块`；
不得手写与 `sector_calls` 不一致的第二套结论。

`industry_matches` 只放引擎候选中可以精确匹配的行业原文。广义“科技”“周期”不得模糊命中候选；若通过客户、
供应商、竞争者或成本关系映射，显式加入 `candidate_codes`，并在股票 context 中写清关系。

## 8. HTML bullet 格式

顶部“全市场主线发现与事件归因”必须先声明：T 是A股信号交易日，按北京时间解释；`as_of_t/post_t_safety`
按北京时间可得时点相对 `cutoff_beijing=T 23:59:59+08:00` 判断，而 `market_session_date` 保留原交易所会话日。
每张重大异动卡必须可见显示 `原市场交易日=market_session_date` 与
`北京时间观测=observed_at_beijing`；每张事件卡必须可见显示
`首次公开（北京时间）=first_public_at_beijing` 和 `phase`。“事件新鲜度与A股定价状态”继续显示
`first_reaction_date/latest_confirmation_date`。不得只在 JSON 保存日期，也不得用A股反应日替代海外交易日或
事件首次公开时点。

顶部 A股走势映射先把 `scheduled_event_expectations` 与 `scheduled_event_reconciliations` 按 `event_id` 合并到同一卡片，
同时显示北京时间、事前一致预期/前值、基准/上下行情景、运行时状态、实际结果、相对预期、来源及 A股板块调用；
已到排期时点不得仍只显示“待观察”。随后独立显示：

```text
相对受益

• 半导体设备、存储（隔夜海外同业相对宽基走强；关键公司指引验证需求）

相对承压

• 航空（若原油由供给冲击上涨，燃油成本承压；油价回落或航油附加费改善即失效）
```

每个 bullet 同时显示预测窗口、置信度、来源链接、失效条件和机械派生的简短信号结论：
`本板块已考虑信号N条（驱动N/反向N/中介N）；关联五域N域；全局信号N条/五域5域均已处置，未解释0项`。
原因必须放在中文括号内；
不允许只有板块名，也不允许把全部原因塞入一段无法对应板块的总述。审计附录必须完整展开全信号处置台账，
顶部卡片只显示上述简短结论。

## 9. 候选股票双向下沉

每个候选继续在 `by_code` 占一项，并新增 `sector_context[]`：

```json
{
  "call_id": "sector-call-001",
  "relation": "direct_sector|customer|supplier|competitor|input_cost|overseas_revenue|sector_only",
  "direction": "beneficiary|pressure",
  "relevance": "direct|indirect|sector_only",
  "reason": "该股票为何实际承接或承压；不能只复制板块总述",
  "source_refs": ["sector_call来源的完整并集"],
  "invalidators": ["该股票级映射的失效条件"]
}
```

执行强制双向完整性：

1. 候选 `industry` 精确出现在 `industry_matches` 时，`sector_call.candidate_codes` 必须包含该候选；
2. 每个 `candidate_codes` 必须在对应 `by_code[code].sector_context` 唯一下沉同一 `call_id`；
3. 每个股票 context 必须反向找到 sector call，方向和来源必须一致；
4. 直接行业命中必须标 `direct_sector/direct`；间接映射必须标真实产业关系；
5. 最终HTML候选卡片必须可见显示板块、方向、关系、窗口、置信度、股票级原因、来源和失效条件；
6. 任一缺失、孤儿ID、模糊行业命中或HTML不可见都拒绝发布。

正面 context 使用独立紫色/中性信息块，不得伪装成红黄风险 alert；负面板块 context 也不自动改变裁定。
原 warning/delta 的红黄 alert 继续按毒月风险协议单独工作。

## 10. 冲突裁定与置信度

- `driver_signal_ids` 至少一条且必须 `fresh`；`opposing_signal_ids` 可为空。
- 有反向信号时写 `dominant_driver`，说明时点、直接性、意外程度或国内确认中的哪项占优。
- 同一板块在不同窗口可有不同方向，但必须使用不同 `call_id`，例如“开盘受益、盘中易回吐”。
- `high` 只用于来源强、映射直接且多个独立输入一致；单一海外个股异动通常不高于 `medium`。
- 来源受阻、行业映射宽泛、冲击原因未知或市场尚未开盘时降低置信度或不形成 call。
- 置信度不是上涨概率，不得换算成百分比。

## 11. 发布门禁与 shadow 验证

`validate-bottom-search` 和最终 `validate --require-bottom-search` 必须拒绝：

- 非v4、缺少五路市场发现或缺少八个行业族逐项覆盖；
- 美股只登记宽基、漏掉A股T日行业异动、重大异动未归因且未显式列入 unresolved；
- 重大事件缺新鲜度/A股定价状态，或 `priced_before_t/stale` 仍生成受益调用；
- `priced_on_t` 被写成全新催化、标高置信，或没有追高/回吐/失效提示；
- 缺少八类预测输入覆盖；
- 观测时点晚于检索时点、T/T后混账或陈旧信号充当主驱动；
- HTML 未声明 T 的A股/北京时间口径，异动卡缺原市场交易日/北京时间观测，或事件卡缺 phase、首次公开北京时间、
  已有的A股首次/最近反应日期；
- signal/query/source/runtime 引用不双向；
- `signal_disposition_ledger` 未精确覆盖全部信号、存在伪中介或 `unmapped_signal_ids` 非空；
- `risk_item_disposition_ledger` 未精确覆盖全部 warning/T后 delta，或 `unmapped_risk_item_ids` 非空；
- 已到排期时点仍为 `pending`、缺官方实际结果/逐项实际值，或顶部卡没有显示运行时实际对账；
- 板块缺原因、来源、主驱动、失效条件或合法窗口；
- `sector_call.considered_signal_ids` 未完整承接直接与中介信号；
- `sector_beneficiaries/pressures` 不是由 calls 派生；
- 提及候选所属行业却漏掉候选，或股票 context/HTML 未下沉；
- 把海外已观察涨幅写成A股预计涨幅、概率或目标点位；
- 任何打分、裁定、排序、仓位、买卖动作或熔断改动。

正式评估时分别记录申万行业的前收盘到开盘超额收益、开盘到收盘超额收益及未来1—5日超额收益，做滚动
样本外、信号族消融、Rank IC、Top/Bottom-K命中、覆盖率、误报率和机会成本。未完成预注册 shadow 样本前，
本协议不得升级为生产打分或交易 gate。
