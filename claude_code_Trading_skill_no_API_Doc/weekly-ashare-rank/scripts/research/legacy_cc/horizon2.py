# -*- coding: utf-8 -*-
import pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
panel = pd.read_pickle(scr + r'\panel.pkl').sort_values(['code', 'd'])
g = panel.groupby('code', group_keys=False)
for k in [1, 2, 3, 5, 7, 10]:
    panel[f'f{k}'] = g.c.shift(-k) / panel.c - 1
for k in [3, 5, 10]:
    panel[f'minlow{k}'] = g.l.transform(lambda s: s.shift(-1).rolling(k, min_periods=1).min().shift(-(k - 1))) / panel.c - 1

def fm_ic(sub, col, fwd):
    ics = []
    for _, gg in sub.groupby('d'):
        gg = gg.dropna(subset=[col, fwd])
        if len(gg) >= 30:
            ics.append(gg[col].rank().corr(gg[fwd].rank()))
    return np.mean(ics) if ics else np.nan

print('== 动量(ret20) IC 随持有期 ==')
print('%-8s' % '', ' '.join('%7s' % f'{k}日' for k in [1, 2, 3, 5, 7, 10]))
for lab, sub in [('全部', panel), ('防守日', panel[panel.defensive]), ('非防守日', panel[~panel.defensive])]:
    print('%-8s' % lab, ' '.join('%7.3f' % fm_ic(sub, 'ret20', f'f{k}') for k in [1, 2, 3, 5, 7, 10]))

print('\n== 全部股票日: 第一天崩后 等回来 vs 先触-8%止损 ==')
crash = panel[(panel.n1_cc <= -5) | (panel.n1_low <= -7)].dropna(subset=['f10', 'minlow10'])
print(f'样本 {len(crash)} 笔')
for k in [3, 5, 10]:
    c2 = crash.dropna(subset=[f'f{k}', f'minlow{k}'])
    rec = (c2[f'f{k}'] > 0).mean() * 100
    stop = (c2[f'minlow{k}'] <= -0.08).mean() * 100
    pure = ((c2[f'f{k}'] > 0) & (c2[f'minlow{k}'] > -0.08)).mean() * 100
    print(f'{k}日窗: 收盘涨回 {rec:.0f}% | 期间触-8%止损 {stop:.0f}% | 真·等回来(涨回且未触) {pure:.0f}% | 均值 {c2[f"f{k}"].mean()*100:+.1f}%')
