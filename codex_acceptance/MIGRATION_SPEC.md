# 三个交易 Skill 的 Codex 迁移规格与不可变清单

## 1. 范围与安装

唯一真相源：
`C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc`

该目录只含三个 skill 子目录：

1. `bottom-fishing`
2. `stock-diagnostic`
3. `weekly-ashare-rank`

共享启动、验价与验收工具放在上一层：
`C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance`，不得被安装器识别为 skill。

Codex 工作区安装位置：`C:\Trading_analysis\.agents\skills`。三个目标均为指向唯一真相源的 junction。
新对话从 `/skills` 选择，或显式输入 `$bottom-fishing`、`$stock-diagnostic`、`$weekly-ashare-rank`。

## 2. 不可变与合法可变边界

以下九个业务文件由 `baseline_manifest.json` 锁定 SHA-256；迁移层不得改动其量化算法、阈值、数据源顺序或 renderer：

- `bottom-fishing/bottom_fishing.py`
- `stock-diagnostic/stock_diagnostic.py`
- `stock-diagnostic/make_diag_report.py`
- `stock-diagnostic/recheck_diag.py`
- `weekly-ashare-rank/ashare_weekly_rank.py`
- `weekly-ashare-rank/market_gate.py`
- `weekly-ashare-rank/review.py`
- `weekly-ashare-rank/recheck.py`
- `weekly-ashare-rank/make_report.py`

bottom-fishing 生产状态迁入唯一真相源时，唯一获准的核心文件字节变化是
`bottom_fishing.py` 的 `DATA` 根目录从外部 `C:\Trading_analysis\data` 改为相对的 `HERE/state`；
量化算法、阈值、数据源顺序和 renderer 均未变化，manifest 已按该路径迁移后的字节重新锁定。

两份 `weights.json` 是原引擎会自动更新的合法运行状态，不能锁死字节。验收器锁定其 schema：

- `fwd_days` 为 1–60 的整数；
- `weights` 必须且只能含 `mom/vol/tech/tape/pull`，每项在 0–2；
- `shrink` 在 0–1；
- 必须有合法 `generated_at` 与非空 IC 明细。

业务中性的回归忽略白名单仅含：生成时间、北京时间戳、裁定/复盘时间、验证生成时间、报告路径与文件名时间戳。

## 3. 共同输入、数据源、缓存与证据

只允许原项目免费公开行情源：东方财富、腾讯、新浪、雅虎，以及原引擎已有的免费交易日历/F10接口。
不调用付费 LLM API、付费行情 API、MCP 或外部 agent。

`run_engine.py` 把 weekly 数据客户端的用户主目录缓存重定向到
`C:\Trading_analysis\data\cache\ashare_weekly`，并把 bottom-fishing 的四个生产状态文件重定向到唯一真相源内的
`claude_code_Trading_skill_no_API_Doc/bottom-fishing/state/`。两项都只改变落盘位置，不改业务计算；
stock-diagnostic 与 weekly-ashare-rank 的正式状态路径保持不变。
若本机已安装的可选 `pyarrow.compute` 因 DLL/应用控制策略无法导入，启动层将其按“未安装”处理；三个生产
引擎不使用 Arrow I/O，因此该兼容只解除 pandas 的可选依赖探测，不改行情、因子或 renderer。显式 Parquet
研究脚本不走此兼容层。

最终人工裁定必须含 `codex_audit`，契约见 `JUDGE_SCHEMA.md`：

- 事实、来源名、URL、发布日期、北京时间检索日期、事件日期、来源等级；
- F10 confirmed/conflict/missing/not_applicable 比对；
- rubric 加减分、推理、最强反方解释、结论、未确定项；
- 一次反方挑战和一次审计官机械复核；
- 单源、日期不符、偏差大或缓存过期不得标“已验证”；weekly 在多源彼此一致后，还必须校验引擎 `close`
  与多源中位数偏差≤0.5%，超限即按脏缓存要求 `--refresh` 整次重跑。

最终 HTML 由原 renderer 生成，再非侵入式追加 Codex 审计附录；bottom 初扫产物不含 ETF 字段，另在 Agent②/③
完成后的裁定版原 renderer 结束后给候选卡片
F10 下方附加只读 ETF 持仓/走势相似度区块。原卡片、字段与排序不删不改。

