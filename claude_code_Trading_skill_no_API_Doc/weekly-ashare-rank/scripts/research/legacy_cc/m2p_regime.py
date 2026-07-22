# -*- coding: utf-8 -*-
"""M2' 条件化验证：B组(4.5-7%+高位) 按 大盘(上证)是否在MA20上方 分层看次日尾部风险"""
import sys, urllib.request, json as _json
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

# 上证指数日K(腾讯)
u = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,260,"
d = _json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
k = d["data"]["sh000001"].get("day") or d["data"]["sh000001"].get("qfqday")
idx_close = {r[0]: float(r[2]) for r in k}
dates = sorted(idx_close)
closes = np.array([idx_close[dd] for dd in dates])
ma20 = {dates[i]: (closes[i] >= closes[max(0, i-19):i+1].mean()) for i in range(len(dates))}

spot = eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60", "00", "30"))]
codes = list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(48))

layers = {"B|大盘MA20上": [], "B|大盘MA20下": []}
for code in codes:
    try:
        h = eng.get_kline(code, bars=160)
    except Exception:
        continue
    if h is None or len(h) < 90:
        continue
    c = h["收盘"].to_numpy(float)
    hi = h["最高"].to_numpy(float); lo = h["最低"].to_numpy(float)
    ds = [str(x)[:10] for x in h["日期"]] if "日期" in h.columns else [str(x)[:10] for x in h.index]
    for t in range(60, len(c) - 1):
        chg1 = (c[t] / c[t-1] - 1) * 100
        if not (4.5 <= chg1 < 7):
            continue
        ret20 = (c[t] / c[t-20] - 1) * 100
        w_hi, w_lo = hi[t-59:t+1].max(), lo[t-59:t+1].min()
        rng = (c[t] - w_lo) / (w_hi - w_lo) * 100 if w_hi > w_lo else 50
        if not (rng > 85 or ret20 > 30):
            continue
        above = ma20.get(ds[t])
        if above is None:
            continue
        f1 = (c[t+1] / c[t] - 1) * 100
        layers["B|大盘MA20上" if above else "B|大盘MA20下"].append(f1)

print("| 层 | 样本数 | 次日均% | 胜率 | ≤-5%概率 | ≤-9%概率 | 5%分位 |")
print("|---|---|---|---|---|---|---|")
for kk, v in layers.items():
    v = np.array(v, dtype=float)
    if len(v) == 0:
        print(f"| {kk} | 0 | - | - | - | - | - |"); continue
    print(f"| {kk} | {len(v)} | {v.mean():+.3f} | {100*(v>0).mean():.0f}% | "
          f"{100*(v<=-5).mean():.1f}% | {100*(v<=-9).mean():.1f}% | {np.percentile(v,5):+.2f} |")
