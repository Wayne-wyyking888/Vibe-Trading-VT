---
name: weekly-ashare-rank
description: A股周度股票排名工作流——三智能体辩论（量化因子 + 消息面/催化剂 + 风险挑战者），输出下周一买入、一周内卖出的最佳标的排名，含预期收益%、置信度、持有天数、入场/目标/止损价。直接触发：load_skill("weekly-ashare-rank") 或 run_swarm(preset="weekly_ashare_rank")。
category: research
---

# 周度A股智能选股排名

## 目的

每周五/周末运行，为下周一买入决策生成排名列表。三个智能体从不同维度独立分析，经辩论综合后输出带置信度的最终排名表。

## 如何触发

### 方法一：直接通过 Swarm 运行（推荐）

```
run_swarm(
  preset="weekly_ashare_rank",
  variables={
    "sector_focus": "全市场 或 例如: 煤炭、光通信、机器人",
    "top_n": "10",
    "hold_days": "5"
  }
)
```

### 方法二：agent 内加载本技能后逐步执行

```
load_skill("weekly-ashare-rank")   # 加载本指南
load_skill("akshare")              # A股数据工具
load_skill("alpha-zoo")            # 因子库
load_skill("sector-rotation")      # 行业轮动框架
load_skill("ashare-pre-st-filter") # 排除ST/风险股
```

## 三智能体辩论架构

```
[并行阶段]
  Agent 1: quant_screener    ──┐
  Agent 2: catalyst_analyst  ──┤──→ [串行] Agent 3: risk_challenger ──→ 最终排名
```

### Agent 1 — 量化筛选者（quant_screener）

**职责**：用GTJA191 / Qlib158 / Alpha101因子对A股全市场评分排名

**关键动作**：
1. 调用 `alpha_bench(zoo="gtja191", universe="csi500", period="2024-2026")` 获取最近IC最高的因子
2. 结合动量因子（GTJA_001动量、20日RS）、量价背离（GTJA_060）、筹码集中度
3. 排除：ST/*ST、上市未满3个月、近5日涨幅>15%（已追高）、市值<10亿
4. 技术层面：要求股价处于5/10/20均线多头排列，成交量放大
5. 输出：Top 20 候选股代码 + 各因子得分

**工具**：`bash, factor_analysis, alpha_bench (gtja191/qlib158), load_skill, read_file, write_file`

**参考技能**：`alpha-zoo, factor-research, tushare, ashare-pre-st-filter`

---

### Agent 2 — 催化剂/消息分析者（catalyst_analyst）

**职责**：从基本面、政策、消息、资金面识别近期催化剂

**关键动作**：
1. 用 `web_search` 搜索当周政策公告、行业龙头业绩预告、分析师评级变化
2. 检查北向资金净流入、融资余额变化、大宗交易
3. 判断是否有短期内（1-5天）可落地的催化事件：财报、行业大会、政策文件
4. 对 quant_screener 输出的候选股逐一打标：催化剂强度（高/中/低/无）
5. 额外从消息面补充2-3只被低估的股

**工具**：`bash, web_search, read_url, load_skill, write_file`

**参考技能**：`sector-rotation, sentiment-analysis, hk-connect-flow, earnings-forecast, akshare`

---

### Agent 3 — 风险挑战者（risk_challenger）

**职责**：质疑并过滤两位agent的推荐，确定最终入场/止损/目标价，预测最优退出时间

**关键动作**：
1. 逐一挑战每只推荐股：
   - 量化信号是否已被过度拥挤？（近5日涨幅、换手率）
   - 消息面是否已price-in？（股价是否已在消息前大幅上涨）
   - 是否存在重大下行风险？（解禁、再融资预期、监管风险）
2. 基于ATR(14)计算止损位（ATR × 1.5倍）
3. 根据历史相似形态预测目标价（前高阻力位、斐波那契）
4. 建议持有天数（1-5天），标注预期最佳退出窗口
5. 剔除风险收益比 < 2:1 的标的，对剩余标的打最终置信度分

**工具**：`bash, web_search, factor_analysis, load_skill, write_file`

**参考技能**：`risk-analysis, technical-basic, ashare-pre-st-filter, market-microstructure`

---

## 最终输出格式

```markdown
## A股周度选股排名 —— 买入日期: YYYY-MM-DD（周一）

| 排名 | 代码 | 名称 | 预期收益% | 置信度 | 持有天数 | 买入价 | 目标价 | 止损价 | 核心催化剂 | 风险等级 | 量化分 | 消息分 |
|------|------|------|-----------|--------|---------|--------|--------|--------|-----------|---------|--------|--------|
| 1 | 600XXX | XX股份 | +8.5% | 82% | 3天 | 12.50 | 13.56 | 11.80 | Q1业绩超预期+行业景气 | 中 | 91 | 78 |
| 2 | 000XXX | XX科技 | +7.2% | 75% | 5天 | 28.30 | 30.34 | 26.60 | 政策利好+北向加仓 | 低 | 85 | 82 |
| ... |

### 排除标的（已过滤）
- XXXXXX（原因：风险收益比仅1.3:1，已涨幅过大）

### 三智能体分歧点
- 量化分析师强推 XXXXXX（因子评分第1），消息面智能体认为利好已price-in，风险挑战者剔除
- 消息面智能体发现 XXXXXX 政策催化，量化评分仅第18，最终以置信度55%排在第9位

### 本周风险提示
- 宏观/系统性风险：...
- 建议最大单仓位：不超过总仓位的10%
- 建议持仓总数：3-5只

### 操作建议
- 分时买入建议：周一开盘后30分钟观察，避开集合竞价高开陷阱
- 止损纪律：跌破止损价立刻止损，不加仓摊低
```

## 约束与注意事项

1. **时效性**：所有数据必须为最新一周的。因子评分过期无效
2. **流动性要求**：日均成交额 > 5000万，避免小票无法出货
3. **仓位约束**：单标的不超过10%，总持仓风险分散
4. **A股特殊规则**：T+1不能当天卖出，止损需次日执行，需在预期收益中扣除这1天的不确定性
5. **涨跌停板**：目标收益率 < 10% 时建议分批建仓，避免第二天被封涨停板买不到
6. **量化因子时效**：GTJA191适合持有3-10天的中短期预测，Alpha101侧重1-5天
7. **置信度定义**：
   - 80%+ = 三个智能体高度一致，量化+消息+风险三项均支持
   - 60-79% = 两项支持，一项中性
   - 40-59% = 存在明显分歧，谨慎参考
   - <40% = 剔除不推荐

## 使用示例

```
# 全市场筛选
run_swarm(preset="weekly_ashare_rank", variables={"sector_focus": "全市场", "top_n": "10", "hold_days": "5"})

# 只筛选光通信板块
run_swarm(preset="weekly_ashare_rank", variables={"sector_focus": "光通信/CPO", "top_n": "5", "hold_days": "3"})

# 煤炭+电力
run_swarm(preset="weekly_ashare_rank", variables={"sector_focus": "煤炭 电力", "top_n": "8", "hold_days": "5"})
```
