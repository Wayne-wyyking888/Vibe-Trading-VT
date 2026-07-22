# Weekly A-share rank research ledger

本文件只用于规则追溯、重校准和新假设 A/B。日常选股不得调用 `scripts/research/`；生产流程仍只认
不可变引擎、`SKILL.md` 和独立 acceptance。Legacy 文件是从 Claude Code 临时目录/JSONL 原样恢复的研究快照，
不是可绕过验证直接改规则的入口。

## 资源与完整性

- `scripts/research/legacy_cc/`：20 个研究/烟测脚本。逐文件原始路径、session、恢复方式、大小和 SHA-256
  见同目录 `SOURCE_MANIFEST.json`；20/20 已通过 `py_compile`，未执行联网研究负载。
- 其中 `test_m1.py`、`test_ind.py` 从完整 JSONL Bash heredoc 原文恢复；其余来自存活临时文件或完整 `Write`
  payload。来自同一旧会话、但语义属于 weekly P13/P14 的8个文件已从 bottom 归档重分类到这里。
- 多个脚本仍保留旧 scratchpad 输入路径，属于可核验源码快照而非开箱即跑工具。重跑时复制到隔离工作目录、
  显式传入/重建数据；不得直接改归档源码后仍沿用 manifest hash。

## 规则到实验的映射

| 研究主题 | 主要脚本 | 当前裁定 | 关键限制/依赖 |
|---|---|---|---|
| pool400/top20/最终8 | `ab_400.py` | 采纳 pool=400、详细层top20、终报前8；干净票不足3时安全阀最多top30 | 依赖当期 `ab_pool200.json`/`ab_pool400.json`；粗排重合结论受当日 universe 影响 |
| M6资金流 | `flow_backtest.py` | 采纳“流出否决/降档，流入不加分”及双源分口径 | 东财与新浪资金流定义不同，禁止混算数值；当前高流动性样本有幸存者偏差 |
| M2/M2'兑现带 | `m2p_validate.py`, `m2p_regime.py`, `m2_band_study.py` | 采纳9.5%硬剔除；7–9.5%及4.5–7%高位为软gate，防守/观望升级 | 47–48只×约160日，高成交额/偏牛窗口；必须继续按 regime 复验 |
| 高能式陷阱、U2/U3/U4 | `history_trap_study.py`, `trap_signal_study.py`, `u234_backtest.py` | 采纳长上影软gate、弱信号≥2硬剔除和临界环境向下取整 | 历史头部样本小；单一弱信号误伤率高，禁止升级成单项硬剔除 |
| P13催化剂双门槛 | `catalyst_study.py`, `rule_check.py` | 采纳非进攻档“终排≤3+≤7天正面催化”；P13-2 price-in降级被否决 | 依赖历史 HTML 和联网K线；连续2期被砍组反超保留组即回滚 |
| 中电港复盘与防守排名 | `postmortem.py`, `defense_study.py` | 保留 P13-4 市况披露；否决 U2'阈值收紧和防守模式再排名 | `defense_study.py` 生成共享 `panel.pkl`；单案例只能提出假设，不能定规则 |
| P14爆雷率与持有期 | `filter_scan.py`, `horizon2.py`, `blowup.py`, `pair_prob.py`, `sub3.py` | 采纳稳健线、两票不同业、仓位损失限幅、买入日崩盘快刀；否决“延长N防雷/崩后等回来” | 依赖 `defense_study.py` 面板；1.7%必须连同95%CI 0.6–4.9%和30笔复验中引用 |
| M1/M5/industry接口烟测 | `test_m1.py`, `test_ind.py`, `test_m5.py` | 仅用于确认基本面/行业字段与情绪高潮降档接口，不提供收益证据 | 会联网；通过只代表接口/规则路径存在，不代表策略有效 |

## 重跑纪律

1. 在隔离输出目录重跑，禁止覆盖 `rank_latest.json`、`weights.json`、市场闸门、track record 或报告。
2. 先跑 baseline，记录引擎 hash、数据源、股票池、日期窗、复权方式、T+1开盘买入口径和前视隔离。
3. 先复现 ledger 中的原结论；复现失败时停止，不得继续调参寻找“更漂亮”的新阈值。
4. 新 gate 必须报告走样本 IC/超额/胜率、尾部概率、分 regime 结果、样本量、机会成本和多重筛选风险。
5. 研究输出先进入 shadow 字段并定义回滚条件；达到预设实盘样本数后，才讨论修改不可变引擎和 baseline。

## 已知复现缺口

- `ab_400.py` 的两份 A/B JSON 未随 skill 保存；需用同一交易日、同一引擎 hash 重建，旧数字只能作 provenance。
- P13 的原始 `catalyst_study_raw.json` 未捆绑；可由 `catalyst_study.py` 从历史报告重建，但必须保存报告集合 hash。
- P14 的 `panel.pkl` 未捆绑；由 `defense_study.py` 重建后，必须记录 universe、日期窗和面板 hash，禁止使用旧临时路径中来源不明的 pickle。
