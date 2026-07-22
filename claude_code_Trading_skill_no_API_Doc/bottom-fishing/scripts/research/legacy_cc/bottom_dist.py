# -*- coding: utf-8 -*-
"""推荐线(手工分>=18+ATR<=4)的胜率/爆雷率分布: 市况拆分 / 分数带矩阵 / 按月"""
import pandas as pd, numpy as np, pickle
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
D = pd.read_pickle(scr + r'\bottomD.pkl').copy()
cache = pickle.load(open(scr + r'\race_cache.pkl', 'rb'))
D['out'] = [cache.get(i) for i in D.index]
D = D[D.out.notna()]
D['win'] = (D.out == 1).astype(float); D['stopped'] = (D.out == 0).astype(float)
D['hand'] = (8.6 * D.defensive.astype(float) + 5.2 * D.above_ma10.astype(float)
             + 4.5 * D.dif_up.astype(float) + 3.9 * ((D.rsv > 20) & (D.rsv <= 40)).astype(float)
             + 3.7 * ((D.dd60 <= -30) & (D.dd60 > -45)).astype(float)
             + 3.7 * D.above_ma5.astype(float) + 4.4 * D.gap_reclaim.astype(float)
             - 7.4 * (D.rsv <= 15).astype(float) - 6.3 * (D.downstk >= 4).astype(float)
             - 5.4 * D.zt20.astype(float) - 3.5 * (D.atr >= 7).astype(float)
             - 3.1 * (D.days_low <= 1).astype(float))
D['hand_nodef'] = D.hand - 8.6 * D.defensive.astype(float)   # 去掉市况项的纯个股修复分

def cell(sub):
    n = len(sub)
    if n < 60: return f'n={n}·小'
    return f'{sub.win.mean()*100:.0f}%/{sub.stopped.mean()*100:.0f}% (n={n})'

print('== A. 纯个股修复分(不含市况项) × 市况 矩阵: 胜率/爆雷率 ==')
bands = [(-99, 0, '<0'), (0, 9, '0~9'), (9, 15, '9~15'), (15, 99, '≥15')]
print('%-12s %-24s %-24s' % ('个股分带', '防守日', '非防守日'))
for lo, hi, lab in bands:
    m = (D.hand_nodef > lo) & (D.hand_nodef <= hi)
    print('%-12s %-24s %-24s' % (lab, cell(D[m & D.defensive]), cell(D[m & ~D.defensive])))

L = D[(D.hand >= 18) & (D.atr <= 4)]
Ln = D[(D.hand_nodef >= 15) & (D.atr <= 4)]   # 不靠市况项也过线的
cut = sorted(D.d.unique())[250]
print(f'\n== B. 推荐线(总分≥18+ATR≤4) 市况拆分 ==')
print('全期   防守日:', cell(L[L.defensive]), ' 非防守日:', cell(L[~L.defensive]),
      f' 防守日占比{L.defensive.mean()*100:.0f}%')
T = L[L.d >= cut]
print('样本外 防守日:', cell(T[T.defensive]), ' 非防守日:', cell(T[~T.defensive]))
print('替代口径(个股分≥15+ATR≤4,不吃市况加分) 防守:', cell(Ln[Ln.defensive]), ' 非防守:', cell(Ln[~Ln.defensive]))

print('\n== C. 推荐线按月分布 ==')
L = L.copy(); L['ym'] = L.d.str[:7]
print('%-9s %6s %7s %8s %10s' % ('月份', 'n', '胜率', '爆雷率', '防守日占比'))
for ym, gg in L.groupby('ym'):
    if len(gg) < 15: continue
    print('%-9s %6d %6.0f%% %7.0f%% %9.0f%%' % (ym, len(gg), gg.win.mean()*100, gg.stopped.mean()*100, gg.defensive.mean()*100))
