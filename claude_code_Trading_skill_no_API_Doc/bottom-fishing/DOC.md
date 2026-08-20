# bottom-fishing — 完整文档

## 🚀 怎么调用（样板 wording）
```
抄底                      # T日扫描: 市况→底部区→打分→推荐线→HTML
超跌扫描 / 底部扫描 / bottom
抄底复盘 / 对账             # --review: 影子日志逐笔结算+滚动停做开关
```
**无参数设计**：阈值是走样本定版（防守日总分≥18 ∥ 非防守日个股分≥15，均 ATR≤4），
不开放调参——任何改动必须先过面板走样本（见 README 研究链与"已否决清单"）。

## 文件结构
```
bottom-fishing/
├─ bottom_fishing.py     # 引擎: 扫描/打分/推荐线/执行方案/F10种子/影子日志/--review/HTML
├─ SKILL.md              # Codex skill 定义（安装到工作区 .agents/skills/bottom-fishing/）
├─ references/
│  ├─ WEB_EVIDENCE_PROTOCOL.md # 六维检索/官方源血缘/F10逐条对账/T后安全增量
│  ├─ TOXIC_RISK_WARNING_PROTOCOL.md # Agent③五域风险/T与T后隔离/HTML shadow warning
│  ├─ AGENT3_SECTOR_MAPPING_PROTOCOL.md # 五路发现/八行业族/事件兑现审计/A股分窗口映射
│  └─ RESEARCH_LEDGER.md       # 规则→脚本→数据→偏差→采纳/否决 provenance
├─ scripts/research/
│  ├─ legacy_cc/          # 从 Claude Code scratchpad 原样抢救的历史实验
│  ├─ bottom_ml/          # CatBoost/purged-CV 源码；大 parquet 仍在外部数据目录
│  ├─ precrash_kline_study/ # 暴雷前10—150日轨迹、同日对照与季度前推OOS
│  ├─ holiday_event_study/ # 2024—2026节假日前后事件研究
│  ├─ toxic_month_web_study/ # 毒月真实集中窗、事件账本与预警设计
│  └─ board30_split_study/ # 30* 20%板独立profile预注册/A-B/holdout（shadow-only）
├─ README.md             # 研究存档: 全部方法论数字/否决清单/毒月专项
├─ DOC.md                # 本文件(操作文档)
├─ state/                # Git跟踪的生产状态（标准workflow唯一读写位置）
│  ├─ bottom_latest.json        # 最新一期结果(供回填/复查)
│  ├─ bottom_adjudication.json  # T日裁定、Agent③预警、搜索审计和结构化证据
│  ├─ bottom_shadow_log.jsonl   # 影子日志、冷却依据和复盘结果
│  └─ codex_price_verification.json # 独立跨源验价留痕，发布时并入裁定审计
└─ reports/              # HTML报告(gitignore, bottom_cn_完整北京时间戳[_裁定版].html)
```

共享行情、交易日历与 ETF 在线刷新副本继续位于
`C:\Trading_analysis\data\cache\ashare_weekly`；它们可重建、不进 Git，也不属于上述持久状态。

## 引擎命令行
```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom --            # 扫描
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom -- --review   # 对账
```

## 数据流水线
东财快照(经weekly引擎get_spot,带缓存/新浪兜底) → 剔ST/科创/北交 → 腾讯qfq日K(140根/只) →
底部区(回撤≥20%+60位≤25) → 修复确认打分 → 双路径推荐线 → F10种子(东财datacenter,仅过线票) →
Agent②个股六维裁定 + Agent③五路全市场发现/八行业族覆盖/五域风险/重大事件归因与兑现审计
→ 八类预测输入与A股分窗口映射 → 裁定/预警HTML → 执行方案(权威交易日历给真实买入/离场日期)
→ stdout+JSON+HTML+影子日志 → Agent②/③完成后，仅在裁定版附加候选股 ETF 持仓/走势相似度只读区块。
指数: 创业板(防守日/def_days/大盘RSV, 后两者为影子字段不进规则)。

