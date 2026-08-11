---
name: bottom-fishing
description: A股底部区与超跌修复扫描（0 API、Codex 原生）。用于抄底、超跌、底部扫描、低吸扫描、bottom-fishing、毒月风险预警、错杀裁定、抄底复盘等请求；运行不可变 Python 引擎，执行双路径推荐线、ATR gate、5交易日冷却、官方源优先的多轮网页检索、F10逐条对账、Agent③五域最新风险检索、重大排期事件完整预期、外盘/宏观预测输入与分窗口A股板块映射、候选股票信息下沉、T日证据与运行时点增量隔离、结构化搜索覆盖审计、✓/?/✗分层、shadow warning、影子日志、adjudicate/review，并在一次性自修复与独立验收通过后输出原生 HTML。
---

# A股抄底扫描（0 API · Codex 原生 · 影子复验期）

以最新已收盘日 T 为基准，扫描全市场底部区（距60日高点回撤≥20%且60日位≤25），用**修复确认打分**
（2026-07-14 用 476股×401日≈19万股票日面板校准，全部走样本验证）选出"最有希望抄底成功"的票。
**无参数**——用户说"抄底/超跌扫描/bottom"直接跑即可。与 weekly-ashare-rank 互补：动量吃进攻日，本 skill 的
edge 集中在**防守日**（信号96%来自防守期；⚠“防守≥9天=82%胜最强格子”已被含2024扩展面板证伪——
全期EV-0.41，def_days/idx_rsv 仅作影子字段记录，**不作预期依据**，2026-07-15定）。

## 触发词
抄底 / 超跌 / 底部扫描 / bottom / 接飞刀(纠正用) / 低吸扫描

Codex 新对话中输入 `/skills` 后选择 `bottom-fishing`，或直接输入 `$bottom-fishing`。

## 执行步骤（一句话就跑完）

