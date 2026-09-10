# Agent 1.5 book-inspired price-volume filter study

这是抄底引擎后的研究型价量否决层。源码与预注册留在本目录；体积较大的行情、信号和报告写到：

`C:\Trading_analysis\research\bottom_agent15_book_filter`

它不会修改生产 `SKILL.md`、`bottom_fishing.py`、state 或报告。

## 运行

```powershell
python research.py fetch --refresh
python research.py analyze
python verify.py
```

或：

```powershell
python research.py all --refresh
```

主要产物：

- `fetch_audit.json`：股票池时点、来源、前复权、覆盖率、日期与源码 hash；
- `signals_raw_qualified.csv.gz`：完全不施加冷却的原始过线；
- `signals_cooldown5.csv.gz`：现行 N=5 后的候选；
- `shadow_event_audit.csv`：影子日志每行的 1.5 决策与重算结果；
- `summary.json` / `summary.md`：前后对比、分年、块自助区间与目标案例；
- `verification.json`：不导入研究脚本的独立机械复核。

先读 [PRE_REGISTRATION.md](PRE_REGISTRATION.md)。主规则失败时，不能拿两个灵敏度规则中较好的一条替代并宣称有效。

## 已知案例后的事后敏感性

`POSTHOC_20260827_F2_PLAN.md` 与 `posthoc_f2_gravity_audit.py` 专门检查为了抓住
2026-08-27 先导智能而把 F2 重心阈值放宽到 `-0.40 ATR` 的全局代价。它明确属于 post-hoc，
输出文件均带 `posthoc_` 前缀，不能回写或覆盖 v0.1 预注册结论。

## v0.2 shadow 裁定网格

`V02_CALIBRATION_PROTOCOL.md` 先冻结 F1—F5 共 50,764 个参数/组合候选、误杀约束和唯一排序规则；
`v02_shadow_grid_search.py` 只用未冷却且有 ✓/?/✗ 的成熟 shadow 行选择第一名，然后才打开全局历史。
`verify_v02.py` 独立检查网格没有全局字段、唯一候选确为 shadow 排名第一，以及 pre-shadow headline 可复算。
冻结结果见 [V02_RESULT.md](V02_RESULT.md)：唯一候选在 shadow 调参集改善，但在 pre-shadow 全局段恶化，
结论为 `shadow_overfit_do_not_add_to_production`。

```powershell
python v02_shadow_grid_search.py
python verify_v02.py
```

主回测完成后，可把已冻结的唯一候选应用到尚未结算、未冷却且裁定为 `✓` 的 shadow 行：

```powershell
python v02_open_check_watchlist.py
```

它生成 `v02_open_check_watchlist.csv/.md/.json`，其中涨跌按 T+1 开盘价到冻结行情最新收盘价计算；
这些未结算行只用于观察，不回流到 v0.2 网格排名。

## v0.3 五雷优先与逆时间验证

`V03_POSTHOC_PROTOCOL.md` 冻结523,864个扩展 F1—F5 组合。唯一规则只用2026-07-14以后61笔
成熟、有裁定且未冷却的 shadow 行选择；按用户修订A，先最大化过滤5笔 `✓ stop`，再最小化误杀
`✓ win`，允许最多误杀5笔。规则冻结后才打开2024—2026-07-12的 N=5 实际候选验证。

结果见 [V03_RESULT.md](V03_RESULT.md)：唯一规则在 shadow 内过滤5/5，但逆时间验证的合计胜率下降
1.043pp、暴雷率上升0.778pp，裁定为 `fails_v03_historical_validation_research_only`，不得进入生产。

```powershell
python v03_grid_search.py
python verify_v03.py
```

完整网格、逐行审计、结构化摘要和独立验收分别写到输出目录中的 `v03_grid_all.csv.gz`、
`v03_shadow_row_audit.csv`、`v03_summary.json/.md`、`v03_verification.json`。
