# Agent③ 毒月 Web 风险预警协议（bottom-toxic-risk-warning/v1）

本协议把毒月研究接入日常扫描，但只产生 `shadow warning`：不修改量化分数、推荐线、排序、仓位、
✓/?/✗ 裁定或预算熔断。目标是截至 T 的风险 nowcast，不承诺预测尚未公开的黑天鹅或“下一个毒月”。

## 目录

1. 角色边界
2. 五个固定风险域
3. T 日与 T 后隔离
4. warning 等级和来源门槛
5. 候选暴露映射与 HTML
6. 结构化 JSON 契约
7. 发布门禁

## 1. 角色边界

Agent③ 在每次 bottom 扫描中独立执行市场级 Web 检索：

- 识别未来已排期的波动窗口；
- 识别 T 日已公开且仍在演化的系统性/行业压力；
- 将市场风险映射到有明确暴露的过线候选；
- 将 T 后新出现的风险隔离为执行时点安全增量；
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
`blocked` 与未命中分开，不能因来源打不开就写 clear。

## 3. T 日与 T 后隔离

- `cutoff_beijing` 固定为 `T 23:59:59+08:00`。
- `warnings[]` 只能引用 `phase=as_of_t` 且 `published_at<=T` 的来源。
- 已排期事件允许 `event_start>T`，但必须是 `scheduled + med + direction_certainty=uncertain`。
- 活跃事件必须 `event_start<=T`；已经结束的事件不得冒充当前风险。
- 若实际检索日晚于 T，五个风险域都必须再做一次 `post_t_safety` 末端扫描。
- T 后事件只进入 `post_t_safety_items[]`，并固定
  `used_in_asof_t_warning=false`；不得回填到 T 日 warning。

战争突然爆发、临时制裁名单、未公开财务造假等属于不可预知事件。只能在公开后作为 T 后安全增量，
不能以事后新闻声称 Agent③ 已事前发现。

## 4. warning 等级和来源门槛

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

预警描述必须写清“公开时间—仍在演化的事件—可能的传导链”，同时声明方向不确定性。
不能只写“外围不稳”“消息面偏空”等无法核验的泛化判断。

## 5. 候选暴露映射与 HTML

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

## 6. 结构化 JSON 契约

`bottom_adjudication.json.codex_audit` 增加：

```json
{
  "toxic_risk_warning": {
    "version": "bottom-toxic-risk-warning/v1",
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
        "used_in_asof_t_warning": false,
        "shadow": true
      }
    ],
    "clear_reason": "clear 时必须非空；说明检索边界，不得声称风险不存在"
  }
}
```

`overall_status` 机械重算：存在 `high` 为 `elevated`；否则有 `med` 或任何风险域 `blocked` 为 `watch`；
其余为 `clear`。`by_code.exposure` 同样按关联条目的最高等级重算。

## 7. 发布门禁

写完裁定文件后运行：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\data\bottom_latest.json" `
  --audit "C:\Trading_analysis\data\bottom_adjudication.json"
```

该命令现在同时验证 Agent② `bottom_search` 和 Agent③ `toxic_risk_warning`。任一失败都不得运行
`--adjudicate`。最终 `augment-report` 会生成 Agent③ 独立审计表，`validate --require-bottom-search`
还会检查 warning 来源链接和报告/个股 alert 文本确实出现在 HTML。不可变核心 HTML 中遗留的旧“61%”
集中窗口径由该附录强制校正为真实连续5个市场交易日的57.2%（严格10月）/55.3%（含边界月），
最终报告不得再引用旧数值。

Agent③ 在完成无前视 shadow 样本、误报率和机会成本评估前，不得升级为数值 gate、自动禁买、降级或仓位规则。
