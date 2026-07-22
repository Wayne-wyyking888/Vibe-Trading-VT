# -*- coding: utf-8 -*-
"""M2 硬剔除阈值研究：按 T日涨幅分档，拆解 T+1 跳空 vs 实际可买收益(开盘买入)
   口径：gap = T+1开盘/T收盘-1（买不到的部分）；o2c = T+1开盘买→T+1收盘；o2c3 = T+1开盘买→T+3收盘"""
import sys
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

spot = eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60", "00", "30"))]
codes = list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(48))

bands = {
    "基准:全体": [],
    "4.5-7%且高位(M2'软带)": [],
    "7-9.5%(现硬剔除下段)": [],
    "9.5-15%(涨停级)": [],
    ">=15%(20cm大长腿)": [],
}
n_ok = 0
for code in codes:
    try:
        h = eng.get_kline(code, bars=160)
    except Exception:
        continue
    if h is None or len(h) < 90:
        continue
    n_ok += 1
    o = h["开盘"].to_numpy(float); c = h["收盘"].to_numpy(float)
    hi = h["最高"].to_numpy(float); lo = h["最低"].to_numpy(float)
    for t in range(60, len(c) - 3):
        chg1 = (c[t] / c[t-1] - 1) * 100
        ret20 = (c[t] / c[t-20] - 1) * 100
        w_hi, w_lo = hi[t-59:t+1].max(), lo[t-59:t+1].min()
        rng = (c[t] - w_lo) / (w_hi - w_lo) * 100 if w_hi > w_lo else 50
        hi_pos = rng > 85 or ret20 > 30
        gap = (o[t+1] / c[t] - 1) * 100
        o2c = (c[t+1] / o[t+1] - 1) * 100
        o2c3 = (c[t+3] / o[t+1] - 1) * 100
        row = (gap, o2c, o2c3)
        bands["基准:全体"].append(row)
        if 4.5 <= chg1 < 7 and hi_pos:
            bands["4.5-7%且高位(M2'软带)"].append(row)
        elif 7 <= chg1 < 9.5:
            bands["7-9.5%(现硬剔除下段)"].append(row)
        elif 9.5 <= chg1 < 15:
            bands["9.5-15%(涨停级)"].append(row)
        elif chg1 >= 15:
            bands[">=15%(20cm大长腿)"].append(row)

print(f"样本股={n_ok} (成交额Top48 · 近160日 · T+1开盘买入口径 · 无未来函数)")
print("| 档 | n | T+1平均跳空% | 开盘买→当日收%均值 | 开盘买当日胜率 | 开盘买→T+3收%均值 | T+3胜率 | 开盘买当日≤-5%概率 | 开盘买3日≤-8%概率 |")
print("|---|---|---|---|---|---|---|---|---|")
for k, v in bands.items():
    v = np.array(v, dtype=float)
    if len(v) == 0:
        print(f"| {k} | 0 | - | - | - | - | - | - | - |"); continue
    g, oc, oc3 = v[:, 0], v[:, 1], v[:, 2]
    print(f"| {k} | {len(v)} | {g.mean():+.2f} | {oc.mean():+.3f} | {100*(oc>0).mean():.0f}% | "
          f"{oc3.mean():+.3f} | {100*(oc3>0).mean():.0f}% | {100*(oc<=-5).mean():.1f}% | {100*(oc3<=-8).mean():.1f}% |")
