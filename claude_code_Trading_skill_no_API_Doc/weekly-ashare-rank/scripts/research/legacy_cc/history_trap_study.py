# -*- coding: utf-8 -*-
"""历史选股复盘：每期报告前3名在买入日(T+1)的真实表现，统计『高能式陷阱』发生率。
   陷阱定义：T+1开盘跳空>−2%(竞价看不出问题、recheck放行) 但 当日收盘≤−7%(盘中崩)。"""
import sys, re, pathlib, collections
import numpy as np
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

RPT = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank\reports")
files = sorted(RPT.glob("ashare_rank_cn_2026-*.html"))
# 同一天多份取最后一份
by_day = {}
for f in files:
    day = f.name[15:25]
    by_day[day] = f

sessions = []
for day, f in sorted(by_day.items()):
    html = f.read_text(encoding="utf-8", errors="ignore")
    codes = re.findall(r"<span class=tk>(\d{6})</span>", html)
    if not codes:
        codes = re.findall(r"<td class=code>(\d{6})</td>", html)
    seen, top = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c); top.append(c)
        if len(top) >= 3:
            break
    mb = re.search(r"买入日[^：]*：(\d{4}-\d{2}-\d{2})", html)
    mt = re.search(r"数据截止[^：]*：(\d{4}-\d{2}-\d{2})", html)
    if not mt:
        mt = re.search(r"T[=＝]\s*(\d{4}-\d{2}-\d{2})", html)
    buy_date = mb.group(1) if mb else None
    as_of = mt.group(1) if mt else None
    if top:
        sessions.append({"file": f.name, "as_of": as_of, "buy": buy_date, "top3": top})

print(f"解析到 {len(sessions)} 期报告")
rows = []
kcache = {}
for s in sessions:
    for rank, code in enumerate(s["top3"], 1):
        if code not in kcache:
            try:
                kcache[code] = eng.get_kline(code, bars=160)
            except Exception:
                kcache[code] = None
        h = kcache[code]
        if h is None:
            continue
        ds = [str(x)[:10] for x in h["日期"]]
        as_of = s["as_of"]
        if not as_of and s["buy"]:
            # 用买入日反推T：买入日前一根K线
            nxt = [i for i, d in enumerate(ds) if d >= s["buy"]]
            if not nxt or nxt[0] == 0:
                continue
            ti = nxt[0] - 1
        elif as_of:
            if as_of not in ds:
                cand = [i for i, d in enumerate(ds) if d <= as_of]
                if not cand:
                    continue
                ti = cand[-1]
            else:
                ti = ds.index(as_of)
        else:
            continue
        if ti + 1 >= len(h):
            continue
        as_of = ds[ti]
        c0 = float(h["收盘"].iloc[ti])
        o1 = float(h["开盘"].iloc[ti + 1]); c1 = float(h["收盘"].iloc[ti + 1]); l1 = float(h["最低"].iloc[ti + 1])
        gap = (o1 / c0 - 1) * 100
        d1 = (c1 / c0 - 1) * 100
        low1 = (l1 / c0 - 1) * 100
        d3 = None
        if ti + 3 < len(h):
            d3 = (float(h["收盘"].iloc[ti + 3]) / c0 - 1) * 100
        trap = gap > -2 and d1 <= -7
        bad = gap > -2 and d1 <= -5
        rows.append({"date": as_of, "rank": rank, "code": code, "gap": gap, "d1": d1,
                     "low1": low1, "d3": d3, "trap": trap, "bad": bad})

print(f"\n有效样本(期×前3) = {len(rows)}")
print("| T日 | 名次 | 代码 | T+1跳空% | T+1收盘% | T+1最低% | T+3收盘% | 判定 |")
print("|---|---|---|---|---|---|---|---|")
for r in rows:
    tag = "💥陷阱(开盘正常盘中崩)" if r["trap"] else ("⚠大跌" if r["bad"] else ("跳空回避" if r["gap"] <= -2 and r["d1"] <= -5 else ""))
    d3s = f"{r['d3']:+.1f}" if r["d3"] is not None else "-"
    print(f"| {r['date']} | {r['rank']} | {r['code']} | {r['gap']:+.1f} | {r['d1']:+.1f} | {r['low1']:+.1f} | {d3s} | {tag} |")

a = np.array([[r["gap"], r["d1"], 1.0 if r["trap"] else 0.0, 1.0 if r["bad"] else 0.0] for r in rows])
d3v = np.array([r["d3"] for r in rows if r["d3"] is not None], dtype=float)
print(f"\n汇总: 样本{len(rows)}  T+1平均{a[:,1].mean():+.2f}%  T+1胜率{100*(a[:,1]>0).mean():.0f}%  "
      f"T+3平均{d3v.mean():+.2f}%")
print(f"『陷阱』(开盘>−2%但收≤−7%): {int(a[:,2].sum())}次 = {100*a[:,2].mean():.1f}%  |  "
      f"放宽到收≤−5%: {int(a[:,3].sum())}次 = {100*a[:,3].mean():.1f}%")
print(f"T+1收≤−5%合计(含跳空型): {int((a[:,1]<=-5).sum())}次 = {100*(a[:,1]<=-5).mean():.1f}%")
