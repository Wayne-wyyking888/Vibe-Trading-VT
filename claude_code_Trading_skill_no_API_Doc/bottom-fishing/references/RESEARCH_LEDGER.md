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
- 大样本数据不随 skill 复制，保留在 `C:\Trading_analysis\research\bottom_ml\`。运行前必须先核对 manifest；
  hash 不一致即视为不同实验，不得沿用旧结论。

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
