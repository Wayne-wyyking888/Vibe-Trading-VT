# -*- coding: utf-8 -*-
"""打分机制优化·走样本检验: 前250天训练(多元LPM学权重) vs 手工分, 后~150天测试
回答: ①手工分OOS还剩多少 ②学权重能提多少"""
import pandas as pd, numpy as np, pickle
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
D = pd.read_pickle(scr + r'\bottomD.pkl').copy()
cache = pickle.load(open(scr + r'\race_cache.pkl', 'rb'))
D['out'] = [cache.get(i) for i in D.index]
D = D[D.out.notna()]
D['win'] = (D.out == 1).astype(float)
D['stopped'] = (D.out == 0).astype(float)

# 手工分(同前)
D['hand'] = (8.6 * D.defensive.astype(float) + 5.2 * D.above_ma10.astype(float)
             + 4.5 * D.dif_up.astype(float) + 3.9 * ((D.rsv > 20) & (D.rsv <= 40)).astype(float)
             + 3.7 * ((D.dd60 <= -30) & (D.dd60 > -45)).astype(float)
             + 3.7 * D.above_ma5.astype(float) + 4.4 * D.gap_reclaim.astype(float)
             - 7.4 * (D.rsv <= 15).astype(float) - 6.3 * (D.downstk >= 4).astype(float)
             - 5.4 * D.zt20.astype(float) - 3.5 * (D.atr >= 7).astype(float)
             - 3.1 * (D.days_low <= 1).astype(float))

# 特征(混合dummy+连续)
F = pd.DataFrame(index=D.index)
F['defensive'] = D.defensive.astype(float)
F['above_ma10'] = D.above_ma10.astype(float)
F['dif_up'] = D.dif_up.astype(float)
F['rsv'] = D.rsv / 100
F['rsv_low'] = (D.rsv <= 15).astype(float)
F['dd60'] = D.dd60 / 100
F['atr'] = D.atr / 10
F['volx'] = D.volx.clip(0, 5) / 5
F['downstk'] = D.downstk.clip(0, 8) / 8
F['zt20'] = D.zt20.astype(float)
F['days_low'] = D.days_low.clip(0, 30) / 30
F['ret5'] = D.ret5.clip(-30, 30) / 30
F['gap_reclaim'] = D.gap_reclaim.astype(float)
F['ma20slope'] = D.ma20slope.clip(-15, 15) / 15
F = F.fillna(0)

days = sorted(D.d.unique())
cut = days[250]
tr = D.d < cut; te = D.d >= cut
print(f'训练 {tr.sum()} 行(≤{cut}), 测试 {te.sum()} 行({len(days)-251}天)')

X = F.values; y = D.win.values
Xtr = np.c_[np.ones(tr.sum()), X[tr.values]]
beta = np.linalg.lstsq(Xtr, y[tr.values], rcond=None)[0]   # LPM: 直接拟合P(win)
D['lpm'] = np.c_[np.ones(len(D)), X] @ beta
print('LPM权重(标准化特征):')
for name, b in sorted(zip(['const'] + list(F.columns), beta), key=lambda x: -abs(x[1])):
    print(f'  {name:<12} {b:+.3f}')

TE = D[te]
def ev(sub, lab):
    n = len(sub)
    if n < 80: print(f'{lab:<40} n={n} 过小'); return
    w = sub.win.mean() * 100; s = sub.stopped.mean() * 100
    print(f'{lab:<40} n={n:<5} 覆盖{sub.d.nunique():>3}天 胜{w:5.1f}%  先砸-8%={s:4.1f}%  EV≈{w/100*5-s/100*8:+.2f}%')

print(f'\n== 测试窗(样本外, {TE.d.nunique()}天, 含2026年6-7月退潮段) ==')
ev(TE, '底部池全体(测试窗基线)')
n18 = (TE.hand >= 18).sum()
ev(TE[TE.hand >= 18], '手工分≥18 (OOS真实成绩)')
ev(TE[TE.hand >= 21], '手工分≥21')
th_l = TE.lpm.quantile(1 - n18 / len(TE))
ev(TE[TE.lpm >= th_l], f'LPM学权重·同样本量(top{n18})')
th_l2 = TE.lpm.quantile(0.90)
ev(TE[TE.lpm >= th_l2], 'LPM·top10%')
ev(TE[(TE.hand >= 18) & (TE.atr <= 4)], '手工分≥18+ATR≤4 (OOS)')
ev(TE[(TE.lpm >= th_l) & (TE.atr <= 4)], 'LPM同量+ATR≤4')
# 训练窗对照(过拟合度)
TR = D[tr]
ev(TR[TR.hand >= 18], '[对照]手工分≥18·训练窗(在样)')
