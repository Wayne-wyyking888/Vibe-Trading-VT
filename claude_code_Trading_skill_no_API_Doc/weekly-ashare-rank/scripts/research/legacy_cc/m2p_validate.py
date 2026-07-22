# -*- coding: utf-8 -*-
"""M2' 阈值走样本验证：T日涨4.5~7% 且 (60日位>85 或 20日涨>30%) 的次日/3日表现 vs 对照组"""
import sys
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

spot = eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60", "00", "30"))]
codes = list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(48))

grp = {"B:4.5-7%且高位(新增剔除带)": [], "C:4.5-7%但不高位(保留)": [],
       "A:>=7%(旧规则已剔)": [], "D:全体基准": []}
n_ok = 0
for ci, code in enumerate(codes):
    try:
        h = eng.get_kline(code, bars=160)
    except Exception:
        continue
    if h is None or len(h) < 90:
        continue
    n_ok += 1
    c = h["收盘"].to_numpy(float)
    hi = h["最高"].to_numpy(float)
    lo = h["最低"].to_numpy(float)
    for t in range(60, len(c) - 3):
        chg1 = (c[t] / c[t-1] - 1) * 100
        ret20 = (c[t] / c[t-20] - 1) * 100
        w_hi, w_lo = hi[t-59:t+1].max(), lo[t-59:t+1].min()
        rng = (c[t] - w_lo) / (w_hi - w_lo) * 100 if w_hi > w_lo else 50
        f1 = (c[t+1] / c[t] - 1) * 100
        f3 = (c[t+3] / c[t] - 1) * 100
        grp["D:全体基准"].append((f1, f3))
        hi_pos = rng > 85 or ret20 > 30
        if chg1 >= 7:
            grp["A:>=7%(旧规则已剔)"].append((f1, f3))
        elif chg1 >= 4.5 and hi_pos:
            grp["B:4.5-7%且高位(新增剔除带)"].append((f1, f3))
        elif chg1 >= 4.5:
            grp["C:4.5-7%但不高位(保留)"].append((f1, f3))

print(f"样本股={n_ok}  (成交额Top48, 近160根日K, 无未来函数)")
print("| 组 | 样本数 | 次日均% | 次日胜率 | 3日均% | 次日≤-5%概率 | 次日≤-9%概率 | 次日5%分位 | 3日≤-8%概率 |")
print("|---|---|---|---|---|---|---|---|---|")
for k in ["B:4.5-7%且高位(新增剔除带)", "C:4.5-7%但不高位(保留)", "A:>=7%(旧规则已剔)", "D:全体基准"]:
    v = np.array(grp[k], dtype=float)
    if len(v) == 0:
        print(f"| {k} | 0 | - | - | - | - | - | - | - |"); continue
    f1, f3 = v[:, 0], v[:, 1]
    print(f"| {k} | {len(v)} | {f1.mean():+.3f} | {100*(f1>0).mean():.0f}% | {f3.mean():+.3f} | "
          f"{100*(f1<=-5).mean():.1f}% | {100*(f1<=-9).mean():.1f}% | {np.percentile(f1,5):+.2f} | "
          f"{100*(f3<=-8).mean():.1f}% |")
