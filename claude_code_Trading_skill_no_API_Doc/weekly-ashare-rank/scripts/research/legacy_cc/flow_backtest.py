# -*- coding: utf-8 -*-
"""主力资金流因子 mini 回测（双口径独立面板，不混算）：
   sig1=当日主力净占比, sig5=近5日主力净占比均值 → 未来1/3日收益 横截面Spearman IC"""
import sys, time
import numpy as np

sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\stock-diagnostic")
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import stock_diagnostic as sd

CAND = ["000034","603588","000938","002402","300436","301396","300102","603678"]

spot = sd.eng.get_spot(600)
df = spot[~spot["名称"].str.contains("ST", na=False)]
df = df[df["代码"].astype(str).str.startswith(("60","00","30"))]
codes = list(dict.fromkeys(CAND + list(df.sort_values("成交额", ascending=False)["代码"].astype(str).head(40))))[:44]

em_panel, sina_panel = {}, {}
for i, c in enumerate(codes):
    if i % 8 == 0:
        print(f"[progress] {i}/{len(codes)} em={len(em_panel)} sina={len(sina_panel)}", flush=True)
    try:
        ff = sd.fetch_fund_flow(c, days=90)  # 缓存的东财直接命中；限流则内部回退新浪
    except Exception as e:
        print("skip", c, str(e)[:40]); continue
    if not ff.get("available"):
        continue
    rows = [r for r in ff["days"] if r.get("main_ratio") is not None and r.get("chg") is not None]
    if ff.get("source") == "东财历史日线" and len(rows) >= 30:
        em_panel[c] = rows
    elif ff.get("source") == "新浪历史(独立源)" and len(rows) >= 25:
        sina_panel[c] = rows
    time.sleep(0.15)

def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0: return None
    return float(np.corrcoef(rx, ry)[0, 1])

def run_panel(panel, label, min_cs):
    by_date = {}
    for c, rows in panel.items():
        n = len(rows)
        for i in range(5, n - 3):
            sig1 = rows[i]["main_ratio"]
            sig5 = float(np.mean([rows[j]["main_ratio"] for j in range(i - 4, i + 1)]))
            fwd1 = rows[i + 1]["chg"]
            fwd3 = sum(rows[i + k]["chg"] for k in (1, 2, 3))
            by_date.setdefault(rows[i]["date"], []).append((sig1, sig5, fwd1, fwd3))
    res = {"当日占比→次日": [], "5日占比→次日": [], "5日占比→3日": []}
    q_top, q_bot = [], []
    for d, arr in sorted(by_date.items()):
        if len(arr) < min_cs: continue
        a = np.array(arr, dtype=float)
        for key, (si, fi) in {"当日占比→次日": (0, 2), "5日占比→次日": (1, 2), "5日占比→3日": (1, 3)}.items():
            ic = spearman(a[:, si], a[:, fi])
            if ic is not None: res[key].append(ic)
        k = max(2, len(arr) // 5)
        order = np.argsort(a[:, 1])
        q_bot.extend(a[order[:k], 2]); q_top.extend(a[order[-k:], 2])
    print(f"\n== [{label}] 面板 股数={len(panel)} 截面日数={len(res['5日占比→次日'])} (每截面≥{min_cs}只) ==")
    for k, v in res.items():
        v = np.array(v)
        if len(v):
            print(f"  {k}: IC均值={v.mean():+.4f}  ICIR={v.mean()/(v.std()+1e-9):+.2f}  正IC占比={100*(v>0).mean():.0f}%  n={len(v)}")
    if q_top and q_bot:
        print(f"  [分组] 5日主力占比Top20% 次日均={np.mean(q_top):+.3f}%  Bottom20%={np.mean(q_bot):+.3f}%  多空差={np.mean(q_top)-np.mean(q_bot):+.3f}%  ({len(q_top)}/{len(q_bot)})")

run_panel(em_panel, "东财口径", 8)
run_panel(sina_panel, "新浪口径(独立验证)", 10)

print("\n== 案例：07-03选股8只 · T前5日主力资金 vs 07-06表现 ==")
for c in CAND:
    rows = em_panel.get(c) or sina_panel.get(c)
    tag = "东财" if c in em_panel else ("新浪" if c in sina_panel else "无")
    if not rows:
        print(f"  {c}: 无数据"); continue
    upto = [r for r in rows if r["date"] <= "2026-07-03"]
    day76 = [r for r in rows if r["date"] == "2026-07-06"]
    if len(upto) >= 5:
        m5 = sum(r["main_yi"] for r in upto[-5:] if r["main_yi"] is not None)
        r5 = float(np.mean([r["main_ratio"] for r in upto[-5:]]))
        pos = sum(1 for r in upto[-5:] if (r["main_yi"] or 0) > 0)
        chg76 = day76[0]["chg"] if day76 else None
        m76 = day76[0]["main_yi"] if day76 else None
        print(f"  {c}[{tag}]: T前5日主力净={m5:+.2f}亿 占比均={r5:+.2f}% 正天数={pos}/5 → 07-06涨跌={chg76}% 07-06主力净={m76}亿")