0. **先跑不可变基线门禁**。失败即停止，不得生成或宣称最终报告：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" baseline
   ```
   人工裁定必须遵守 `C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\JUDGE_SCHEMA.md`，只用 Codex 网页检索，
   每个关键事实保留 URL、发布日期和北京时间检索日期；不调用 MCP、付费 API 或外部 agent。出现过线票时，裁定前还必须
   **完整阅读** `references/WEB_EVIDENCE_PROTOCOL.md`；无论是否有过线票，都必须完整阅读
   `references/TOXIC_RISK_WARNING_PROTOCOL.md` 和 `references/AGENT3_SECTOR_MAPPING_PROTOCOL.md` 并运行 Agent③，
   不得凭摘要或单轮泛搜跳过覆盖、血缘、外盘会话新鲜度、板块调用、候选下沉与时点门禁。
1. **跑引擎**（PowerShell；从第一次请求开始使用可访问公开行情的网络执行；拉取约480只K线通常需要数分钟，网络慢时可能超过10分钟）：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom --
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-engine --json "C:\Trading_analysis\data\bottom_latest.json" --html "<引擎stdout给出的原始HTML绝对路径>"
   ```
   **长任务等待与防重跑纪律（强制）**：引擎会串行拉取大量免费行情，长时间无新 stdout、调用层超时或暂时没有返回码，
   都不等于引擎已经失败。若执行工具返回可继续等待的任务/cell ID，必须沿用该 ID 分段等待并向用户报告进度，禁止另开第二个
   `run_engine.py bottom`。若调用看似超时，先检查是否仍有对应 Python 进程，并核对 `bottom_latest.json` 与当日原始 HTML 的
   修改时间、交易日 T、文件大小是否仍在更新；进程仍在、产物仍在更新或尚未超过合理拉取窗口时，继续等待。只有在
   **确认原进程已退出**、**没有生成可通过 `validate-bottom-engine` 的新 T 日产物**，且日志明确失败或产物停止更新后，才允许重跑。
   检测到两个产物时先按 T、候选内容和哈希/逐行差异去重：内容相同则保留与 `bottom_latest.json` 时间一致的最新完整产物，
   并检查 `bottom_shadow_log.jsonl` 是否因重叠执行产生相同 `T+code` 的重复行；仅在整行关键字段一致时保留一条，冷却票也按同样
   规则核对，不得误删不同交易日或不同状态的记录。不得把重复产物误当成两次独立扫描结果。等待不是空转，而是避免两个联网
   引擎并发覆盖同一个 `bottom_latest.json`、重复追加影子样本、制造重复 HTML、浪费网络请求并让后续裁定绑定到错误版本的必要步骤。
   第二条非零退出立即停止，不能进入消息面裁定。
   引擎自动：判市况（防守日/def_days/大盘RSV）→ 底部区扫描 → 打分 → **双路径推荐线**
   （防守日·总分≥18 ∥ 非防守日·个股分≥15，均须 ATR≤4）→ **旋转门冷却**（同票**5交易日**内重复过线
   自动降观察池；2026-07-15敏感性 N=0/3/5/7/10/15：N=3~15为平坦高原、精华在前3天，N=5定版=
   全期67.1胜/26.2雷/EV+1.26 vs 无冷却62.6/30.0/+0.73，信号量比N=10多24%；冷却票仍记影子日志
   cooldown=true 供--review对照）→ **dd250≤-50长期深跌卡片标注**（2025-26面板89.8胜/10.2雷但n=59
   且无2024检验·仅标注不打分）→ 过线票执行方案 → 影子日志 →
   **自动生成 ranked HTML**（`reports/bottom_cn_YYYY-MM-DD_HH-MM-SS.html`）→ bottom_latest.json。
   文件名中的日期和时分秒必须来自同一个完整北京时间 `generated_at`（UTC+8），严禁再用交易日 T 拼日期、
   再用北京时间拼时分秒；T 只在报告正文和 JSON 中作为“最新已收盘交易日”展示，不进入生成时间戳文件名。
   初扫 JSON/HTML 是 Agent②/③ 的输入，固定不含 ETF 字段。只有 Agent②/③完成并生成**裁定版**时，Codex wrapper
   才在原 renderer 完成后给每张候选卡片的 **F10 下方**附加
   “ETF持仓与走势相似度”只读区块：用最近完整公开基金报告期反查持有该股的场内 ETF，结构化 JSON 保留全部披露名单；
   再对按持仓占净值排序的前80只 ETF，按截至 T 的最近60个共同交易日前复权日收益 Pearson 相关系数降序，
   同分按归一化路径 RMSE 升序，HTML 只展示最相关前5只且不提供全部展开表。基金定期披露不等于实时仓位，
   一/三季报可能不是全部持仓；较新但尚未完整的
   报告期只提示、不混入名单。该区块固定 `used_in_recommendation=false`，在 Agent①/②/③及原 renderer 之后运行，
    不改候选、分数、✓/?/✗、排序、价位、仓位、冷却或熔断。每次裁定必须在线确认最新完整披露期并在线刷新持仓；
    主持仓端点失败时依次切换三个 DataCenter 备用端点，腾讯三行情端点失败时切换东方财富行情，不使用过期持仓缓存兜底。
    裁定版出现 `blocked/partial/数据获取失败` 时禁止发布并由自动发布器重试。
