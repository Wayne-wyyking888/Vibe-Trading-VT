# bottom-fishing 暴雷前 K 线轨迹研究

本目录归档“当前抄底推荐线在暴雷前 10/20/30/60/75/100/120/150 个交易日的
K 线共同形态，以及与未暴雷样本差异”的可复现研究。它只属于研究层，不是生产 workflow。

## 文件与输出

- `precrash_kline_study.py`：固定标签、成熟样本、N=5 冷却、多窗口 OHLCV 特征、事件对齐轨迹、
  BH 多重检验、20 日事件去重、同日对照、分年方向和季度前推 OOS。
- `SOURCE_MANIFEST.json`：源码、外部 parquet 和本次生成结果的大小与 SHA-256（运行并定版后生成）。
- 大样本输入仍在 `C:\Trading_analysis\research\bottom_ml\`；结果写入
  `C:\Trading_analysis\research\bottom_precrash_kline_study\output\`。

## 固定口径

1. 母体不变：防守日总分≥18，或非防守日个股分≥15；两条路径均须 ATR≤4。
2. 当前 N=5 旋转门：任何过线事件（包括被冷却压下者）都刷新计时。
3. T+1 开盘进场；20 个交易日内先 +5% 为 `win`，先 -8% 为 `stop`；timeout 单列。
4. 每笔 T 后必须仍有至少 21 根个股 K 线；否则视为未成熟，避免样本尾部只剩快速触发的输赢。
5. 前置窗口全部截止于 T，不使用 T+1 或之后的价格。
6. 主比较为 stop vs win，辅助比较为 stop vs (win+timeout)。
7. 逐笔 Mann–Whitney 与 BH 只用于发现；要称为“稳健候选”，还须同时满足：
   - BH q<0.05 且 |AUC−0.5|≥0.06；
   - 2024/2025/2026 三段方向一致；
   - 20 市场交易日事件去重后方向一致；
   - 同一信号日 stop−win 方向一致且 Wilcoxon p<0.10。
8. 季度前推 OOS 使用 20 市场交易日 embargo；模型只诊断可分性，不产出新权重。

## 复现

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing\scripts\research\precrash_kline_study\precrash_kline_study.py"
```

脚本首先核对 `bottom_ml/SOURCE_MANIFEST.json` 中五个 parquet 的大小和 SHA-256，任一不一致立即停止。

## 本次裁定（2026-08-02）

- 成熟原始过线 4,473 笔；按当前 N=5 冷却后 1,458 笔（stop 395 / win 963 /
  timeout 100），覆盖 422 只股票、2024-01-15 至 2026-06-16；再按同股 20 个市场交易日
  事件去重后为 1,115 笔。
- 共同形态：信号前约 60 日持续深跌，20—30 日仍在走弱，T 附近只有局部修复；成交量普遍较
  窗口前段收缩。这是所有过线票的共同形态，不是雷票独有。
- 60 日窗全体中位：收盘收益 -16.3%、最大收盘回撤 -26.8%、T 距窗口最高价 -25.7%、
  距最低价反弹 +5.6%、窗口位置 12.9%；窗口后 20%/前 20%成交量比为 0.51。
- stop vs win 的 60 日中位分别为：收益 -16.36% vs -16.43%、最大回撤 -27.30% vs
  -27.14%、距最高价 -25.90% vs -26.16%、位置 13.14% vs 12.72%；轨迹几乎重合。
- 10—60 日的收益、回撤、位置、连跌、阴线、缺口、下影、波动和成交量，不能稳定区分
  未来先 -8% 还是先 +5%。
- 100—150 日全样本的表面分离主要来自年份/regime 构成；分年、同日和事件去重后不稳定。
- 季度前推 OOS：多窗口个股 K 线 Logistic 的 5 个季度宏平均 AUC=0.503，季度范围
  0.412—0.658；不能稳定泛化。信号日既有字段宏平均 AUC=0.582，只作基准复核。
- 严格稳健个股 K 线候选为 0；不修改生产分数、阈值、仓位、熔断、Agent②/③或扫描流程。

完整数字、轨迹图、逐特征比较和 OOS AUC 见输出目录的 `summary.md`、`trajectory.png`、
`feature_comparison.csv`、`oos_auc.csv` 与 `results.json`。