## 4. bottom-fishing 不可变项

输入：无参数；模式为扫描、`--adjudicate`、`--review`。

量化与 gate：

- 底部区：距 60 日高点回撤至少 20%，60 日位置不高于 25；
- 权重：防守日 +8.6、站回 MA10 +5.2、DIF 三日向上 +4.5、RSV 回升 20–40 +3.9、
  回撤 30–45% +3.7、站回 MA5 +3.7、低开 2% 收回 +4.4、RSV≤15 −7.4、至少四连阴 −6.3、
  20 日涨停基因 −5.4、ATR≥7 −3.5、刚创新低 −3.1；
- 双路径：防守日总分≥18；非防守日个股分≥15；两条路径均要求 ATR≤4；
- 同票五个交易日旋转门冷却；
- `dd250≤−50%` 只做长期深跌标注，不加分；
- 单票仓位上限 3.5%，T+1 开盘，高开>3%放弃，止损 −8%，+5%落袋一半、+10%清，最长 20 个交易日；
- 买入日收盘≤入场价−5%时次日开盘退出；月度亏损预算与滚动停做开关保留。

人工裁定：严格区分基本面恶化硬否决、事件型红旗、系统性踩踏；旧闻或年份不明不得否决。
裁定按 `✓ > ? > ✗` 分层，层内保持引擎分数降序。非 `✓` 票不得保留买入、止损或目标价位。

Agent③市场预警：每次扫描固定覆盖排期宏观政策、国内监管与流动性、海外地缘与贸易、跨资产压力、
长假信息缺口五域，检索到本次实际运行时点，对每条事件及五域综合输出证据约束下的共识、基准/条件情景、
传导链、观察变量和失效条件，再合并映射A股下一交易日和未来1—5日的大概走势、风格与板块结构；
按 T 日与 T 后分账写入 `codex_audit.toxic_risk_warning`。顶部白话卡片置于“市况”正下方。当前只允许
`mode=shadow`；warning 可进入报告级/个股级 alerts，但不得改分、裁定、仓位、推荐线或两个预算熔断。

Git 跟踪的生产状态固定为 `bottom-fishing/state/` 下的 `bottom_latest.json`、
`bottom_adjudication.json`、`bottom_shadow_log.jsonl`、`codex_price_verification.json`；reports HTML 仍由
`bottom-fishing/reports/` 管理。共享行情、日历与 ETF 刷新副本仍留在外部缓存目录，不属于持久状态。
`--review` 的影子样本、裁定子集、滚动停做统计由原引擎执行。

HTML 必须呈现 T、市况/防守天数、大盘 RSV、底部区数量、阈值/命中、冷却、dd250、F10、裁定层、
Agent③ shadow alerts、五域风险审计、执行纪律、影子期/真实口径和非投资建议。每张候选卡片还必须在 F10 后、
操作计划前显示 `bottom-etf-holdings/v1` 只读区块（原卡片无 F10 行时使用同一信息槽）：最近完整公开报告期的场内 ETF 持仓名单，以及截至 T 最近
60个共同交易日前复权日收益 Pearson 相关排序（同分以路径 RMSE 升序）；HTML 只显示最相关前5只且不提供
全部展开表，完整披露名单只留在 JSON。结构化字段固定
`used_in_recommendation=false`；ETF 请求失败只允许信息块降级，不得改变或阻断任何 Agent、候选、分数、裁定、
排序、价位、仓位、冷却或熔断。

## 5. stock-diagnostic 不可变项

输入：六位代码必填；成本可选；`hold-days` 默认 20；权重模式保持原引擎。

六角色：①技术量价、②基本面财务、③中英文消息催化、④资金板块大盘、⑤风险挑战/裁决、⑥机械复核。

评分：

- ① = 引擎 stance×0.85 + 主观校正，校正在 −10~+10 且必须说明理由；
- ②从 50 起：估值 ±10、业绩趋势 ±15、质地 ±8、机构目标价 ±7；预亏/暴雷/审计非标/ST风险封顶30；
- ③从 50 起：催化强度与时效 ±20、最大利空 ±15、price-in 0~−10；立案/重大违规/退市风险封顶30；
- ④从 50 起：主力资金 ±12、两融 ±8、板块 ±10、同行 ±10、大盘 ±5；新浪资金口径只看方向且幅度减半；
- ⑤ = 引擎技术风险 + F10事件风险 + 主观风险逐项，上限100；
- 综合多空分 = ①×0.28 + ②×0.22 + ③×0.22 + ④×0.28；
- 最终分 = 综合多空分 − ⑤×0.15 + 大盘调整(−5~+5)。