2. **Agent②「错杀裁定官」（我做，只对过线票，通常0-3只——个股消息面的人工增量位置；毒月研究证明
   输赢家量化特征完全相同，"为什么跌"只能靠消息面）**：按下面「红旗分型」判——核心不是"有没有F10红旗"，而是
   **"基本面是否在恶化"**（2026-07-16 毒月8v8消息面对照实证：⚠事件型红旗单独出现≈无区分力、恶化型才是毒月雷真
   源头，见README§CatBoost选股+毒月消息面对照双实验2026-07-16，n=16定性待面板量化）：
   - **搜索覆盖硬门禁（v1）**：每票必须完成 `业绩经营/财务信用/治理监管/资本事件/国内行业/海外驱动` 六类查询，
     另做官方公告回扫、T日跌因、代码/全称/简称精确标题回溯；有H股或重要海外业务时补HKEXnews、英文名、海外监管和子公司。
     每类都要在 `codex_audit.bottom_search` 留 `hit/no_relevant_hit/blocked`、查询语句、时间窗、审阅URL与采用的
     `fact_id`。`blocked` 或会改变结论的日期冲突/单一聚合源，不得给 ✓。
   - **来源血缘硬门禁**：官方原文/PDF > 官方机构或公司IR > 主流媒体 > 聚合镜像。搜索摘要只用于发现；镜像必须回溯
     `canonical_url` 和 `origin_id`，同一公告的多个转载只算一个来源。决定性 ✓/✗ 证据不能来自
     `unverified_secondary`。
   - **两个“最新”必须分账**：主事实和 `base_verdict_asof_t` 只认 `published_at≤T`；检索从T+1更新到实际完成时点，
     T后内容只进 `post_t_safety_by_code`，不得混入主事实。T后利好不能升级；确认或未决的重大负面只能维持、上限降?或
     上限降✗，并同时保存 `effective_verdict`。最终报告必须同时显示T日裁定与检索时点有效裁定。
   - **F10必须逐条而非按日期对账**：`forecast` 与 `notices[]` 每一条都有唯一 seed key；同日四条公告也要四条 ledger。
     T后种子必须 `quarantined_post_t`，无日期种子必须 `quarantined_undated`，都不得进入主事实。
   ① 先读引擎附的 **F10客观种子**（`kcfj_yoy`/`pe`/`forecast`/近14天公告/`f10_flag`，卡片已渲染）。
      ⚠**种子会过期·`f10_flag`常是误报**：东财取的是"该股**最新一条**业绩预告、**无年龄下限**"（公司只在大变动时才发预告，
      平稳者可一两年不发→最新一条可以任意老），而 flag 只看 `forecast.type`(预亏/预减/略减)**不看新鲜度**——这是**故意的**
      （加 `fresh` 门槛会让20天前的预亏不亮=漏报，比误报危险；flag 是提示器不是 gate）。
      2026-07-17 起 **flag 文本已直出年龄**，形如
      `⚠F10负面待裁定(预告略减·2025-01-21·541天前·⚠陈旧,须与扣非同比/最新季报交叉验证,勿据此否决)`——
      **见到"·N天前"就必须核**：与 `kcfj_yoy`/最新季报交叉验证，陈旧或与最新报表矛盾 = **误报，勿据此否决**
      （2026-07-16 宁德时代实例：flag 源=2025-01-21"略减"·讲的是**2024年营收**·距T 541天，
      而同一JSON里 `kcfj_yoy`=+52.95%、2026Q1营收+52.45%/净利+48.52%，方向完全相反）。
      **无"·N天前"标注 = 由公告侧(减持/质押/解禁/立案)点亮**，那侧本就限近期、无年龄问题。
      反向亦然：**基本面强劲不加分**（不进技术打分，不造未经走样本校准的新权重）。
   ② **强否决靶（基本面恶化/治理崩塌 = ✗，输家4/8 vs 赢家0/8 强区分力）**——优先专搜命中即否：
      · 业绩：预亏/预减、业绩快报同比大降、季报净利/毛利率骤降、销量/订单/产量断崖、大额减值/商誉暴雷；
      · 财务：债务违约/评级下调/资产负债率飙升/经营现金流持续为负；
      · 治理：立案/留置、问询函、审计保留意见、*ST、大额诉讼执行、会计差错更正。
      命中且"仍在进行/未出尽"= ✗恶化否决（戴维斯双杀、行业逻辑破坏、政策转向同此）。
   ③ **事件型红旗（减持/解禁/质押/定增·H股摊薄）单独存在区分力弱**（毒月赢家亦~4/8命中，A股遍地都是）：
      **不再机械一票否决**，升alert并三问——(a)是否叠加②的恶化项？叠加=✗；(b)是否"急性大比例"（当日解禁≥15%
      +同步大额减持、质押≥55%濒临平仓）？是=✗；(c)否则=存量/一次性，不因它否决，记med-alert供参考
      （范例：小商品城H股融资=事件型摊薄→未否✓；保利=预减85%恶化→✗）。
   ④ **国内外双向搜索**（周期/出口/医药必做）：大宗商品价（海油→油价/OPEC）、海外同业暴跌、海外政策
      （关税/FDA/制裁）——底部票常是外部驱动，只搜国内会漏主因。
   ⑤ **前视纪律（双向：既防未来信息泄入，也防旧闻冒充当期）**：只认信号日T当日或之前已公开的信息定裁定；
      关键恶化信息若发布日>T，标"滞后披露·裁定时不可得"仅供复盘（8v8显示部分恶化如销量/季报系滞后披露）。
      ⚠**对称的另一半同样致命——网页检索会把多年前旧文与当期新闻混排返回、摘要常不带年份，看着全像当期利空**：
      **每条红旗必须核到"年"再采信**（点开来源/看URL日期段/与当前股价·市值量级对表），**核不出年份的一律不采信**，
      不因它否决、也不写进 why（2026-07-16 宁德时代实例：搜出的三条重磅红旗全是旧闻——"9.52亿股/近2800亿解禁"=
      **2021-06-11**、"LG夺特斯拉43亿订单致宁王跌6%"=**2025-07-30**、"国会议员指强迫劳动/要求列实体清单"=**2024-06**；
      任一条误当当期都会造成**错误否决**——比误给✓更隐蔽，因为空手不留痕迹、没人复盘一只没买的票）。
      每条带来源日期；查无恶化也要写"已搜 业绩/财务/治理+事件型+海外驱动 ≥3类源，无恶化证据"→ ✓错杀可入；
      证据不足/存疑 = ?降级。
   ⑥ **纯regime踩踏免责**：全市场流动性危机砸的票（如2024-01微盘股）可无任何个股红旗，消息面对它无解——别硬编
      利空理由，据实写"无个股恶化证据·系系统性踩踏"，靠仓位/熔断兜底。
