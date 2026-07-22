# -*- coding: utf-8 -*-
"""对照组: 抽毒月里"同样高分过线、但先到+5%"的赢家, 与输家比红旗命中率。"""
import pathlib
import pandas as pd
ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
TH_TOTAL, TH_STOCK, TH_ATR = 18.0, 15.0, 4.0

p = pd.read_parquet(ROOT / "panel.parquet")
meta = pd.read_parquet(ROOT / "meta.parquet").set_index("code")
p["qual"] = (((p.mkt_def) & (p.score >= TH_TOTAL)) | ((~p.mkt_def) & (p.stock_score >= TH_STOCK))) & (p.atr <= TH_ATR)

# 毒月池(规则过线雷率高的月份)
POISON = ["2024-01", "2024-05", "2024-06", "2026-02", "2026-05"]
print("=== 毒月赢家(规则过线 & 先到+5%, 按分数降序) — 对照输家 ===")
for m in POISON:
    sub = p[(p.month == m) & (p.qual) & (p.outcome == "win")].sort_values("score", ascending=False)
    nwin = len(sub)
    ntot = len(p[(p.month == m) & (p.qual)])
    print(f"\n[{m}] 过线赢家 {nwin}/{ntot}只:")
    for _, r in sub.head(3).iterrows():
        ind = meta.loc[r.code, "industry"] if r.code in meta.index else "?"
        print(f"   {r.code} | T={r.d} 收{r.close} | 分{r.score}(个股{r.stock_score}) "
              f"ATR{r.atr} 回撤{r.dd60}% | win({int(r.hold_days)}天) | {ind}")
