# Agent③ 毒月 Web 风险预警协议（bottom-toxic-risk-warning/v2）

本协议把毒月研究接入日常扫描，但只产生 `shadow warning`：不修改量化分数、推荐线、排序、仓位、
✓/?/✗ 裁定或预算熔断。Agent③同时维护两本账：截至 T 的无前视风险 nowcast，以及截至本次实际检索
完成时点的最新五域评估。后一本账必须纳入 T 后公开信息并给出证据约束下的推断，但不得倒灌 T 日裁定。
不承诺预测尚未公开的黑天鹅或“下一个毒月”。

## 目录

1. 角色边界
2. 五个固定风险域
3. T 日与 T 后隔离
4. 完整检索与证据约束推断
5. warning 等级和来源门槛
6. 候选暴露映射与 HTML
7. 结构化 JSON 契约
8. 发布门禁

## 1. 角色边界

Agent③ 在每次 bottom 扫描中独立执行市场级 Web 检索：

- 识别未来已排期的波动窗口；
- 识别 T 日已公开且仍在演化的系统性/行业压力；
- 将市场风险映射到有明确暴露的过线候选；
- 从 T+1 搜索到实际运行时点，把新事实隔离为执行时点安全增量；
- 对每条 warning、每条 T 后 delta 和五个风险域分别输出事实、共识、基准情景、上下行情景、
  传导链、观察变量、失效条件、置信度和推断边界；
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

## 5. warning 等级和来源门槛

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

## 6. 候选暴露映射与 HTML

每个候选都在 `by_code` 中占一项：

- `none`：没有可核验的直接暴露；
- `watch`：关联的最高等级为 `med`；
- `high`：至少关联一条 `high`。

行业预警只有在候选行业、产品、成本或海外收入暴露能够明确对应时才下沉。市场恐慌不能机械复制成
所有候选的同一条个股红色警示。

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

HTML 必须单列“运行时点五域综合评估（最新公开信息；不倒灌 T 日裁定）”，显示每域检索完成时点、
最新采用来源日期、共识、基准情景、上下行情景、传导链、观察变量、失效条件和推断边界。

## 7. 结构化 JSON 契约

`bottom_adjudication.json.codex_audit` 增加：

```json
{
  "toxic_risk_warning": {
    "version": "bottom-toxic-risk-warning/v2",
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
        "phase": "as_of_t|post_t_safety"
      }
    ],
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
    "by_code": {
      "600000": {
        "exposure": "none|watch|high",
        "warning_ids": [],
        "post_t_delta_ids": [],
        "reason": "暴露映射理由"
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
    "clear_reason": "clear 时必须非空；说明检索边界，不得声称风险不存在"
  }
}
```

`overall_status` 机械重算：存在 `high` 为 `elevated`；否则有 `med` 或任何风险域 `blocked` 为 `watch`；
其余为 `clear`。`by_code.exposure` 同样按关联条目的最高等级重算。

## 8. 发布门禁

写完裁定文件后运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\data\bottom_latest.json" `
  --audit "C:\Trading_analysis\data\bottom_adjudication.json"
```

该命令同时验证 Agent② `bottom_search` 和 Agent③ `toxic_risk_warning`。Agent③缺五域运行时点评估、
缺事件推断、只写“方向不确定”、末端扫描未覆盖至实际运行日、无来源精确概率或发生 T 后倒灌时均失败。
任一失败都不得运行 `--adjudicate`。最终 `augment-report` 会生成 Agent③ 独立审计表，
`validate --require-bottom-search` 还会检查运行时点共识/基准情景、warning 来源链接和报告/个股 alert
文本确实出现在 HTML。不可变核心 HTML 中遗留的旧“61%”
集中窗口径由该附录强制校正为真实连续5个市场交易日的57.2%（严格10月）/55.3%（含边界月），
最终报告不得再引用旧数值。

Agent③ 在完成无前视 shadow 样本、误报率和机会成本评估前，不得升级为数值 gate、自动禁买、降级或仓位规则。