3. **Agent③「毒月 Web 预警官」（我做，每次扫描必跑，0只过线也不能跳过）**：完整遵守
   `references/TOXIC_RISK_WARNING_PROTOCOL.md` 与 `references/AGENT3_SECTOR_MAPPING_PROTOCOL.md`，同时维护
   **截至T的无前视风险 nowcast**和**截至本次实际
   检索完成时点的最新五域评估**；后者可用 T 后公开信息，但必须隔离并禁止倒灌 T 日裁定。禁止声称能预测尚未公开的黑天鹅。
   固定覆盖 `排期宏观政策/国内监管与流动性/海外地缘与贸易/跨资产压力/长假信息缺口` 五域：
   - **全域最新检索**：FOMC、PMI 只是例子，不是事件白名单。每次运行都要把五域从 T+1 搜到实际完成时点，
     每域至少使用两种不同查询文本，纳入当时最新公开的重大变化；不得只复述上次报告已有事件，
     或以“未确定”替代检索和判断。
   - **八类预测输入**：逐类覆盖美股行业、全球同业事件、亚洲早盘同业、中国相关离岸资产、利率汇率波动、
     商品运价、宏观预期差、国内政策产业信息；记录原市场交易日、北京时间可得时点、相对基准、冲击类型、
     新鲜度和来源。市场未开写 `not_open`，来源受阻写 `blocked`，两者都不得伪造方向。
    - **逐项证据约束推断**：每条 warning、每条 T 后 delta、每个五域运行时点综合都必须分开写
      `事实/当前共识/基准情景+置信度/上下行情景/传导链/观察变量/失效条件/推断边界`。
      有市场定价、调查或可靠机构来源才可写精确概率/基点；否则只给定性置信度。推断不是交易指令。
    - **重大排期事件完整预期台账**：主动扫描 T+1 起未来10个自然日的重大统计、央行、政策与长假排期；
      每条 `status=scheduled` warning 必须一对一写入 `scheduled_event_expectations`，包含事件名、北京时间/精度、
      官方排期来源、一致预期来源、前值、公布后观察项和条件式情景。CPI/PPI 固定补齐总项环比/同比、核心环比/同比
      四项；就业报告固定补齐新增就业、失业率、薪资环比；LPR 固定补齐1年期/5年期。官方源只证明排期，调查或可靠
      机构来源才证明预期。确无可靠共识时必须在完成至少两种独立共识查询后写
      `consensus_status=no_reliable_consensus`，不得编数，也不得只漏字段。用户不负责提醒或补齐宏观预期。
   - **五域+预测输入合并映射到A股**：完成五域与八类输入后输出 `ashare_runtime_outlook`，分别直说竞价/开盘、
     开盘后延续或回吐、下一交易日综合及未来1—5个交易日的偏强、偏弱、震荡或分化路径。相对受益/承压板块
     必须来自结构化 `sector_calls`，在HTML逐条 bullet 为 `板块（原因）`，并显示窗口、置信度、来源与失效条件；
     不得用“风险资产或有波动”代替A股结论，不得编造未经校准的A股涨跌概率、幅度或指数点位。
    - **排期风险**：统计数据、LPR、FOMC、政策生效日、长假闭市等只给 med 黄色提示，
      `direction_certainty=uncertain`；但仍须评估当前最普遍预期和条件式市场传导，不能只写“方向不确定”。结构化预期
      必须在报告顶部 A股走势映射中可见，不能只埋在审计附录。
   - **活跃风险**：战争—航运—油价、关税/出口管制、融资收缩、退市/ST恐慌、跨资产强平等，只有
     `first_public_at≤T` 且截至T仍未结束才可 warning；high 必须有官方源和至少两个独立 `origin_id`。
   - **候选暴露与板块下沉**：warning 只有行业、产品、成本或海外收入能明确映射时才下沉；泛化市场恐慌不得
     复制成每票红条。若 `sector_calls` 精确提及候选所属行业或显式纳入产业链候选，则同一 `call_id` 必须双向进入
     `by_code[code].sector_context` 和HTML股票卡片，显示股票级关系、原因、来源与失效条件。Agent③只加 shadow 信息，
     **不改变 Agent② 的 ✓/?/✗**。
   - **T后隔离**：检索日晚于T时五域都做截至实际运行日的末端扫描；突发风险只进
     `post_t_safety_items`，`used_in_asof_t_warning=false`，但必须进入 `runtime_evaluation` 被综合评价；
     HTML 明示“T后”和运行时点评估，不得倒灌成事前命中。
   - **结构化落盘**：写入 `codex_audit.toxic_risk_warning`，固定
      `version=bottom-toxic-risk-warning/v3`、`mode=shadow`、五域 coverage、sources、queries、warnings、
      `scheduled_event_expectations`、`market_signals`、八类 `predictive_input_coverage`、含 `sector_context` 的 by_code、post-T 增量、五域
     `runtime_evaluation`、含分窗口 `sector_calls` 的 `ashare_runtime_outlook` 和 clear_reason。
     每条 warning/delta 同步到顶层 `alerts`；
     映射候选的再同步到 `rulings[code].alerts`，都带 `warning_id/level/text/shadow/post_t`。
   Agent③尚未完成无前视 shadow 样本验证，**不得改分、禁买、降级、调仓位或替代两个预算熔断**。
