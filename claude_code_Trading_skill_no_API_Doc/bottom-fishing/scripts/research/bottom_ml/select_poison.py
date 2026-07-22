# -*- coding: utf-8 -*-
"""挑毒月代表雷票: 每个代表毒月取2只"规则过线却先砸-8%"的高分票(最有迷惑性)。"""
import pathlib
import numpy as np, pandas as pd
ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
TH_TOTAL, TH_STOCK, TH_ATR = 18.0, 15.0, 4.0

p = pd.read_parquet(ROOT / "panel.parquet")
meta = pd.read_parquet(ROOT / "meta.parquet").set_index("code")
p["name"] = p.code.map(meta["name"])
p["qual"] = (((p.mkt_def) & (p.score >= TH_TOTAL)) | ((~p.mkt_def) & (p.stock_score >= TH_STOCK))) & (p.atr <= TH_ATR)

# 各月 雷率(规则过线子集)
print("=== 各月 规则过线子集 胜/雷 (n>=8) ===")
g = p[p.qual].groupby("month").agg(n=("outcome", "size"),
                                    win=("outcome", lambda x: (x == "win").mean() * 100),
                                    stop=("outcome", lambda x: (x == "stop").mean() * 100))
g = g[g.n >= 8].sort_values("stop", ascending=False)
for m, r in g.iterrows():
    print(f"  {m}  n={int(r.n):3d}  胜{r.win:4.0f}%  雷{r.stop:4.0f}%")

# 选定代表毒月(雷率高且跨越不同regime)
POISON = ["2024-01", "2025-03", "2026-02", "2026-05"]
print("\n=== 代表毒月 每月2只(规则过线 & 先砸-8%, 按分数降序) ===")
picks = []
for m in POISON:
    sub = p[(p.month == m) & (p.qual) & (p.outcome == "stop")].sort_values("score", ascending=False)
    if len(sub) < 2:  # 放宽: 底部区高分stop票
        sub = p[(p.month == m) & (p.outcome == "stop")].sort_values("score", ascending=False)
    for _, r in sub.head(2).iterrows():
        picks.append(r)
        print(f"  {m} | {r.code} {r.name} | 信号日T={r.d} 收{r.close} | 分{r.score}(个股{r.stock_score}) "
              f"ATR{r.atr} 回撤{r.dd60}% | {r.outcome}({int(r.hold_days)}天) | 行业={meta.loc[r.code,'industry']}")
pd.DataFrame([dict(month=r.month, code=r.code, name=r.name, T=r.d, close=r.close,
                   score=r.score, atr=r.atr, dd60=r.dd60, outcome=r.outcome,
                   days=int(r.hold_days), industry=meta.loc[r.code, "industry"]) for r in picks]
             ).to_csv(ROOT / "poison_picks.csv", index=False, encoding="utf-8-sig")
print("\nsaved → poison_picks.csv")
