# -*- coding: utf-8 -*-
"""pool=200 vs 400(=今日全universe) 对比 + Top20的粗排名次分布"""
import sys, json
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as eng

S = r"C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\cfc901c9-5c57-49a6-b073-46c243757db5\scratchpad"
b = json.load(open(S + r"\ab_pool200.json", encoding="utf-8"))
c = json.load(open(S + r"\ab_pool400.json", encoding="utf-8"))
print(f"pool200: scored {b['scored']} | pool400: scored {c['scored']} (universe {c['universe_after_filter']})")

cb = [(x["code"], x["name"], x.get("rank_score", x["score"])) for x in b["candidates"]]
cc = [(x["code"], x["name"], x.get("rank_score", x["score"])) for x in c["candidates"]]
sb, sc = set(x[0] for x in cb), set(x[0] for x in cc)
print(f"\nTop20重合: {len(sb & sc)}/20   Top8重合: {len(set(x[0] for x in cb[:8]) & set(x[0] for x in cc[:8]))}/8")
print("pool200 Top8:", [f"{x[1]}({x[2]})" for x in cb[:8]])
print("pool400 Top8:", [f"{x[1]}({x[2]})" for x in cc[:8]])
new = [x for x in cc if x[0] not in sb]
print(f"\n400池新入Top20的({len(new)}只):", [f"{x[1]}({x[2]})" for x in new])

# 粗排名次分布
spot = eng.get_spot(600)
filt = eng.prefilter(spot)
pres = eng.prescore(filt)
order = [str(x) for x in pres["代码"].astype(str)]
rank_of = {code: i + 1 for i, code in enumerate(order)}
for label, lst in (("pool200", cb), ("pool400", cc)):
    ranks = [rank_of.get(x[0], -1) for x in lst]
    print(f"\n{label} Top20 的粗排名次: {ranks}")
    print(f"  中位数={sorted(r for r in ranks if r>0)[len([r for r in ranks if r>0])//2]}  "
          f">200名的有 {sum(1 for r in ranks if r>200)} 只  >150名的有 {sum(1 for r in ranks if r>150)} 只")
