# Bottom-fishing research ledger

本文件只服务于规则追溯、重校准和新假设评估。日常扫描不得调用 `scripts/research/`；生产规则仍只认
不可变引擎、`SKILL.md` 和独立 acceptance。Legacy 脚本是从 Claude Code scratchpad 原样恢复的研究快照，
不是自动升级规则的入口。

## 资源与完整性

- `scripts/research/legacy_cc/`：18 个 bottom 专属历史面板、敏感性和复盘脚本；逐文件原始路径、Claude session、
  大小与 SHA-256 见同目录 `SOURCE_MANIFEST.json`。18/18 已通过 `py_compile`，未执行联网研究负载。
  同一旧会话中属于 weekly P13/P14 的脚本已按规则归属移入 weekly skill，避免重复和误用。
- `scripts/research/bottom_ml/`：7 个 CatBoost/purged-CV 与毒月样本选择源码；来源、源码 hash 及外部 parquet
  hash 见同目录 `SOURCE_MANIFEST.json`。
- `scripts/research/precrash_kline_study/`：暴雷前 10/20/30/60/75/100/120/150 根 K 线轨迹、
  stop/win 对照、N=5 冷却、成熟标签、同日对照、20日事件去重与季度前推 OOS；源码、外部 parquet
  和生成结果 hash 见同目录 `SOURCE_MANIFEST.json`，大结果留在
  `C:\Trading_analysis\research\bottom_precrash_kline_study\output\`。
- `scripts/research/holiday_event_study/`：2024—2026 上交所官方休市日历与节前/节后事件研究；
  源码、日历、外部 parquet 和生成结果 hash 见同目录 `SOURCE_MANIFEST.json`，大结果留在
  `C:\Trading_analysis\research\bottom_holiday_event_study\output\`。
- `scripts/research/toxic_month_web_study/`：毒月真实 5 市场交易日集中窗、行业集中度、国内外网页事件归因
  及事前 warning 设计；源码、证据账本、外部 parquet 和生成结果 hash 见同目录
  `SOURCE_MANIFEST.json`，大结果留在 `C:\Trading_analysis\research\bottom_toxic_month_web_study\output\`。
- `scripts/research/board30_split_study/`：`30*`（20%涨跌幅）与 `60*+00*`（10%涨跌幅）分组机制研究；
  `PRE_REGISTRATION.md` 冻结候选族、时间隔离和 shadow 门槛，`research.py` 负责同源 qfq 分段补历史、
  N=5 旋转门、1000候选搜索和2026 holdout，`verify.py` 独立复算五条 A/B 结果。大数据、报告、冻结文件、
  验收结果和 hash manifest 留在 `C:\Trading_analysis\research\bottom_board30_split_study\`。
- 大样本数据不随 skill 复制，保留在 `C:\Trading_analysis\research\bottom_ml\`。运行前必须先核对 manifest；
  `board30_split_study` 的大样本另保留在上述独立目录。运行前必须先核对对应 manifest；hash 不一致即视为
  不同实验，不得沿用旧结论。

## 规则到实验的映射

| 研究主题 | 主要脚本 | 当前裁定 | 关键限制 |
|---|---|---|---|
| 底部区、修复因子、绝对阈值与双路径 | `bottom_panel.py`, `bottom_study.py`, `bottom_factors.py`, `bottom_top1.py`, `bottom_oos.py`, `bottom_threshold.py`, `bottom_dist.py`, `bottom_regime2.py` | 采纳底部区、修复确认、双路径与 ATR gate；否决“每日相对Top-1” | 今日高流动性股票回溯，含幸存者与牛市窗口偏差 |
| 毒月、2024扩窗和熊市闸门 | `toxic_month.py`, `fix2024.py`, `bear_gate.py` | 采纳月度-3%与滚动20笔雷率熔断；否决 MA250/简单牛熊选股 gate | 2024能证明市况成簇，但不能保证未来熊市形态相同 |
| 旋转门与候选 filter | `cooldown_sens.py`, `monthly_cooldown.py`, `bottom_filter_research.py`, `analyze_filters.py`, `validate_two_filters.py` | 采纳5交易日冷却；放量、MA250等不进生产规则 | 900根历史不足的字段和分段反向结果不得挑有利窗口引用 |
| 资金流 | `bottom_flow.py`, `bottom_flow2.py` | 否决资金流加分/选股 gate | 源口径、缺失和样本选择会改变方向 |
| 崩盘快刀与尾部成簇 | `toxic_month.py`, `bottom_regime2.py` | 保留买入日崩盘快刀、低仓位和预算熔断；否决“拿久等回来” | 胜负必须使用先到目标/先触-8%的路径口径，不能只看期末收益 |
| 毒月消息面红旗分型 | `bottom_ml/select_poison.py`, `bottom_ml/select_winners.py` + README 8v8人工证据 | 采纳“恶化型强否决、事件型单独不否”的定性 rubric；不加数值分 | 人工对照仅 n=16，进入数值 gate 前仍需面板验证 |
| ML 选股与毒月对照 | `bottom_ml/fetch_klines.py` → `panel_build.py` → `align_check.py` → `model.py`; `select_poison.py`, `select_winners.py` | CatBoost/tree boosting 选股进入否决清单；只保留 regime 研究可能性 | purged前推与 holdout 仍受股票池、特征脆弱性和单窗口影响 |
| 暴雷前多窗口 K 线轨迹 | `precrash_kline_study/precrash_kline_study.py` | 采纳“60日深跌、20—30日继续走弱、T附近微修复、成交量收缩”为全体过线票描述；未找到稳健区分 stop/win 的个股 K 线特征，不新增过滤器 | 成熟冷却样本1458笔；100—150日表面差异受年份/regime混杂；同日、分年、20日事件去重与季度前推均未支持稳定泛化；150日窗缺早期样本 |
| 节假日前后暴雷相关性、毒月反向归因 | `holiday_event_study/holiday_event_study.py` + 上交所2024—2026休市日历 | 节前仅弱提示、节后不成立；毒月不是节日窗制造；不增加禁买窗口，不改生产workflow；只允许未来shadow累计 | 完整事件仅16个；节前5日冷却线+7.9pp但cluster CI跨0、随机化p=0.470、BH q=0.765；毒月内±5日窗占25.5%信号/26.1%雷，删除后真正因雷率跌破30%而消失的毒月=0 |
| 毒月真实集中窗、网页事件归因与事前预警可得性 | `toxic_month_web_study/analyze_toxic_windows.py` + `web_event_ledger.csv` + `EARLY_WARNING_DESIGN.md` | 当前固定面板为10个严格毒月及2024-07边界月；11个月1057笔止损中584笔（55.3%）集中于各月真实连续5个市场交易日的兑现窗，严格10个月为506/884（57.2%）；旧约61%是“5个有信号日期”口径，不得混用。共同点更像跨行业regime切换，不是同一板块。2026-07-23采纳为 Agent③ 五域 Web nowcast 和 HTML shadow warning，契约见 `references/TOXIC_RISK_WARNING_PROTOCOL.md`；不改分数、裁定、仓位或推荐 | 网页归因有事后叙事偏差；仅11个月，且2026多个月份受小样本和重复信号放大；Agent③仍须累计逐笔T日证据、误报和机会成本，未升级为交易gate |
| `30*` 20%涨跌幅独立打分/阈值 | `board30_split_study/research.py`, `verify.py`, `PRE_REGISTRATION.md` | **shadow-only，暂不采纳生产**。预注册网格冻结候选为 `limit20_atr10|atr8|def24|stock15`：仅对`30*`把涨停基因阈值9.3%→18.5%、ATR高波惩罚7→10、硬gate 4→8、防守总分18→24，非防守个股分仍15；`60*+00*`逐行不变。全扩窗`30*`旧/新=544笔61.6胜/32.4雷/EV+0.49 vs 632笔69.6/26.7/+1.34；全部旧/分组=1754笔61.1/31.9/+0.50 vs 1842笔63.8/30.0/+0.79。2026 holdout `30*`旧/新=49笔67.3/30.6/+0.92 vs 116笔71.6/27.6/+1.37。独立验收12/12、旧面板复现通过 | holdout仅7个月且月簇bootstrap胜率/雷率/EV差CI均跨0；29个双方有信号月仅15改善/3平/11变差，2025H2反向；ATR=8落在预注册网格上边界，post-hoc 到12/无硬gate才饱和，未识别全局最优；今日成交额前600有幸存者偏差、未模拟20%板跌停封单与滑点。只支持未来前瞻shadow，不支持现在改workflow |

## 重跑纪律

1. 在隔离输出目录重跑，禁止覆盖 `bottom_latest.json`、裁定文件、影子日志、报告或生产权重。
2. 先跑 baseline，记录引擎 hash、数据 hash、股票池、日期窗、复权方式、标签定义和 embargo；缺一不比较结果。
3. 先复现 ledger 中原结论，再测试新假设；复现失败时不得继续调参寻找“更好数字”。
4. 新规则必须有时间隔离的走样本结果、分 regime 结果、样本量/置信区间和机会成本，并与现行规则同口径 A/B。
5. 研究输出只能先进入 shadow 字段；完成预先约定的样本数和回滚条件后，才讨论修改不可变引擎。

## 尚未升级为生产规则的问题

- `?` 同时承载“证据不足”和“证据充分但方向冲突”。先在影子审计中区分 `insufficient`/`conflicting`，
  累积样本后再决定是否引入“逆风观察”第四态。
- 小盘/冷门公司的低信息密度可能被误读为“无恶化证据”。先按市值与资讯覆盖度分层检验 ✓ 子集表现；
  未验证前不得机械否决小盘股，也不得把 `no_relevant_hit` 当作强正面证据。
- 市场级 Web risk warning 已以 Agent③ 接入日常流程并由独立验收器强制检查五域覆盖、T/T后隔离、
  来源 origin 和 HTML alert 联动；当前仍未完成 T-as-of 回放和 shadow 样本，只允许
  `mode=shadow` 的透明提示，不得自动降级、禁买、调仓位或改分。
- `30*` 分组机制虽达到预注册“进入 shadow 讨论”点估计门槛，但未达到统计确认，也未解决 ATR 上边界。
  若开启下一阶段，必须在**尚未出现的新交易日**预先冻结 ATR8/ATR9/无硬gate 三条影子线、最小样本与回滚条件；
  在此前不得把 post-hoc ATR12/无gate 反过来写进生产，也不得把本次2026 holdout再次称为新样本外。
