# -*- coding: utf-8 -*-
"""系统扫描: 哪些filter能把高动量票3日爆雷率(P(触-8%))再压下去, 代价多大"""
import sys, pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel.pkl').sort_values(['code', 'd']).copy()
g = p.groupby('code', group_keys=False)
p['minlow3'] = g.l.transform(lambda s: s.shift(-1).rolling(3, min_periods=1).min().shift(-2)) / p.c - 1
p['f3'] = g.c.shift(-3) / p.c - 1
# 特征工程(全部仅用T日及之前数据)
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean())
p['volx'] = p.v / p.vma20
p['ret5'] = g.c.transform(lambda s: s.pct_change(5)) * 100
p['chg1'] = p.ret * 100
p['prevc'] = g.c.shift(1)
p['amp'] = (p.h - p.l) / p.prevc * 100
p['amp5'] = g.amp.transform(lambda s: s.rolling(5).mean())
p['ma5'] = g.c.transform(lambda s: s.rolling(5).mean())
p['ma20'] = g.c.transform(lambda s: s.rolling(20).mean())
p['dev5'] = (p.c / p.ma5 - 1) * 100
p['dev20'] = (p.c / p.ma20 - 1) * 100
p['ma20slope'] = g.ma20.transform(lambda s: s.pct_change(5)) * 100
p['yang'] = (p.c >= p.o).astype(int)
p['fade'] = (p.h - p.c) / p.c * 100          # T日高点回落
p['gapup'] = (p.o / p.prevc - 1) * 100
p['upstreak'] = g.ret.transform(lambda s: (s > 0).astype(int).groupby((s <= 0).cumsum()).cumsum())
# 换手率(近似: 当前流通股本, 静态快照近似历史)
try:
    sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
    import ashare_weekly_rank as eng
    spot = eng.get_spot(600)
    spot['shares'] = spot['流通市值'].astype(float) / spot['最新价'].astype(float)
    shmap = dict(zip(spot['代码'].astype(str), spot['shares']))
    p['shares'] = p.code.map(shmap)
    p['tov'] = p.v * 100 / p.shares * 100
except Exception as e:
    p['tov'] = np.nan
    print('turnover skip:', e)

hi_th = p.groupby('d').ret20.transform(lambda s: s.quantile(0.70))
p['sig'] = ((p.upsh >= 3) & ((p.pos60 > 75) | (p.ret20 > 20))).astype(int) + p.zt5.astype(int) + (p.pos60 > 88).astype(int) + (p.ret20 > 30).astype(int)
hi = p[(p.ret20 >= hi_th)].dropna(subset=['minlow3', 'f3', 'volx', 'amp5', 'dev5', 'ma20slope']).copy()
base_bl = (hi.minlow3 <= -0.08).mean() * 100
print(f'高动量基线: n={len(hi)} 爆雷率{base_bl:.1f}% 3日中位{hi.f3.median()*100:+.2f}%\n')

filters = [
    ('ATR<=5', hi.atr <= 5), ('ATR<=6', hi.atr <= 6),
    ('当日量比volx<=1.5', hi.volx <= 1.5), ('volx<=2', hi.volx <= 2),
    ('5日涨幅<=10%', hi.ret5 <= 10), ('5日涨幅<=15%', hi.ret5 <= 15),
    ('T日涨幅chg1<=4.5%', hi.chg1 <= 4.5), ('chg1介于-2~4.5%', (hi.chg1 <= 4.5) & (hi.chg1 >= -2)),
    ('T日收阳且回落<3%', (hi.yang == 1) & (hi.fade < 3)),
    ('乖离MA5<=5%', hi.dev5 <= 5), ('乖离MA20<=15%', hi.dev20 <= 15),
    ('近5日均振幅<=6%', hi.amp5 <= 6), ('近5日均振幅<=8%', hi.amp5 <= 8),
    ('股价>=15元', hi.c >= 15), ('股价>=10元', hi.c >= 10),
    ('换手<=10%', hi.tov <= 10), ('换手<=15%', hi.tov <= 15),
    ('MA20斜率>0(自身上升趋势)', hi.ma20slope > 0),
    ('连阳<=3', hi.upstreak <= 3),
    ('60日位<=85', hi.pos60 <= 85),
    ('非跳空高开(gap<2%)', hi.gapup < 2),
    ('下行beta<=1.2', hi.dnbeta <= 1.2),
]
print('%-24s %6s %8s %8s %10s %10s' % ('filter(保留侧)', '保留%', '爆雷率', 'Δ vs基线', '3日中位', '被剔组爆雷率'))
rows = []
for lab, m in filters:
    m = m & hi.minlow3.notna()
    keep = hi[m]; drop = hi[~m]
    if len(keep) < 300: continue
    bl = (keep.minlow3 <= -0.08).mean() * 100
    rows.append((lab, len(keep)/len(hi)*100, bl, bl-base_bl, keep.f3.median()*100, (drop.minlow3 <= -0.08).mean()*100))
for r in sorted(rows, key=lambda x: x[2]):
    print('%-24s %5.0f%% %7.1f%% %+7.1fpp %+9.2f%% %9.1f%%' % r)

print('\n== 叠加到组合拳(非防守+0弱信号+ATR<=6.2) 上 ==')
c0 = hi[(~hi.defensive) & (hi.sig == 0) & (hi.atr <= 6.2)]
print(f'组合拳基线: n={len(c0)} 爆雷率{(c0.minlow3<=-0.08).mean()*100:.1f}% 3日中位{c0.f3.median()*100:+.2f}%')
stacks = [
    ('+ 当日volx<=2', (c0.volx <= 2)),
    ('+ 5日涨幅<=15%', (c0.ret5 <= 15)),
    ('+ 换手<=15%', (c0.tov <= 15)),
    ('+ 乖离MA5<=5%', (c0.dev5 <= 5)),
    ('+ 股价>=10元', (c0.c >= 10)),
    ('+ 近5日均振幅<=8%', (c0.amp5 <= 8)),
    ('+ volx<=2 & 换手<=15 & 5日<=15', (c0.volx <= 2) & (c0.tov <= 15) & (c0.ret5 <= 15)),
    ('+ 上面三项 & 振幅<=8 & >=10元', (c0.volx <= 2) & (c0.tov <= 15) & (c0.ret5 <= 15) & (c0.amp5 <= 8) & (c0.c >= 10)),
]
for lab, m in stacks:
    k = c0[m & c0.minlow3.notna()]
    if len(k) < 150: print(f'{lab:<38} n={len(k)} 样本过小跳过'); continue
    print(f'{lab:<38} n={len(k):<5} 保留{len(k)/len(c0)*100:.0f}% 爆雷率{(k.minlow3<=-0.08).mean()*100:.1f}% 3日中位{k.f3.median()*100:+.2f}% 均值{k.f3.mean()*100:+.2f}%')
