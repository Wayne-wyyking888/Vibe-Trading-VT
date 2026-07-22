# -*- coding: utf-8 -*-
"""阈值扫描: 识底打分过线才推荐 → 各阈值的 n/覆盖/胜率/爆雷率/EV; 再给收益分布矩阵(3/5/10/20/30天)"""
import pandas as pd, numpy as np, pickle, math
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel480.pkl').sort_values(['code', 'd']).reset_index(drop=True)
D = pd.read_pickle(scr + r'\bottomD.pkl')
cache = pickle.load(open(scr + r'\race_cache.pkl', 'rb'))
g = p.groupby('code', group_keys=False)
p['o1'] = g.o.shift(-1)                     # T+1开盘=进场价
for N in [3, 5, 10, 20, 30]:
    p[f'fw{N}'] = g.c.shift(-(1 + N)) / p.o1 - 1   # 进场后N日收盘收益
p['min10'] = g.l.transform(lambda s: s.shift(-2).rolling(10, min_periods=1).min().shift(-9)) / p.o1 - 1  # 进场次日起10日最低

D = D.copy()
D['score'] = (8.6 * D.defensive.astype(float) + 5.2 * D.above_ma10.astype(float)
              + 4.5 * D.dif_up.astype(float) + 3.9 * ((D.rsv > 20) & (D.rsv <= 40)).astype(float)
              + 3.7 * ((D.dd60 <= -30) & (D.dd60 > -45)).astype(float)
              + 3.7 * D.above_ma5.astype(float) + 4.4 * D.gap_reclaim.astype(float)
              - 7.4 * (D.rsv <= 15).astype(float) - 6.3 * (D.downstk >= 4).astype(float)
              - 5.4 * D.zt20.astype(float) - 3.5 * (D.atr >= 7).astype(float)
              - 3.1 * (D.days_low <= 1).astype(float))
for col in ['fw3', 'fw5', 'fw10', 'fw20', 'fw30', 'min10', 'o1']:
    D[col] = p[col].reindex(D.index)

def wilson_hi(k, n, z=1.96):
    if n == 0: return 0
    ph = k / n; d = 1 + z * z / n
    return ((ph + z * z / (2 * n)) / d + z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d) * 100

def row(sub, lab):
    idxs = [i for i in sub.index if cache.get(i) is not None]
    outs = [cache[i] for i in idxs]
    n = len(outs)
    if n < 60:
        print(f'{lab:<30} n={n} 样本过小'); return
    w = sum(1 for o in outs if o == 1); s = sum(1 for o in outs if o == 0)
    t8 = (sub.min10 <= -0.08).mean() * 100
    days = sub.d.nunique(); per_day = n / max(days, 1)
    ev = w / n * 5 + s / n * (-8)
    print(f'{lab:<30} n={n:<5} 覆盖{days:>3}天(日均{per_day:.1f}只) 胜{w/n*100:5.1f}%  先砸-8%={s/n*100:4.1f}%(CI上限{wilson_hi(s,n):.1f}%)  10日触-8%={t8:4.1f}%  EV≈{ev:+.2f}%/笔')

print(f'== 阈值扫描(底部池n={len(D)}, score分布: p50={D.score.median():.1f} p80={D.score.quantile(.8):.1f} p95={D.score.quantile(.95):.1f} max={D.score.max():.1f}) ==')
for th in [5, 10, 15, 18, 21, 24]:
    row(D[D.score >= th], f'score≥{th}')
print('-- 叠加变体 --')
row(D[(D.score >= 18) & (D.atr <= 4)], 'score≥18 + ATR≤4')
row(D[(D.score >= 21) & (D.atr <= 4)], 'score≥21 + ATR≤4')
row(D[(D.score >= 18) & (D.defensive)], 'score≥18 + 防守日')
row(D[(D.score >= 24) & (D.atr <= 4)], 'score≥24 + ATR≤4')

print('\n== 收益分布矩阵: score≥21 组(过线才推荐), T+1开盘进场, N日后收盘(纯分布·不带止损) ==')
SEL = D[D.score >= 21]
edges = [-np.inf, -20, -10, -5, -2, 2, 5, 10, 20, np.inf]
labels = ['<-20', '-20~-10', '-10~-5', '-5~-2', '-2~+2', '+2~+5', '+5~+10', '+10~+20', '>+20']
print('%-6s %8s' % ('N日', 'n') + ''.join('%9s' % l for l in labels) + '%8s %8s' % ('均值', '中位'))
for N in [3, 5, 10, 20, 30]:
    x = SEL[f'fw{N}'].dropna() * 100
    cut = pd.cut(x, edges, labels=labels)
    freq = cut.value_counts(normalize=True).reindex(labels) * 100
    print('%-6s %8d' % (f'{N}天', len(x)) + ''.join('%8.1f%%' % v for v in freq.values) + '%+7.2f%% %+7.2f%%' % (x.mean(), x.median()))
print('\n对照·带-8%止损的现实版(score≥21): 先到+5%胜率见上表; 止损把左尾三桶截断为≈-8~-10%成交')
