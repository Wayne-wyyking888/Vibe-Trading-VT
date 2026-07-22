# -*- coding: utf-8 -*-
"""稳健线(不降档)下: 1只 vs 2只 的纯爆雷事件概率(含同日聚集修正), 不谈仓位"""
import pandas as pd, numpy as np
from itertools import combinations
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel.pkl').sort_values(['code', 'd']).copy()
g = p.groupby('code', group_keys=False)
p['minlow3'] = g.l.transform(lambda s: s.shift(-1).rolling(3, min_periods=1).min().shift(-2)) / p.c - 1
p['ret5'] = g.c.transform(lambda s: s.pct_change(5)) * 100
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean()); p['volx'] = p.v / p.vma20
p = p.dropna(subset=['minlow3', 'atr', 'volx', 'ret5'])
hi_th = p.groupby('d').ret20.transform(lambda s: s.quantile(0.70))
p['himom'] = p.ret20 >= hi_th
p['sig'] = ((p.upsh >= 3) & ((p.pos60 > 75) | (p.ret20 > 20))).astype(int) + p.zt5.astype(int) + (p.pos60 > 88).astype(int) + (p.ret20 > 30).astype(int)
p['boom'] = p.minlow3 <= -0.08

cells = [
    ('稳健线(ATR≤4+5日≤10+volx≤2+60位≤85)', p[(p.himom) & (~p.defensive) & (p.atr <= 4) & (p.ret5 <= 10) & (p.volx <= 2) & (p.pos60 <= 85) & (p.sig == 0)]),
    ('稳健线·不含sig条件(原C格子)', p[(p.himom) & (~p.defensive) & (p.atr <= 4) & (p.ret5 <= 10) & (p.volx <= 2) & (p.pos60 <= 85)]),
    ('ATR≤5线(himom+非防守+0弱信号)', p[(p.himom) & (~p.defensive) & (p.atr <= 5) & (p.sig == 0)]),
    ('组合拳线(himom+非防守+0弱信号+ATR≤6.2)', p[(p.himom) & (~p.defensive) & (p.atr <= 6.2) & (p.sig == 0)]),
]
import math
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    ph = k / n
    d = 1 + z*z/n
    c = (ph + z*z/(2*n)) / d
    hw = z*math.sqrt(ph*(1-ph)/n + z*z/(4*n*n)) / d
    return (max(0, c-hw)*100, (c+hw)*100)

for lab, cell in cells:
    n, k = len(cell), int(cell.boom.sum())
    p1 = k / n * 100 if n else 0
    lo, hiC = wilson(k, n)
    # 同日抽两只的经验联合概率(聚集修正): 对每个有>=2只合格票的日期, 枚举所有两两组合
    days = cell.groupby('d')
    pair_any, pair_both, npair = 0, 0, 0
    for _, gd in days:
        m = gd.boom.values
        if len(m) < 2: continue
        for a, b in combinations(range(len(m)), 2):
            npair += 1
            pair_any += (m[a] or m[b]); pair_both += (m[a] and m[b])
    pa = pair_any / npair * 100 if npair else float('nan')
    pb = pair_both / npair * 100 if npair else float('nan')
    indep = (1 - (1 - k/n)**2) * 100 if n else 0
    d_per = cell.d.nunique()
    print(f'{lab}')
    print(f'  单只: 爆雷率 {p1:.1f}% (k={k}/n={n}, 95%CI {lo:.1f}–{hiC:.1f}%)  覆盖{d_per}个交易日, 平均每日合格 {n/max(d_per,1):.1f} 只')
    print(f'  两只(同日实抽{npair}对): 至少一雷 {pa:.1f}%  (独立假设算 {indep:.1f}%)   双雷 {pb:.2f}%')
    print()
