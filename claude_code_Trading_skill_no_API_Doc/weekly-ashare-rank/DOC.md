# weekly-ashare-rank — 完整文档

## 🚀 怎么调用（样板 wording，直接复制改数字）

**日常选股（一句话，平时就用这条）：**
```
/weekly-ashare-rank 天数=5
```
**带参数（板块/数量/持有天数都可选，省略用默认）：**
```
/weekly-ashare-rank 天数=3 板块=光通信 top=10
/weekly-ashare-rank 天数=5 板块=机器人
/weekly-ashare-rank top=15
```
**自然语言也认（不想记格式就直接说）：**
```
用 weekly-ashare-rank 选下个交易日买入、最多持有5天的票
下周买什么，持有3天，重点看光通信
明天能买哪些，T+1进，一周内出
```
**买入日早上（T+1，9:15–9:25）盘前复核（可选，一句话）：**
```
复核
复核今天要买的票
```
**重新校准因子权重（一般不用，换持有天数我会自动校准）：**
```
重新校准 weekly-ashare-rank 的因子权重，持有天数5
```

> 调用后我（Claude Code）会：解析参数 → 自动查清 **T / 买入日T+1 / 最晚卖出日T+N 的真实日期**（含春节国庆等节假日）
> → 必要时自动校准权重 → 跑量化引擎 + WebSearch 催化剂 + 风险综合 → 给最终排名表。**0 API、0 额外花费。**

参数对照：`天数/持有/hold/N`→持有交易日数(默认5)；`板块/行业/sector`→限定板块(默认全市场)；`top/数量`→输出几只(默认8)。

---

> A股短线选股排名 skill。Claude Code 原生、0 API、真实免费行情。
> 定位：以最新收盘日 **T** 为基准 → **T+1 买入** → **持有 ≤N 个交易日**（N 默认 5，可参数指定）。
> 不局限于周一；T+1 与 T+N 的真实日期由权威交易日历算出（自动跳过周末+节假日）。

## 1. 为什么 0 API

Vibe-Trading 自带 swarm（`agent/src/swarm/presets/`）每个 agent 都调外部 LLM，需 API key/Ollama。
本 skill 反过来：**Claude Code 本身就是大脑**，读 `SKILL.md` 后亲自跑 Python 引擎（免费行情）+
自带 WebSearch + 自己判断排名。0 API、0 额外花费。

## 2. 文件结构
```
weekly-ashare-rank/
├── SKILL.md                 # Claude Code 执行流程（三方辩论 + 参数解析 + 跳空规则）
├── DOC.md                   # 本文件
├── ashare_weekly_rank.py    # 量化引擎（Agent①）+ 因子IC回测 + HTML报告渲染
├── recheck.py               # T+1 盘前复核（跳空/破位 → 可买/等回调/放弃）
├── make_report.py           # 由结果JSON渲染HTML报告（Agent③富集后出完整版）
├── universe_seed.txt        # 兜底种子universe（约100只龙头）
├── weights.json             # 回测产出的因子权重（--backtest 生成，--weights auto 调用）
└── reports/                 # 每次run自动生成的HTML报告（文件名后缀=中国当地时间）
```

## 3. 引擎命令行
```
python ashare_weekly_rank.py [选项]
  --hold-days N     最多持有交易日数（T+1买入，持有≤N）。默认5。驱动因子权重 + 回测窗口
  --sector X        行业/概念板块名，模糊匹配（光通信/煤炭/机器人…）。省略=全市场
  --top K           最终输出数量。默认15（skill 默认传8）
  --out 路径.json   结果写JSON
  --backtest        跑因子IC回测，产出 weights.json（不出选股）
  --bt-sample M     回测样本股数。默认60
  --weights auto    使用 weights.json 的回测权重（或给文件路径）
  --no-cache        禁用缓存
  --refresh         强制重拉（绕过读缓存）
```
> Windows：先刷新 PATH，且行情请求要 `dangerouslyDisableSandbox: true`（沙箱拦外网）。

### 数据流水线
1. **快照/universe**（按成交额降序取前600）：东方财富 clist → 新浪 Market_Center → 种子(腾讯报价)。
   - 盘前/收盘后实时数据归零时，自动用K线最后完整交易日兜底。
2. **初筛**：剔除 ST/退市/北交所 → 流通≥15亿 → 成交额≥1亿 → 剔大跌；**涨停票保留**（后续打标）。
3. **粗排**：量比/换手/涨幅取前 `pool` 拉K线。
4. **历史因子**（每只 ~160 日前复权日线，东财→腾讯，带缓存）。
5. **综合打分** → 输出 Top，并标注 N连板 / 一字板 / 回踩 / 尾盘强。
6. 引擎自动算 **T（数据截止日）/ T+1（买入日）/ 是否跨周末** 写入结果。

### 因子与打分（综合分 0–100）
| 桶 | 满分 | 因子 |
|---|---|---|
| 动量 mom | 35 | 5日/20日涨幅、相对均线 |
| 量能 vol | 25 | vr=5/20日均量比、当日放量倍数 |
| 技术 tech | 25 | MA多头排列、MACD金叉、距MA10甜区 |
| 盘口 tape | 15 | **尾盘强弱**(收盘在当日区间位置)、收阳、实体占比 |
| 回调 pull | 10 | 强势股回踩MA10不破+企稳收阳 |
| 惩罚 | − | 过度乖离、60日位>95%、**高位连板(≥3)追高** |

