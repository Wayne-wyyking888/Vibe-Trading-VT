# -*- coding: utf-8 -*-
"""U2/U3 回测评定（kline层信号，无未来函数）：
   面板: 47只×160日 — 按 0/1/≥2 弱信号分组看 陷阱率/次日均收/大跌率，并算组合层面
        『基线全仓买 vs U2/U3政策(1信号半仓,≥2剔除,硬规则剔除)』的期望收益与尾部对比。
   历史: 41个头部选择 — 新政策的坏案例拦截 vs 好案例误伤 终表。"""
import sys, re, pathlib
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

def feats(h, ti):
    c = h["收盘"].to_numpy(float); o = h["开盘"].to_numpy(float)
    hi = h["最高"].to_numpy(float); lo = h["最低"].to_numpy(float)
    if ti < 60: return None
    chg1 = (c[ti]/c[ti-1]-1)*100
    chg_prev = (c[ti-1]/c[ti-2]-1)*100
    ret5 = (c[ti]/c[ti-5]-1)*100
    ret20 = (c[ti]/c[ti-20]-1)*100
    w_hi, w_lo = hi[ti-59:ti+1].max(), lo[ti-59:ti+1].min()
    rng60 = (c[ti]-w_lo)/(w_hi-w_lo)*100 if w_hi>w_lo else 50
    ma10 = c[ti-9:ti+1].mean()
    dist10 = (c[ti]/ma10-1)*100
    upsh = (hi[ti]-max(o[ti],c[ti]))/c[ti]*100
    hi_pos = rng60>85 or ret20>30
    m2_hard = chg1>=9.5
    ext = ((2 if rng60>95 else (1 if rng60>88 else 0))
           +(2 if dist10>20 else (1 if dist10>12 else 0))
           +(1 if ret5>25 else 0))>=3
    sig = []
    if (not m2_hard) and (chg1>=7 or (chg1>=4.5 and hi_pos)): sig.append("兑现带")
    if upsh>=3 and hi_pos: sig.append("长上影")
    if chg_prev<=-3 and chg1>=4 and hi_pos: sig.append("剧烈换手")
    return dict(m2_hard=m2_hard, ext=ext, sig=sig, chg1=chg1, rng60=rng60, upsh=upsh)

spot = eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60","00","30"))]
codes = list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(48))

kc = {}
def K(code):
    if code not in kc:
        try: kc[code] = eng.get_kline(code, bars=200)
        except Exception: kc[code] = None
    return kc[code]

# ---------- 面板 ----------
groups = {"硬规则剔除(M2硬/ext)": [], "0信号": [], "1信号(半仓)": [], "≥2信号(U3剔除)": []}
for code in codes:
    h = K(code)
    if h is None or len(h) < 90: continue
    c = h["收盘"].to_numpy(float); o = h["开盘"].to_numpy(float)
    for ti in range(60, len(c)-1):
        ft = feats(h, ti)
        if ft is None: continue
        gap = (o[ti+1]/c[ti]-1)*100
        d1 = (c[ti+1]/c[ti]-1)*100
        trap = 1.0 if (gap>-2 and d1<=-7) else 0.0
        big = 1.0 if d1<=-5 else 0.0
        row = (d1, trap, big)
        if ft["m2_hard"] or ft["ext"]:
            groups["硬规则剔除(M2硬/ext)"].append(row)
        elif len(ft["sig"]) >= 2:
            groups["≥2信号(U3剔除)"].append(row)
        elif len(ft["sig"]) == 1:
            groups["1信号(半仓)"].append(row)
        else:
            groups["0信号"].append(row)

print("== 面板评定 (47只×~130日, T+1收盘对T收盘, 无未来函数) ==")
print("| 组(U2/U3政策) | 样本 | 占比 | 次日均收% | 陷阱率(开正常盘中崩) | 次日≤-5%率 |")
print("|---|---|---|---|---|---|")
tot = sum(len(v) for v in groups.values())
for k, v in groups.items():
    a = np.array(v)
    if not len(a): continue
    print(f"| {k} | {len(a)} | {100*len(a)/tot:.1f}% | {a[:,0].mean():+.3f} | "
          f"{100*a[:,1].mean():.2f}% | {100*a[:,2].mean():.2f}% |")

