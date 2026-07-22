# -*- coding: utf-8 -*-
"""两个问题一起答：
A) 历史41个头部选择：若当时就有 新管线kline层规则(M2硬≥9.5 / M2'软带 / 过热ext≥3 / 长上影新候选)，
   坏案例(买入日收≤-5%)能拦几个？好案例误伤几个？
B) 47只×160日面板：『开盘正常盘中崩(陷阱)』的T日/T-1日前兆信号 lift 统计——
   候选信号: M2'带 / 高位长上影 / T-1大阴+T大阳(高位宽幅换手) / 高位放量 / 组合"""
import sys, re, pathlib
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

def feats(h, ti):
    """T=ti 时点特征(只用≤ti数据,无未来函数)"""
    c = h["收盘"].to_numpy(float); o = h["开盘"].to_numpy(float)
    hi = h["最高"].to_numpy(float); lo = h["最低"].to_numpy(float); v = h["成交量"].to_numpy(float)
    if ti < 60: return None
    chg1 = (c[ti]/c[ti-1]-1)*100
    chg_prev = (c[ti-1]/c[ti-2]-1)*100
    ret5 = (c[ti]/c[ti-5]-1)*100
    ret20 = (c[ti]/c[ti-20]-1)*100
    w_hi, w_lo = hi[ti-59:ti+1].max(), lo[ti-59:ti+1].min()
    rng60 = (c[ti]-w_lo)/(w_hi-w_lo)*100 if w_hi>w_lo else 50
    ma10 = c[ti-9:ti+1].mean()
    dist10 = (c[ti]/ma10-1)*100
    day_rng = hi[ti]-lo[ti]
    upsh = (hi[ti]-max(o[ti],c[ti]))/c[ti]*100          # 上影幅度(占收盘价%)
    vol_ratio = v[ti]/max(1e-9, v[ti-5:ti].mean())      # 当日量/前5日均量
    hi_pos = rng60>85 or ret20>30
    return dict(chg1=chg1, chg_prev=chg_prev, ret5=ret5, ret20=ret20, rng60=rng60,
                dist10=dist10, upsh=upsh, vol_ratio=vol_ratio, hi_pos=hi_pos,
                m2_hard=chg1>=9.5,
                m2_soft=(chg1<9.5) and (chg1>=7 or (chg1>=4.5 and hi_pos)),
                ext=( (2 if rng60>95 else (1 if rng60>88 else 0))
                     +(2 if dist10>20 else (1 if dist10>12 else 0))
                     +(1 if ret5>25 else 0) )>=3,
                upsh_sig=(upsh>=3 and hi_pos),                      # 候选: 高位长上影
                churn_sig=(chg_prev<=-3 and chg1>=4 and hi_pos),    # 候选: 高位大阴后大阳(剧烈换手)
                volhi_sig=(vol_ratio>=1.8 and hi_pos))              # 候选: 高位显著放量

# ---------- A) 历史头部选择回溯 ----------
RPT = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank\reports")
by_day = {}
for f in sorted(RPT.glob("ashare_rank_cn_2026-*.html")):
    by_day[f.name[15:25]] = f
sessions = []
for day, f in sorted(by_day.items()):
    html = f.read_text(encoding="utf-8", errors="ignore")
    codes = re.findall(r"<span class=tk>(\d{6})</span>", html) or re.findall(r"<td class=code>(\d{6})</td>", html)
    seen, top = set(), []
    for cd in codes:
        if cd not in seen:
            seen.add(cd); top.append(cd)
        if len(top) >= 3: break
    mb = re.search(r"买入日[^：]*：(\d{4}-\d{2}-\d{2})", html)
    if top and mb:
        sessions.append((day, mb.group(1), top))

kc = {}
def K(code):
    if code not in kc:
        try: kc[code] = eng.get_kline(code, bars=200)
        except Exception: kc[code] = None
    return kc[code]

