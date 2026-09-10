# Agent 1.5 v0.2 结果

状态：完成；`shadow_overfit_do_not_add_to_production`。  
协议 SHA-256：`91B6ED7B0DCA2AF83E7212CB5A5901576516AE2074F939C4728BA03879E49097`。  
本研究没有修改生产 `SKILL.md`、引擎、state 或 Agent②/③。

## 唯一候选

共机械枚举 50,764 个候选，2,729 个通过 shadow 保留约束。只按 61 笔未冷却、有裁定且成熟的
shadow 行排序后，唯一第一名为：

`F1=-;F2=F2_B11_G025;F3=F3_V180_E025;F4=-;F5=-;k=1`

即 F2（20日阳线至少11根且价格重心下降不小于 0.25 ATR）或 F3（10日峰值量比至少1.8、
价格效率不高于0.25且10日收益不高于2%）任一触发就否决。全局回测字段没有进入网格文件或排序。

## Shadow 调参集

|范围|过滤前 W/S/T|过滤后 W/S/T|胜率变化|雷率变化|EV变化|
|---|---:|---:|---:|---:|---:|
|✓|19/5/1|18/2/1|+9.714pp|-10.476pp|+1.324pp|
|✓/?/✗ 全部|45/12/4|37/5/4|+6.664pp|-8.803pp|+1.037pp|

在 5 笔 `✓ stop` 中，v0.2 过滤中国长城（2026-08-12）、先导智能（2026-08-27）和天赐材料
（2026-08-28）；漏掉先导智能（2026-08-07）和汇川技术（2026-08-26）。同时误杀 1 笔 `✓ win`：
中国中免（2026-07-17）。

## 打开全局后的隔离检验

|范围|成熟样本 N→保留 N|胜率变化|雷率变化|EV变化|误杀赢家|
|---|---:|---:|---:|---:|---:|
|原始过线全期|5,882→4,670|-0.563pp|+0.553pp|-0.072pp|21.353%|
|N=5 全期|1,837→1,470|-0.112pp|+0.264pp|-0.027pp|20.124%|
|N=5，T<2026-07-13|1,770→1,420|-0.227pp|+0.350pp|-0.039pp|20.075%|
|2024|922→755|-1.561pp|+1.024pp|-0.160pp|20.588%|
|2025|571→441|+1.059pp|+0.078pp|+0.047pp|21.678%|
|2026|344→274|+3.423pp|-3.367pp|+0.441pp|16.143%|

pre-shadow 月份块自助 95% 区间：雷率增量 `[-1.212, +1.960]pp`，EV 增量
`[-0.227, +0.161]pp`，均跨零。v0.2 在 2026 段看似有效，但在与 shadow 调参事件不重叠的
pre-shadow 段以及 2024 段恶化，因此没有通过冻结的全局门槛。不得查看全局后改选排行榜第二名。

## 未结算 ✓ 观察表

另把唯一候选应用到 `cooldown=false + judge=✓ + outcome=open` 的 17 笔事件：过滤4笔、保留13笔。
行情冻结截至 2026-09-09；涨跌按 T+1 开盘到冻结行情最新收盘计算。该表不回流到调参：

- `C:\Trading_analysis\research\bottom_agent15_book_filter\v02_open_check_watchlist.md`
- `C:\Trading_analysis\research\bottom_agent15_book_filter\v02_open_check_watchlist.csv`
- `C:\Trading_analysis\research\bottom_agent15_book_filter\v02_open_check_watchlist.json`

## 产物与复算

- 完整网格：`v02_grid_all.csv.gz`
- shadow 排名前200：`v02_shadow_leaderboard.csv`
- 逐行选中候选审计：`v02_shadow_calibration_rows.csv`
- 全局结果：`v02_summary.json` / `v02_summary.md`
- 独立验收：`v02_verification.json`

以上大文件均位于 `C:\Trading_analysis\research\bottom_agent15_book_filter`。主要限制是只有5笔 `✓ stop`
参与目标排序、50,764 次多重检验风险很高，而且全局股票池不是 point-in-time，存在幸存者及流动性快照偏差。
