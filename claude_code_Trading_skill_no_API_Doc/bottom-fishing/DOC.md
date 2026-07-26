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
│  ├─ TOXIC_RISK_WARNING_PROTOCOL.md # Agent③五域市场风险nowcast/HTML shadow warning
│  └─ RESEARCH_LEDGER.md       # 规则→脚本→数据→偏差→采纳/否决 provenance
├─ scripts/research/
│  ├─ legacy_cc/          # 从 Claude Code scratchpad 原样抢救的历史实验
│  ├─ bottom_ml/          # CatBoost/purged-CV 源码；大 parquet 仍在外部数据目录
│  ├─ holiday_event_study/ # 2024—2026节假日前后事件研究
│  └─ toxic_month_web_study/ # 毒月真实集中窗、事件账本与预警设计
├─ README.md             # 研究存档: 全部方法论数字/否决清单/毒月专项
├─ DOC.md                # 本文件(操作文档)
└─ reports/              # HTML报告(gitignore, bottom_cn_完整北京时间戳[_裁定版].html)
C:\Trading_analysis\data\
├─ bottom_latest.json        # 最新一期结果(供回填/复查)
├─ bottom_adjudication.json  # T日裁定、Agent③预警、搜索审计和Codex结构化证据
└─ bottom_shadow_log.jsonl   # 影子日志: 每笔过线票+def_days/idx_rsv影子字段+结算结果
```

## 引擎命令行
```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom --            # 扫描
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\run_engine.py" bottom -- --review   # 对账
```

## 数据流水线
东财快照(经weekly引擎get_spot,带缓存/新浪兜底) → 剔ST/科创/北交 → 腾讯qfq日K(140根/只) →
底部区(回撤≥20%+60位≤25) → 修复确认打分 → 双路径推荐线 → F10种子(东财datacenter,仅过线票) →
Agent②个股六维裁定 + Agent③市场五域风险nowcast → 裁定/预警HTML → 执行方案(权威交易日历给真实买入/离场日期)
→ stdout+JSON+HTML+影子日志。
指数: 创业板(防守日/def_days/大盘RSV, 后两者为影子字段不进规则)。

报告文件名严格使用 `bottom_cn_YYYY-MM-DD_HH-MM-SS[_裁定版].html`：日期与时分秒均来自同一个
UTC+8 的 `generated_at` / `adjudicated_at`。业务截止日 T 只写入正文与 JSON，不再与生成时钟混拼。

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
- **Agent③=毒月 Web 预警官（每次扫描必跑，零候选也不跳过）**：
  固定搜索排期宏观政策、国内监管与流动性、海外地缘与贸易、跨资产压力、长假信息缺口五域。
  T日已公开且仍活跃的风险可形成报告级 warning；只有行业/产品/成本/海外收入暴露能够明确对应时才下沉到个股。
  排期事件只给 med 黄色提示；high 要求官方源和至少两个独立 origin。每次运行都把五域检索到实际完成时点，
  每条事件及五域综合必须写事实、共识、基准/上下行情景、传导链、观察变量和失效条件。T 后新突发只进安全增量，
  但必须进入运行时点评估并在 HTML 标“T后”；不倒灌T日裁定。当前固定 `mode=shadow`，不改分、不禁买、
  不影响 Agent② 裁定。五域完成后必须再合成为A股下一交易日和未来1—5日的大概走势、风格、相对受益/
  承压板块与触发条件；白话卡片放在最终HTML顶部“市况”正下方。
  结构化数据写入 `codex_audit.toxic_risk_warning`，详见
  `references/TOXIC_RISK_WARNING_PROTOCOL.md`。
- Agent④=复核官: 价格新鲜度/来源可追溯/口径一致。

裁定文件写完后，以下命令同时验 Agent② 和 Agent③，失败时不得生成裁定版：

```powershell
python "C:\Trading_analysis\Vibe-Trading-VT\codex_acceptance\acceptance.py" validate-bottom-search `
  --result "C:\Trading_analysis\data\bottom_latest.json" `
  --audit "C:\Trading_analysis\data\bottom_adjudication.json"
```

## 硬纪律速查（数字出处=README）
| 条款 | 内容 |
|---|---|
| 0过线=空手 | 不降格凑数(历史约6成日子无票) |
| 仓位 | 单票≤3.5%(跌停封死风险>动量票) |
| 执行三刀 | T+1开盘进场(高开>3%放弃) / -8%条件单成交即挂 / 买入日收≤-5%次日开盘出 |
| 目标 | +5%落袋半仓 / +10%清(EV最优) / 最晚T+20收盘离场 |
| 毒月熔断 | 月度亏损-3%停做 ∥ 近20笔雷率≥30%停(--review自动检测) |
| Agent③预警 | 五域搜索至实际运行时点并映射A股T+1/未来1—5日；顶部shadow卡片，不改变分数/裁定/仓位 |
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
  `scripts/research/`。最新：2026-07-23 毒月真实交易日集中窗和节假日归因已接入 Agent③ shadow warning，
  但尚未升级为交易 gate。