报告文件名严格使用 `bottom_cn_YYYY-MM-DD_HH-MM-SS[_裁定版].html`：日期与时分秒均来自同一个
UTC+8 的 `generated_at` / `adjudicated_at`。业务截止日 T 只写入正文与 JSON，不再与生成时钟混拼。

## ETF 持仓与走势相似度（报告只读层）

- 作用阶段：初扫 JSON/HTML 不含 ETF 信息，不给 Agent②/③读取；只有完成裁定后的最终候选卡片才附加。
- 位置：每只最终候选卡片的 F10 行下方、操作计划上方；若 F10 源失败而原卡片没有 F10 行，则放在同一信息槽、
  仍位于操作计划前。HTML 只展示最相关前5只且不提供全部展开表，观察池不请求、不展示。
- 持仓口径：东方财富公开机构持仓反查中的最近完整基金报告期，只保留有沪深场内代码且名称/类型可确认的 ETF，
  排除 ETF 联接基金；全部已识别名单只保留在结构化 JSON 供审计。这里的“持有”是定期披露口径，不是基金盘中实时仓位；一/三季报可能
  只披露主要持仓，较新但尚在披露中的报告期只提示而不混用。
- 走势排序：先按该股票占 ETF 净值比例选前80只计算；截至引擎 T 日取最近60个共同交易日，以前复权收盘的
  日对数收益 Pearson 相关系数降序，相关相同再按归一化价格路径 RMSE 升序。HTML 只显示前5只；未进入80只
  上限的 ETF 仍保留在 JSON 完整名单并标记未计算。
- 隔离：增强层在 Agent②/③完成且原 renderer 写完裁定版 JSON/HTML 后运行，结构化字段为 `etf_holdings_meta` 与
  `candidates[].etf_holdings`，固定 `used_in_recommendation=false`。它不属于 Agent①/②/③ 的输入，不改变任何
  推荐、裁定或风险控制；请求失败只令本区块显示不可用。
- 缓存：`C:\Trading_analysis\data\cache\ashare_weekly\bottom_etf`；完整报告期元数据6小时、持仓名单7天，
  日线按目标 T 是否已覆盖决定复用。公开持仓来源页：`https://data.eastmoney.com/zlsj/jj.html`。

## 角色分工（LLM侧, 见SKILL.md）
- Agent①=引擎(全自动)。
- **Agent②=错杀裁定官(人工信息唯一有增量的位置)**: 按「红旗分型」判(2026-07-16定)——
  **恶化型**(业绩/债务/治理)=强否决✗；**事件型**(减持/质押/解禁)单独≈无区分力、结合恶化或急性大比例才否；
  + **六维多轮搜索**(业绩经营/财务信用/治理监管/资本事件/国内行业/海外驱动)，优先回溯官方原文；
  逐条对账全部 F10 种子并按 `origin_id` 去重转载。裁定只认 T 日已公开信息，T 后更新单列安全增量且只能维持或降级。
  每类查询、未命中、受阻、采用事实和来源血缘都写入 `codex_audit.bottom_search`，由独立门禁机械验收。
  详见 SKILL step2 与 `references/WEB_EVIDENCE_PROTOCOL.md`。
  **取证护栏×2**(2026-07-17加, 源自宁德时代实盘裁定, 非判据改动): ①**F10种子会过期**——`f10_flag`只看
  `forecast.type`不看新鲜度, 必核 `notice_date`/`fresh` 并与 `kcfj_yoy`/最新季报交叉验证, 陈旧或矛盾=误报勿否决;
②**旧闻污染**——网页检索会把多年前旧文与当期新闻混排且摘要常不带年份, 每条红旗核到"年"再采信, 核不出不采信
  (旧闻致**错误否决**, 比误给✓更隐蔽)。详见 README §Agent②取证护栏。