硬 gate：预亏/暴雷、立案、退市或ST风险、审计非标、30天内解禁≥流通5%、质押≥60%且接近平仓线。
命中后最终分封顶40、置信度低、动作至少减仓。

置信度按原顺序：硬 gate→低；最终≥75且四维同向→高；最终≥60且至少三维同向→中高；
45–59或分歧>30→中；其余低/规避。

操作矩阵、成本盈亏/解套/摊薄、支撑/压力/加仓/止盈、T+N 到期行全部保留。
R:R<1.5 禁止现价加仓；止损要求至少 1.2×ATR 且在结构支撑下；深套+下跌+资金流出禁止补仓。

Agent⑥必须机械检查数据新鲜度、跨源价格、算术、rubric、价位、引用、资金流口径和上次诊断可比性。
②③④每个分项从50起展示 URL 与日期；①和⑤也必须结构化重算。

## 6. weekly-ashare-rank 不可变项

输入：持有天数 N、板块、top；契约默认 N=5，原引擎默认 pool=400/top=20。只有 SKILL 的稀缺候选
安全阀可显式扩到 top=30，不得继续无界扩池；最终 JSON/HTML 只留终排前8。

Agent⓪：环境分从50起，只用上证/创业板 MA20、当日涨跌、放量下跌、连跌，以及涨停、炸板、跌停、
连板、涨停收缩、情绪高潮规则；0–39观望/15%总仓，40–54防守/30%，55–69中性/50%，70–100进攻/60%。
40/55/70 上方 3 分临界带按低一档执行。

因子：

- 动量：ret5 截断 −5~12 映射18分，ret20 截断 −15~25 映射17分；
- 量能：量比高于1部分映射15分，当日量映射10分；
- 技术：多头12、MACD金叉7、距MA10在−2~6为6；
- 盘口：尾盘强度×8、阳线4、实体比>0.4为3；回调10；
- 过热惩罚、风险分、事件调整、主力流出/兑现带/长上影扣分与 rank_score 均由原引擎执行并由验收器重算。

必须保留：全市场筛选、个人可交易池仅 `00*`/`30*`/`60*`（排除科创 `68*` 与北交 `8*`/`4*`）、
IC/Top-K验证、P1/P3/P7/P9/P11/P12/P13/P14、跨源价格、≤7天催化、非进攻档终排≤3+新鲜催化
双门槛、观望不给价位、T+1 recheck 与 review。

P1 置信度上限：|IC|<0.02 中低，0.02–0.04 中，≥0.04且验证通过才可中高；只有 IC≥0.06、
超额>1%、胜率>55%且验证通过才可高。

P3：至少两个源且最大偏差≤1%才是一致；单源/偏差大不得标✓。即使多源彼此一致，引擎 `close` 相对
多源中位数偏差>0.5%仍判 stale-cache，必须 `--refresh`，不得手改 JSON 绕过。
P13：非进攻档可买票必须终排前三且有≤7天可核日期的正面催化；观望档文字与HTML均不给操作价位。
P14：稳健线要求无弱信号、ATR≤4、5日涨幅≤10%、量比≤2、60日位≤85；首选最多两只且行业不同，
并保留买入日崩盘快刀和赢家延展规则。

最终 `candidates[]` 物理顺序必须等于文字 `final_codes` 和 HTML 卡片顺序；每只 risk_note 必须显示
`✓已验证` 或 `⚠`，状态徽标、市场横幅、策略验证与警示必须一致。

## 7. 阻断规则

`--no-verify` 和 `--no-notices` 在原引擎中为兼容参数，但 Codex 薄启动器机械拒绝它们。
最终验收命令没有降级开关。baseline、原始引擎验收、证据/公式/gate、HTML 任一非零退出，结论必须写“未通过”，
不得向用户发布该 HTML 为最终报告。
