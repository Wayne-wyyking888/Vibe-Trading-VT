# -*- coding: utf-8 -*-
"""目标: 爆雷率<3% 在哪里物理上存在? 额外维度: 全宇宙ATR格子/市值/披露季日历/止损宽度/账户级换算"""
import sys, pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel.pkl').sort_values(['code', 'd']).copy()
g = p.groupby('code', group_keys=False)
p['minlow3'] = g.l.transform(lambda s: s.shift(-1).rolling(3, min_periods=1).min().shift(-2)) / p.c - 1
p['f3'] = g.c.shift(-3) / p.c - 1
p['ret5'] = g.c.transform(lambda s: s.pct_change(5)) * 100
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean()); p['volx'] = p.v / p.vma20
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import ashare_weekly_rank as eng
spot = eng.get_spot(600)
capmap = dict(zip(spot['代码'].astype(str), spot['流通市值'].astype(float) / 1e8))
p['cap'] = p.code.map(capmap)
p = p.dropna(subset=['minlow3', 'f3', 'atr'])
hi_th = p.groupby('d').ret20.transform(lambda s: s.quantile(0.70))
p['himom'] = p.ret20 >= hi_th

def bl(x): return (x.minlow3 <= -0.08).mean() * 100

print('== A. 全宇宙(不限动量) ATR × 环境 格子: 爆雷率 / 3日中位收益 / 样本 ==')
for atr_lo, atr_hi in [(0, 2.5), (2.5, 3.5), (3.5, 5), (5, 7), (7, 99)]:
    row = []
    for lab, m in [('非防守', ~p.defensive), ('防守', p.defensive)]:
        s = p[(p.atr > atr_lo) & (p.atr <= atr_hi) & m]
        row.append(f'{lab}: {bl(s):4.1f}% / {s.f3.median()*100:+.2f}% / n={len(s)}')
    print(f'ATR {atr_lo}-{atr_hi}%:  ' + '   '.join(row))

print('\n== B. 上面最优格子 × 流通市值 ==')
low = p[(p.atr <= 3.5) & (~p.defensive)]
for lab, m in [('≥300亿', low.cap >= 300), ('100-300亿', (low.cap >= 100) & (low.cap < 300)), ('<100亿', low.cap < 100)]:
    s = low[m]
    if len(s): print(f'ATR≤3.5&非防守 & {lab:<9} 爆雷率 {bl(s):4.2f}%  3日中位 {s.f3.median()*100:+.2f}%  n={len(s)}')

print('\n== C. 高动量宇宙里最极端的收缩(确认地板) ==')
hi = p[p.himom]
best = hi[(hi.atr <= 4) & (~hi.defensive) & (hi.ret5 <= 10) & (hi.volx <= 2) & (hi.pos60 <= 85)]
print(f'高动量+ATR≤4+非防守+5日≤10+volx≤2+60位≤85: n={len(best)} 爆雷率 {bl(best):.1f}% 3日中位 {best.f3.median()*100:+.2f}%')

print('\n== D. 披露季日历效应(月份切) ==')
p['mon'] = p.d.str[5:7]
for m in sorted(p.mon.unique()):
    s = p[p.mon == m]
    if len(s) > 2000: print(f'{m}月: 全宇宙爆雷率 {bl(s):4.1f}%  (高动量 {bl(s[s.himom]):4.1f}%)  n={len(s)}')

print('\n== E. 止损宽度敏感性(高动量组合拳样本: 非防守+ATR≤6.2) ==')
c0 = hi[(~hi.defensive) & (hi.atr <= 6.2)]
for th in [-0.06, -0.08, -0.10, -0.12]:
    print(f'  P(3日内触{th*100:.0f}%) = {(c0.minlow3<=th).mean()*100:4.1f}%')

print('\n== F. 账户级换算(单票仓位6%, 两票, 单票爆雷率10%) ==')
import math
pb = 0.10
print(f'单笔爆雷对账户伤害: 6%仓 × -10%(触-8%后滑点) ≈ -0.6%账户')
print(f'一期两票至少一雷概率: {(1-(1-pb)**2)*100:.0f}%  两票同雷: {pb*pb*100:.1f}%')
print(f'账户单期最大损失(双雷+滑点): ≈ -1.2%账户;  连续5期全雷(概率{(1-(1-pb)**2)**5*100:.2f}%)也只 -6%账户')