- **Agent③=全市场主线发现与风险映射官（v4；每次扫描必跑，零候选也不跳过）**：
  五域风险只是最低覆盖。上游先完成 `us_sector_tape/global_movers/ashare_t_day_sector_tape/asia_sector_tape/
  event_first_scan` 五路发现，并逐项覆盖医疗生科、TMT、消费、金融地产、能源材料、工业军工运输、公用事业新能源、
  宽基风格八个行业族；重大异动必须有事件归因或显式 `unresolved`，A股 T 日行业异动不得缺席。
  每个重大事件都要审计新鲜度、A股首次/最近反应日和兑现状态：`priced_before_t/stale` 只留审计且不得产生受益调用；
  `priced_on_t` 只能作为低/中置信延续观察，并提示追高、回吐和失效风险。随后覆盖八类外盘/宏观/政策预测输入与
  排期事件预期—实际对账，再分别给出竞价/开盘、日内延续或回吐及未来1—5日的A股条件式板块映射。
  T 固定指A股信号交易日并按北京时间解释，T/T后以 `T 23:59:59+08:00` 为界；HTML 异动卡分别显示原市场交易日
  和北京时间观测，事件卡显示首次公开北京时间与阶段，A股定价行另显示首次/最近反应日，三类日期不得互相替代。
  五域仍固定搜索排期宏观政策、国内监管与流动性、海外地缘与贸易、跨资产压力、长假信息缺口；T 后新突发只进
  安全增量和运行时点评估，HTML 标“T后”，不得倒灌 T 日裁定。全部信号、风险项和五域必须逐项处置，未解释项为0；
  只有行业/产品/成本/海外收入暴露明确对应时才下沉到候选股。当前固定 `mode=shadow`，不改分、不禁买、不影响
  Agent② 裁定。结构化契约为 `bottom-toxic-risk-warning/v4`，详见
  `references/TOXIC_RISK_WARNING_PROTOCOL.md` 与 `references/AGENT3_SECTOR_MAPPING_PROTOCOL.md`。
- Agent④=复核官: 价格新鲜度/来源可追溯/口径一致。

裁定文件写完后，以下命令同时验 Agent② 和 Agent③，失败时不得生成裁定版：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_latest.json" `
  --audit "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\state\bottom_adjudication.json"
```

## 硬纪律速查（数字出处=README）
| 条款 | 内容 |
|---|---|
| 0过线=空手 | 不降格凑数(历史约6成日子无票) |
| 仓位 | 单票≤3.5%(跌停封死风险>动量票) |
| 执行三刀 | T+1开盘进场(高开>3%放弃) / -8%条件单成交即挂 / 买入日收≤-5%次日开盘出 |
| 目标 | +5%落袋半仓 / +10%清(EV最优) / 最晚T+20收盘离场 |
| 毒月熔断 | 月度亏损-3%停做 ∥ 近20笔雷率≥30%停(--review自动检测) |
| Agent③预警 | 五路发现+八行业族+五域+八类输入；陈旧/已兑现 flag，分窗口映射A股；顶部shadow卡片，不改变分数/裁定/仓位 |
| ETF信息块 | 最近完整披露期持仓 + 截至T的60日收益相关排序；F10下只读展示，不进入三个Agent或交易规则 |
| 口径引用 | 胜率必须带"走样本75.2~77.8%·月度45~92%大摆·2024式熊市全年EV为负"全套披露 |
| 影子期 | 累计30笔了结前: 纸面跟踪或仓位减半 |

## 与 weekly-ashare-rank 的分工
进攻/中性市 → weekly(动量, 短拿2-3天)；防守市 → bottom(修复超跌, 长拿10-20天)。
两边同时持仓时总仓合并计算, 服从 weekly 的 Agent⓪ 总仓闸门。

## 维护规则
- **改阈值/加因子 = 先面板走样本**(README已否决清单: 资金流/学习权重/K线形态/MA250牛熊闸门/
  已兑现降级/"拿久等回来"——不要再试)。
- 影子候选(强格子未进规则, 随影子日志复验): 防守持续≥9天、大盘RSV∈[15,40]。
- 30笔了结后 --review 与回测口径(75.2%/13.9%)偏离>10pp → 回研究台重校准。
- **研究结论摘要进 README，复现实验与 provenance 进 `references/RESEARCH_LEDGER.md`**；日常扫描不得执行
  `scripts/research/`。最新：2026-08-09 完成 `30*` 20%涨跌幅独立profile研究，点估计只达到未来shadow讨论门槛，
  因holdout月簇CI跨0、月度反向和ATR上边界而**暂不进入生产**；现行全市场统一阈值、workflow和日志均不变。