# 组合对比：基线=全买(权重1)；新政策=0信号1.0 / 1信号0.5 / ≥2与硬规则0
base = np.concatenate([np.array(v)[:,0] for v in groups.values() if len(v)])
base_trap = np.concatenate([np.array(v)[:,1] for v in groups.values() if len(v)])
w = {"硬规则剔除(M2硬/ext)":0.0, "0信号":1.0, "1信号(半仓)":0.5, "≥2信号(U3剔除)":0.0}
num = den = trap_exp = 0.0
for k, v in groups.items():
    a = np.array(v)
    if not len(a): continue
    num += w[k]*a[:,0].sum(); den += w[k]*len(a); trap_exp += w[k]*a[:,1].sum()
print(f"\n[组合] 基线全仓: 次日均收 {base.mean():+.3f}%/仓位日  陷阱暴露 {100*base_trap.mean():.2f}%")
print(f"[组合] U2/U3政策: 次日均收 {num/den:+.3f}%/仓位日  陷阱暴露 {100*trap_exp/den:.2f}%  "
      f"(保留仓位 {100*den/tot:.0f}%)")

# ---------- 历史41案例 ----------
RPT = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank\reports")
by_day = {}
for f in sorted(RPT.glob("ashare_rank_cn_2026-*.html")):
    by_day[f.name[15:25]] = f
sessions = []
for day, f in sorted(by_day.items()):
    html = f.read_text(encoding="utf-8", errors="ignore")
    cds = re.findall(r"<span class=tk>(\d{6})</span>", html) or re.findall(r"<td class=code>(\d{6})</td>", html)
    seen, top = set(), []
    for cd in cds:
        if cd not in seen:
            seen.add(cd); top.append(cd)
        if len(top) >= 3: break
    mb = re.search(r"买入日[^：]*：(\d{4}-\d{2}-\d{2})", html)
    if top and mb:
        sessions.append((day, mb.group(1), top))

FIX_D1 = {("2026-07-03","000034"): 3.25, ("2026-07-03","603588"): -10.0}
# 已知补充信号(仅7/3期有资金流/事件数据): 高能=主力流出+事件负面, 神州=无
EXTRA = {("2026-07-03","603588"): ["主力流出","事件负面"]}
res = []
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
        c = h["收盘"].to_numpy(float)
        d1 = FIX_D1.get((ds[ti], code), (c[ti+1]/c[ti]-1)*100)
        sig = ft["sig"] + EXTRA.get((ds[ti], code), [])
        if ft["m2_hard"] or ft["ext"]:
            act = "硬剔除"
        elif len(sig) >= 2:
            act = "U3剔除"
        elif len(sig) == 1:
            act = "半仓"
        else:
            act = "全仓"
        res.append((ds[ti], code, d1, act, sig))

bad = [r for r in res if r[2] <= -5]
good = [r for r in res if r[2] > -5]
print(f"\n== 历史{len(res)}个头部选择 · 新政策终表 ==")
print("坏案例(买入日≤-5%):")
for r in bad:
    print(f"  {r[0]} {r[1]} d1={r[2]:+.1f}% → {r[3]}  信号={'+'.join(r[4]) or '无'}")
def cnt(rs, act): return sum(1 for r in rs if r[3]==act)
print("\n| 处置 | 坏案例(共%d) | 好案例(共%d) |" % (len(bad), len(good)))
print("|---|---|---|")
for act in ("硬剔除","U3剔除","半仓","全仓"):
    print(f"| {act} | {cnt(bad,act)} | {cnt(good,act)} |")
# 期望收益: 政策权重后坏案例损失 vs 好案例收益保留
wmap = {"硬剔除":0.0,"U3剔除":0.0,"半仓":0.5,"全仓":1.0}
base_m = np.mean([r[2] for r in res])
pol_num = sum(wmap[r[3]]*r[2] for r in res); pol_den = sum(wmap[r[3]] for r in res)
print(f"\n[历史组合] 基线全仓买41个: 平均买入日收益 {base_m:+.2f}%")
print(f"[历史组合] 新政策加权: 平均 {pol_num/max(pol_den,1e-9):+.2f}%/仓位日  保留仓位 {100*pol_den/len(res):.0f}%")