FIX_D1 = {("2026-07-03","000034"): 3.25, ("2026-07-03","603588"): -10.0}  # 修正脏缓存
rows = []
for day, buy, top in sessions:
    for rank, code in enumerate(top, 1):
        h = K(code)
        if h is None: continue
        ds = [str(x)[:10] for x in h["日期"]]
        nxt = [i for i,d in enumerate(ds) if d >= buy]
        if not nxt or nxt[0]==0: continue
        ti = nxt[0]-1
        if ti+1 >= len(h): continue
        ft = feats(h, ti)
        if ft is None: continue
        c = h["收盘"].to_numpy(float); o = h["开盘"].to_numpy(float)
        d1 = (c[ti+1]/c[ti]-1)*100
        gap = (o[ti+1]/c[ti]-1)*100
        d1 = FIX_D1.get((ds[ti], code), d1)
        rows.append(dict(T=ds[ti], code=code, rank=rank, d1=d1, gap=gap, **ft))

bad = [r for r in rows if r["d1"]<=-5]
good = [r for r in rows if r["d1"]>-5]
def hits(rs, key): return sum(1 for r in rs if r[key])
print("== A) 历史%d个头部选择回溯(kline层规则,无未来函数) ==" % len(rows))
print("坏案例(买入日收≤-5%%): %d 个" % len(bad))
for r in bad:
    trig = [k for k in ("m2_hard","m2_soft","ext","upsh_sig","churn_sig","volhi_sig") if r[k]]
    print(f"  {r['T']} {r['code']} d1={r['d1']:+.1f}% gap={r['gap']:+.1f}% chg1={r['chg1']:+.1f} "
          f"60位={r['rng60']:.0f} 上影={r['upsh']:.1f}% 触发={trig or ['(无kline信号)']}")
print("\n| 规则 | 坏案例拦截 | 好案例误伤 |")
print("|---|---|---|")
for k, nm in [("m2_hard","M2硬(≥9.5%)"),("m2_soft","M2'软带"),("ext","过热ext≥3"),
              ("upsh_sig","高位长上影≥3%(候选)"),("churn_sig","高位大阴后大阳(候选)"),("volhi_sig","高位放量≥1.8x(候选)")]:
    print(f"| {nm} | {hits(bad,k)}/{len(bad)} | {hits(good,k)}/{len(good)} |")
anyk = lambda r: r["m2_hard"] or r["m2_soft"] or r["ext"]
anyk2 = lambda r: anyk(r) or r["upsh_sig"] or r["churn_sig"]
print(f"| 现有三规则任一 | {sum(1 for r in bad if anyk(r))}/{len(bad)} | {sum(1 for r in good if anyk(r))}/{len(good)} |")
print(f"| 现有+两候选任一 | {sum(1 for r in bad if anyk2(r))}/{len(bad)} | {sum(1 for r in good if anyk2(r))}/{len(good)} |")

# ---------- B) 面板陷阱前兆 lift ----------
spot = eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60","00","30"))]
codes = list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(48))
recs = []
for code in codes:
    h = K(code)
    if h is None or len(h) < 90: continue
    c = h["收盘"].to_numpy(float); o = h["开盘"].to_numpy(float)
    for ti in range(60, len(c)-1):
        ft = feats(h, ti)
        if ft is None: continue
        gap = (o[ti+1]/c[ti]-1)*100
        d1 = (c[ti+1]/c[ti]-1)*100
        trap = gap>-2 and d1<=-7
        recs.append((trap, ft))
n = len(recs); base = sum(1 for t,_ in recs if t)/n*100
print(f"\n== B) 面板前兆统计: {n}个股票日, 陷阱基础率={base:.2f}% ==")
print("| 信号(T日可观测) | 触发样本 | 触发后次日陷阱率 | lift | 触发后次日均收益 |")
print("|---|---|---|---|---|")
for k, nm in [("m2_soft","M2'兑现风险带"),("upsh_sig","高位长上影≥3%"),("churn_sig","高位大阴后大阳"),
              ("volhi_sig","高位放量≥1.8x"),("hi_pos","仅高位(对照)")]:
    sel = [(t,f) for t,f in recs if f[k]]
    if not sel: continue
    tr = sum(1 for t,_ in sel if t)/len(sel)*100
    # 次日均收益需重算: 用d1不在recs里,重跑太贵→只给陷阱率
    print(f"| {nm} | {len(sel)} | {tr:.2f}% | {tr/base:.1f}x | - |")
combo = [(t,f) for t,f in recs if (f["m2_soft"] or f["upsh_sig"] or f["churn_sig"])]
tr = sum(1 for t,_ in combo if t)/len(combo)*100
print(f"| 三信号任一(组合) | {len(combo)} | {tr:.2f}% | {tr/base:.1f}x | - |")