4. **裁定与预警同步进HTML（--adjudicate，2026-07-15 新增）**：把每只过线票的裁定和 Agent③预警写入
   `C:\Trading_analysis\data\bottom_adjudication.json`（格式
   `{"T":"引擎T日","alerts":[组合级警示],"rulings":{"代码":{"verdict":"✓|?|✗","why":"理由+日期来源",
   "alerts":[{"level":"high|med","text":"警示"}]}},"codex_audit":{"bottom_search":{...},
   "toxic_risk_warning":{...}}}`——搜索审计必须严格遵守
   `references/WEB_EVIDENCE_PROTOCOL.md`、`references/TOXIC_RISK_WARNING_PROTOCOL.md` 和
   `references/AGENT3_SECTOR_MAPPING_PROTOCOL.md`；
   **凡影响大的点必须抽成 alerts 高亮**（P9利好兑现/
   见光死、低价股滑点、重复过线旋转门、硬否决项等），high=红条/med=琥珀条，别埋在 why 长文里），然后跑：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search --result "C:\Trading_analysis\data\bottom_latest.json" --audit "C:\Trading_analysis\data\bottom_adjudication.json"
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom -- --adjudicate
   ```
    `validate-bottom-search` 会同时验 Agent② 与 Agent③；非零退出时禁止 adjudicate：自动定位并补齐搜索、来源链、
    F10逐条对账、重大排期事件完整预期、五域 coverage、alert 映射或T+隔离记录，不能删字段绕过，也不能停下来让用户
    提醒缺哪项或代填预期。
   引擎自动：**分层重排**（✓>?>✗，层内仍按引擎总分——纪律：不造未经走样本校准的新数值权重）→
   ?/✗票撤除买入方案（P13-3：非买入票不给价，卡片保留供对照，✗票置灰）→ 重出裁定版HTML
   （严格命名为 `bottom_cn_YYYY-MM-DD_HH-MM-SS_裁定版.html`，时间取带 `+08:00` 的
   `adjudicated_at`）→ 裁定回写影子日志 → 以后 `--review` 自动拆「引擎全线 vs 裁定✓子集」
   两条战绩，长期检验Agent②消息面裁定是否真有增量。T不一致会拒绝合并（防脏裁定）；
   生成后按 HTML 正文中的 T **自动删除同T旧版HTML**（含旧裁定版），reports 里每个 T 只留最新裁定版；
   文件名本身只表达完整北京时间生成时点，不再承担 T 的语义。
5. **Agent④式复核并通过 Codex 硬门禁后才输出最终表**：引擎表 + 裁定列（✓错杀可入 / ✗恶化·否决 / ?存疑降级）+
   Agent③ shadow warning，检查
   价格新鲜度(引擎T=最近已收盘日)、数字来源可追溯；否决票在文字报告注明理由（HTML卡片保留供对照）。
   每票 `rulings[code].why` 至少展开关键财务数字、六维核查、最强反方风险和结论，不得只写“已覆盖、未见风险”的摘要；
   最终 HTML 还必须把六维采用事实、日期、来源、F10逐条数量及 T/T后裁定状态下沉到每张候选卡片。
   `bottom_adjudication.json` 顶层除原字段外必须加入 `codex_audit`。完成审计后优先运行自动发布器；它会自动执行
    基线、可机械派生字段归一、搜索审计、ETF多端点重试、跨源验价重试、attach、augment、brand、最终验收和HTML禁词/区块巡检：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\scripts\finalize_bottom.py"
   ```
   手工排障时才拆开运行以下等价步骤。先对每个 `✓` 票执行独立跨源验价，
   并把输出的 `price_verification_by_code` 并入 `codex_audit`；没有 `✓` 时输出为空对象即可：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\verify_prices.py" --skill bottom-fishing --result "C:\Trading_analysis\data\bottom_latest.json"
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" attach-audit --result "C:\Trading_analysis\data\bottom_latest.json" --audit "C:\Trading_analysis\data\bottom_adjudication.json"
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" augment-report --skill bottom-fishing --json "C:\Trading_analysis\data\bottom_latest.json" --html "<裁定版HTML绝对路径>"
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" brand-report --html "<裁定版HTML绝对路径>"
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate --skill bottom-fishing --json "C:\Trading_analysis\data\bottom_latest.json" --html "<裁定版HTML绝对路径>" --require-bottom-search
   ```
    任一命令非零退出时由自动发布器对可恢复环节换源重试；重试完成前不得回复“获取失败”或发布半成品。
    **一次出结果闭环（强制）**：首次校验失败后读取全部错误，能由已有证据机械同步的交给 normalizer；缺来源、缺查询、
    缺重大事件指标、缺情景或时间冲突的，由 Codex 继续网页检索并更新同一份审计，再从 `validate-bottom-search` 重跑。
    最多保留一个最终裁定版并在所有硬门禁通过后才回复用户。除非遭遇需要用户新授权的权限阻断，整个补齐—重验过程不得
    要求用户干预，也不得把“下次再加”当成本次完成。
   最终 HTML 仍含 `数据获取失败`、`未识别明确相对受益板块`、`未识别明确相对承压板块`、缺ETF区块、
   缺六维裁定底稿或缺候选板块下沉时一律不发布；验收器不提供最终报告降级开关。
6. **复盘对账**：用户说"抄底复盘/对账"时跑：
   ```powershell
   python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom -- --review
   ```
   自动给每笔历史过线票结算（先到+5% vs 先砸-8%，20日窗），输出累计胜率/爆雷率 vs 回测口径（75.2%/13.9%）。

## 硬性纪律（全部来自面板实证，改动须先过走样本）

1. **数字口径引用规范**：胜率只能说"走样本 75.2~77.8%（先到+5% vs 先砸-8%，20日窗）"，爆雷率
   "13.8~13.9%·CI上限16.3%"；**必须同时披露"月度胜率45~92%、爆雷2~55%成簇"和"牛市窗口+幸存者池，打七折"**。
   拆档：触-10%=12%/-15%=4%；摸+10%=53%/+20%=19%。EV最优目标=+10%（+3.71%/笔）但期长翻倍，默认+5%落袋半仓/+10%清。
2. **0过线=空手**，不降格凑数（历史约6成日子无票——频繁空手是合格线的本体）；**不做"每日相对最好"**（
   打分Top-1已被证明≈随机，只认绝对阈值）。
3. **反指标铁律**：深超卖RSV≤15/≥4连阴/涨停基因/ATR≥7/刚创新低——"越惨越买"全是负分，**永不因"更便宜了"加分**。
4. **执行三刀**：T+1开盘进场（引擎已用权威日历算出并显示真实买入日期，竞价高开>3%放弃）；止损-8%条件单
   成交即挂（**跌停封死风险高于动量票，所以单票≤3.5%仓，硬上限**）；买入日收盘≤-5% → 次日开盘无条件离场
   （崩盘快刀·本skill适用性已单独验证：过线票买入日崩概率仅0.5%，崩后继续持有 胜21%/雷79%/EV-5.29% vs
   次日出确定-5.58%——期望≈打平，保留此刀的理由是截断79%的深尾路径+与weekly-ashare纪律统一，成本≈0）。
5. **毒月防御（2026-07-14 扩展研究定版，2026-07-23 真实交易日口径复核）**：本策略**市况依赖**——
   **2024式阴跌熊市全年EV为负（51.6%胜/42.7%雷）**；毒月解剖旧稿的“61%集中在同一周”
   使用5个**有信号日期**、可能跨越超过5个市场交易日；按真实连续5个市场交易日复算，
   10个严格毒月止损兑现窗覆盖506/884=57.2%，连同2024-07边界月为584/1057=55.3%，后续只能引用新口径。
   行业复核显示大样本毒月以跨行业 regime 冲击为主，小样本月易被重复信号/赛道轮动放大；
   **毒月内输家与赢家个股特征完全相同（分数/ATR/回撤全一样）→ 选股端无解**；牛熊闸门(MA250)已测无效
   （2024年线上子样本29.6%胜更差，纯regime混淆）。防御只有两个预算型熔断，都必须执行：
   ① 月度预算：抄底累计亏损达账户-3% → 当月停做；
   ② 滚动停做开关（--review 自动检测）：近20笔了结雷率≥30% → 暂停抄底至回落<20%。
6. **影子复验期**：累计30笔了结前，报告顶部必须标"影子期：建议纸面跟踪或仓位减半"；Agent③独立保持
   `mode=shadow`，在完成无前视回放、误报率、机会成本与预注册样本前只显示 warning，不影响任何交易字段。30笔后 --review 实测
   与回测口径偏离>10pp → 回到研究台重校准。def_days/idx_rsv 继续随每笔记录，但两格子已被含2024面板
   证伪（防守≥9天全期EV-0.41·2024内40.7胜/51.7雷；大盘RSV15-40无增量）——**只记录、不作预期依据**。
7. **已被数据否决、禁止再加**：资金流(叠加后反向)、多元学习权重(OOS全灭)、K线企稳形态(安慰剂)、
   "拿久等回来"(84-89%先触止损)、P13-2式已兑现降级、**放量volx≥1.5**(全期+5.7pp纯系2024伪影，
   2025H1/H2/2026三段雷率一致变差+7.4/+10.3/+15.2pp)、**个股MA250上方**(2024Q4子样本17%胜/65%雷
   反向，regime混淆，与指数MA250闸门同病)、**CatBoost/tree-boosting打分选股**(2026-07-16 purged前推CV:
   ML等量 65.9胜/26.4雷 < 规则 77.3/13.8 < base 74.7/17.8; 特征重要度≈90%在大盘regime·个股因子≈0 →
   选股端无信号，是"多元学习权重OOS全灭"第二证据; ML仅regime择时或有价值、绝非选股)。
   新想法一律先面板走样本再进规则。
8. **0 API / 真实数据 / 全程中文**；T+1次日才能卖；与 weekly-ashare 同账户运行时注意两边仓位合并计算总仓。

## 研究复现资源（仅在重校准/规则审计时读取）

日常扫描不得执行研究脚本。用户要求重校准、追溯阈值来源或评估新 filter 时，先完整阅读
`references/RESEARCH_LEDGER.md`，再从 `scripts/research/legacy_cc/`、`scripts/research/bottom_ml/`、
`scripts/research/holiday_event_study/` 或 `scripts/research/toxic_month_web_study/`
选择对应实验；必须保留原样本窗、幸存者偏差、前视隔离与“采纳/否决”状态，未经面板走样本复验不得改生产规则。

方法论摘要见同目录 `README.md`；研究脚本、外部数据 hash 和 Claude Code provenance 见
`references/RESEARCH_LEDGER.md`。
