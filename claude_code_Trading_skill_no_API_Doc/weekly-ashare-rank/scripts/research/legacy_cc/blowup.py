# -*- coding: utf-8 -*-
"""买1-2只的爆雷率研究: N是否防雷 / 哪些可控参数真正降爆雷率"""
import pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel.pkl').sort_values(['code', 'd'])
g = p.groupby('code', group_keys=False)
for k in [3, 5, 10]:
    p[f'minlow{k}'] = g.l.transform(lambda s: s.shift(-1).rolling(k, min_periods=1).min().shift(-(k - 1))) / p.c - 1
    p[f'f{k}'] = g.c.shift(-k) / p.c - 1
hi_th = p.groupby('d').ret20.transform(lambda s: s.quantile(0.70))
p['himom'] = p.ret20 >= hi_th   # 高动量=我们头部候选的画像
hi = p[p.himom].dropna(subset=['minlow10'])

def bl(g_, k): return (g_[f'minlow{k}'] <= -0.08).mean() * 100

print('== Q: 增加持有N能降爆雷率吗? P(窗口内曾砸穿-8%) ==')
for lab, sub in [('全部股票日', p.dropna(subset=['minlow10'])), ('高动量票', hi)]:
    print(f'{lab:<8} 3日 {bl(sub,3):.1f}%  →  5日 {bl(sub,5):.1f}%  →  10日 {bl(sub,10):.1f}%')

print('\n== 真正的防雷参数 (高动量票, 3日窗口爆雷率=P(触-8%)) ==')
print(f'① 环境: 非防守日 {bl(hi[~hi.defensive],3):.1f}%  vs 防守日 {bl(hi[hi.defensive],3):.1f}%')
q = hi.atr.quantile([.25, .5, .75])
print(f'② ATR分位: <{q[.25]:.1f}% {bl(hi[hi.atr<=q[.25]],3):.1f}% | 中位段 {bl(hi[(hi.atr>q[.25])&(hi.atr<=q[.75])],3):.1f}% | >{q[.75]:.1f}%(高波动) {bl(hi[hi.atr>q[.75]],3):.1f}%')
hi = hi.copy()
hi['sig'] = ((hi.upsh >= 3) & ((hi.pos60 > 75) | (hi.ret20 > 20))).astype(int) + hi.zt5.astype(int) + (hi.pos60 > 88).astype(int) + (hi.ret20 > 30).astype(int)
for n in [0, 1, 2]:
    sub = hi[hi.sig == n] if n < 2 else hi[hi.sig >= 2]
    lab = f'{n}项' if n < 2 else '≥2项'
    print(f'③ 弱信号(上影带/涨停基因/60位>88/20日>30%)={lab:<4} n={len(sub):<6} 爆雷率 {bl(sub,3):.1f}%   3日中位 {sub.f3.median()*100:+.2f}%')
print(f'④ 组合拳: 非防守日+0弱信号+ATR≤{q[.5]:.1f}%: ', end='')
best = hi[(~hi.defensive) & (hi.sig == 0) & (hi.atr <= q[.5])]
print(f'n={len(best)} 爆雷率 {bl(best,3):.1f}%  3日中位 {best.f3.median()*100:+.2f}%  3日均值 {best.f3.mean()*100:+.2f}%')
worst = hi[(hi.defensive) & (hi.sig >= 2)]
print(f'   对照(防守日+≥2信号): n={len(worst)} 爆雷率 {bl(worst,3):.1f}%  3日中位 {worst.f3.median()*100:+.2f}%')