- **持有天数自适应权重**：`hold-days≤3` 加重盘口/动量/反转；`≥10` 加重趋势/技术、降盘口。
- **涨停/连板识别**：创业板/科创 20%、其余 10%；标 N连板、一字板(次日难买入)。

### 风险分 + 买入方案（引擎直接产出，Agent③ 拿来即用）
- **客观风险分(0–100,越高越危险)+等级(低/中/中高/高)**：由 60日位/距MA10乖离/ATR波动/连板/流动性·小盘 叠加。
- **买入方案**：按 风险/信号/乖离/ATR 规则化生成「买入区间 + 建议仓位 + 入场方式 + 止损 + 放弃条件」：
  回踩→低吸标准仓(10%)、偏离MA10>9%→等回调(6%)、连板高风险→轻仓(3%)、健康位置→分批试探(8%)；
  止损=close−1.3×ATR(封顶−6%)，放弃条件含 T+1 竞价跳空(高风险高开>2%/其余>3%)。基本面风险由 Agent③ 叠加。

## 4. 因子IC回测（--backtest，Phase2）
对样本股做横截面 IC 检验：每个因子在 T 的值 vs 未来 N 日收益的 Spearman 秩相关。
- 因子集 = 本引擎因子 + qlib Alpha158/gtja191 风格公式化因子（KMID/KUP/KLOW、ROC、RSV、波动率、量比、位置…）。
- 输出每因子 **IC均值 / ICIR / |IC| / 截面日数**；按经济含义归 5 桶，用平均 |IC| 推**建议权重**写入 weights.json。
- 解读：|IC|>0.03 有效、>0.05 较强；ICIR>0.5 稳定。
- 实测（40样本/25截面/5日窗口）：量比、20日动量、60日位置 IC 最高(~0.07)；盘口/反转较弱 → 权重自动下调。
- 用法：先 `--backtest --hold-days N` 刷权重，再正式跑 `--weights auto`。

## 4b. HTML 报告（每次run自动生成）
每次选股 run 结束，引擎自动在 `reports/` 写一份自包含 HTML（无外部依赖，可直接双击打开）：
- **文件名**：`ashare_rank_cn_YYYY-MM-DD_HH-MM-SS.html`，时间戳为**中国当地时间(UTC+8)**，
  每次运行即时由系统 UTC 换算(`_cn_now()`)，不受机器时区影响、中国无夏令时故恒准确。
- **结构(卡片式，易读、无横向滚动、适配一页宽)**：顶部信息条 → **每只股一张卡片** → 量化明细(可折叠) → 图例 + 免责声明。
  每张卡片：①顶排 量化分·风险等级(分,按低/中/中高/高配色)·盘口信号chip；②四指标 预期收益/置信度/R:R/持仓上限；
  ③蓝条 买入价/目标价/止损价；④三行 入场方式·放弃条件·核心催化剂(长文本整行铺开，不挤成窄列)。
- 顶部信息条含 T/买入日T+1/最晚卖出T+N(带星期) + 跨周末/节假日提示 + 连板/一字警示。
- 之所以用卡片而非宽表：14列宽表会横向溢出、长文本被挤成一字一行；卡片让催化剂等长文本在整页宽度自然换行。
- **预览 vs 完整版**：引擎自动出的是量化预览（置信度/核心催化剂为"—"）；做完 Agent②/③ 后回填 JSON
  再 `python make_report.py --in rank_latest.json` 出完整版（catalysts_md/final_md 会渲染成章节）。
- `--no-report` 关闭自动报告；`--report-dir` 改输出目录。
- `reports/.gitignore` 默认不把 *.html 入 git（避免膨胀）；想同步删掉该忽略即可。

## 5. T+1 盘前复核（recheck.py，Phase3）
买入日早上 9:15–9:25 跑：读 `rank_latest.json`，用腾讯实时集合竞价价算每只票相对 T 收盘的
**跳空%**、是否跌破 ATR 参考止损，给 **可买 / 等回调(高开>3%) / 放弃(破位) / 低吸好点**。
```
python recheck.py --in C:\Trading_analysis\data\rank_latest.json
```

## 6. 容错 / 限流
- 三级跨源回退（universe 东财→新浪→种子；K线 东财→腾讯）+ 多镜像轮换 + 指数退避(≤70s)。
- **东财熔断**：被限流后本次跳过它走回退源。
- **当天缓存**（`~/.vibe-trading/cache/ashare_weekly/`）：K线 TTL 60h(覆盖周末)、快照 6h；`--refresh` 强刷。
- 新浪降级响应识别；种子兜底保证**永不硬失败**。
- 周度单次只发约 40 请求，正常不触发限流；连续测试触发就停手等 1–2 分钟。

## 7. 已知限制
- 板块解析依赖东财板块接口；东财被限流时板块模式回退全市场。
- 引擎只做量价/技术/盘口因子；基本面与消息面由 Agent②(WebSearch) 补足。
- 交易日历用 akshare 权威源(覆盖到当年底)，春节/国庆等节假日已精确处理；跨年且新一年日历未发布时，
  会回退"仅跳周末"并明确标注[日历未取到]，此时跨年节假日可能偏差，以交易所公告为准。
- 结果为量化+研究分析，**非投资建议**；A股 T+1 当日买入次日才能卖。

## 8. 手动自测
```powershell
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
# 选股
python ashare_weekly_rank.py --hold-days 5 --top 8
# 回测刷权重
python ashare_weekly_rank.py --backtest --bt-sample 40 --hold-days 5
# T+1 复核
python recheck.py
```
看到带 T/T+1/持有天数 表头 + 真实价格/信号列即正常。
