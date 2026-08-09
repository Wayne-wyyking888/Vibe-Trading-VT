# board30_split_study

研究 `30*`（20%涨跌幅）是否应与 `60*+00*`（10%涨跌幅）使用不同的 bottom-fishing 机制。

本目录只包含研究源码和预注册；大样本数据与运行输出写入：

`C:\Trading_analysis\research\bottom_board30_split_study\`

不会读取或写入 `bottom_latest.json`、`bottom_adjudication.json`、`bottom_shadow_log.jsonl`、生产报告或生产权重。

运行：

```powershell
python research.py --fetch --refresh
python research.py --extend-history
python research.py --analyze
```

腾讯单次最多返回约640根，因此 `--extend-history` 会再抓一段截至2023-12-31的同源前复权数据，逐值核对
重叠日后合并，确保2023-11信号拥有60日预热。`--refresh` 只会重抓本研究自己的外部数据目录，不影响其他实验。
完整口径见 `PRE_REGISTRATION.md`。分析产物包括 `summary.json`、`candidate_grid.csv`、信号明细、月度比较、
`REPORT.md` 与 `SOURCE_MANIFEST.json`。

## 当前裁定（2026-08-09）

预注册冻结候选为 `limit20_atr10|atr8|def24|stock15`。全扩窗 `30*` 旧/新为
544笔61.6%胜/32.4%雷/EV+0.49，对632笔69.6%/26.7%/+1.34；2026 holdout为
49笔67.3%/30.6%/+0.92，对116笔71.6%/27.6%/+1.37。

裁定固定为：**达到进入生产外 shadow 讨论的点估计门槛，但统计未确认，暂不改生产**。原因是holdout只有7个月且
月簇bootstrap全部跨0，29个双方有信号月中11个月变差，2025H2反向；ATR=8位于预注册搜索上边界，post-hoc
到12/无硬gate才饱和，不能把事后结果倒灌成新规则。若开启下一阶段，只能从尚未出现的交易日预注册并并行记录
ATR8/ATR9/无硬gate shadow 线。

未来若最终采纳，架构上是一条公共 workflow 加一个 board profile 选择：`30*` 使用独立打分语义/推荐线，
`60*+00*` 使用现行 baseline；市况、底部区、数据拉取、旋转门、F10、Agent②/③、执行风控保持共用。
仍须给日志增加 `mechanism_version`、让 review 分线结算，并补 acceptance，不能只加一行无版本的 `if/else`。
