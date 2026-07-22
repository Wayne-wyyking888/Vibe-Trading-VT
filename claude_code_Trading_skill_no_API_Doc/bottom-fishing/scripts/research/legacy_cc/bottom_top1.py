# -*- coding: utf-8 -*-
"""综合识底打分的 Top-1/Top-3 测试(权重=各因子Δ胜率, 同窗拟合=乐观口径)"""
import pandas as pd, numpy as np, pickle
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
D = pd.read_pickle(scr + r'\bottomD.pkl')
cache = pickle.load(open(scr + r'\race_cache.pkl', 'rb'))

D = D.copy()
D['score'] = (8.6 * D.defensive.astype(float) + 5.2 * D.above_ma10.astype(float)
              + 4.5 * D.dif_up.astype(float) + 3.9 * ((D.rsv > 20) & (D.rsv <= 40)).astype(float)
              + 3.7 * ((D.dd60 <= -30) & (D.dd60 > -45)).astype(float)
              + 3.7 * D.above_ma5.astype(float) + 4.4 * D.gap_reclaim.astype(float)
              - 7.4 * (D.rsv <= 15).astype(float) - 6.3 * (D.downstk >= 4).astype(float)
              - 5.4 * D.zt20.astype(float) - 3.5 * (D.atr >= 7).astype(float)
              - 3.1 * (D.days_low <= 1).astype(float))

def wr(idxs, lab):
    outs = [cache.get(i) for i in idxs if cache.get(i) is not None]
    n = len(outs)
    if n < 40:
        print(f'{lab:<34} n={n} 样本过小'); return
    w = sum(1 for o in outs if o == 1) / n * 100; s = sum(1 for o in outs if o == 0) / n * 100
    print(f'{lab:<34} n={n:<5} 胜{w:.1f}%  止损{s:.1f}%')

rng = np.random.RandomState(7)
top1, top3, rnd1, names = [], [], [], []
for d, gd in D.groupby('d'):
    if len(gd) < 3: continue
    gs = gd.sort_values('score', ascending=False)
    top1.append(gs.index[0]); top3.extend(gs.index[:3])
    rnd1.append(gd.sample(1, random_state=rng).index[0])
    names.append((d, gs.iloc[0]['code']))
print('== 全期(每天在底部池打分) ==')
wr(top1, '打分Top-1(每天1只)')
wr(top3, '打分Top-3篮子')
wr(rnd1, '随机1只(对照)')
same = sum(1 for a, b in zip(names[:-1], names[1:]) if a[1] == b[1])
print(f'Top-1连续性: 相邻日同一只 {same}/{len(names)-1} = {same/(len(names)-1)*100:.0f}%')

print('\n== 只在防守日出手(策略实际形态) ==')
Dd = D[D.defensive]
t1, t3, r1 = [], [], []
for d, gd in Dd.groupby('d'):
    if len(gd) < 3: continue
    gs = gd.sort_values('score', ascending=False)
    t1.append(gs.index[0]); t3.extend(gs.index[:3])
    r1.append(gd.sample(1, random_state=rng).index[0])
wr(t1, '防守日·打分Top-1')
wr(t3, '防守日·打分Top-3篮子')
wr(r1, '防守日·随机1只(对照)')

print('\n== 打分分位数单调性检验(全底部池) ==')
D['q'] = pd.qcut(D.score, 5, labels=False, duplicates='drop')
for q in sorted(D.q.dropna().unique()):
    wr(D[D.q == q].index, f'score第{int(q)+1}/5档(高=好)')
