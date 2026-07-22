# -*- coding: utf-8 -*-
"""识底因子动物园: 底部区(dd60<=-20 & pos60<=25)内, 每个候选因子 on/off 的赛跑差异
赛跑: T+1开盘进场, 先到+5%还是先砸-8%, 20日窗"""
import pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel480.pkl').sort_values(['code', 'd']).reset_index(drop=True)
g = p.groupby('code', group_keys=False)
p['hi60'] = g.h.transform(lambda s: s.rolling(60).max())
p['lo60'] = g.l.transform(lambda s: s.rolling(60).min())
p['dd60'] = (p.c / p.hi60 - 1) * 100
p['pos60'] = (p.c - p.lo60) / (p.hi60 - p.lo60 + 1e-9) * 100
p['ret5'] = g.c.transform(lambda s: s.pct_change(5)) * 100
p['ret20'] = g.c.transform(lambda s: s.pct_change(20)) * 100
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean())
p['volx'] = p.v / p.vma20
p['prevc'] = g.c.shift(1); p['prevh'] = g.h.shift(1)
p['ret'] = g.c.transform(lambda s: s.pct_change())
p['ma5'] = g.c.transform(lambda s: s.rolling(5).mean())
p['ma10'] = g.c.transform(lambda s: s.rolling(10).mean())
p['ma20'] = g.c.transform(lambda s: s.rolling(20).mean())
p['ma20slope'] = g.ma20.transform(lambda s: s.pct_change(5)) * 100
tr = pd.concat([p.h - p.l, (p.h - p.prevc).abs(), (p.l - p.prevc).abs()], axis=1).max(axis=1)
p['atr'] = tr.groupby(p.code).transform(lambda s: s.rolling(14).mean()) / p.c * 100
# EMA/DIF/DEA (MACD金叉)
p['ema12'] = g.c.transform(lambda s: s.ewm(span=12, adjust=False).mean())
p['ema26'] = g.c.transform(lambda s: s.ewm(span=26, adjust=False).mean())
p['dif'] = p.ema12 - p.ema26
p['dea'] = g.dif.transform(lambda s: s.ewm(span=9, adjust=False).mean())
p['gold'] = (p.dif > p.dea) & (g.dif.shift(1) <= g.dea.shift(1))
p['dif_up'] = g.dif.transform(lambda s: s.diff(3)) > 0     # DIF 3日向上(动能修复)
# RSV14 (KDJ的K原料, 超卖/回升)
lo14 = g.l.transform(lambda s: s.rolling(14).min()); hi14 = g.h.transform(lambda s: s.rolling(14).max())
p['rsv'] = (p.c - lo14) / (hi14 - lo14 + 1e-9) * 100
# 距60日最低点天数 / 二次探底
p['is_low'] = (p.l <= p.lo60 * 1.001)
def dsl(s):
    out, cnt = [], 999
    for v in s:
        cnt = 0 if v else cnt + 1
        out.append(cnt)
    return pd.Series(out, index=s.index)
p['days_low'] = g.is_low.transform(dsl)
p['wbottom'] = (p.l <= p.lo60 * 1.02) & (p.days_low.groupby(p.code).shift(3) >= 8)  # 回踩前低不破(右脚)
# 连跌天数 / 恐慌承接 / 低开收回
p['downstk'] = g.ret.transform(lambda s: (s < 0).astype(int).groupby((s >= 0).cumsum()).cumsum())
p['climax'] = (p.volx >= 2) & (p.ret <= -0.03)            # 放量恐慌抛售(承接换手)
p['gap_reclaim'] = (p.o < p.prevc * 0.98) & (p.c > p.o)   # 低开≥2%后收回(衰竭缺口)
p['zt20'] = g.ret.transform(lambda s: (s >= 0.093).rolling(20).sum().fillna(0)) > 0  # 20日内有过涨停(妖股基因)
p['above_ma5'] = p.c > p.ma5
p['above_ma10'] = p.c > p.ma10

arr = {};
for code, gg in p.groupby('code'):
    arr[code] = gg[['o', 'c', 'h', 'l']].values
pos_in_code = p.groupby('code').cumcount().values
codes_arr = p.code.values
def race(ri, tp=5.0, sp=-8.0, hz=20):
    code = codes_arr[ri]; i = pos_in_code[ri]; a = arr[code]
    if i + 2 >= len(a): return None
    e = a[i + 1][0]; st = e * (1 + sp / 100); tg = e * (1 + tp / 100)
    if a[i + 1][1] <= st: return 0
    for j in range(i + 2, min(i + 1 + hz, len(a) - 1) + 1):
        hs = a[j][3] <= st; ht = a[j][2] >= tg
        if hs: return 0
        if ht: return 1
    return -1

v = p.reset_index()
D = v[(v.dd60 <= -20) & (v.pos60 <= 25) & v.dd60.notna() & v.volx.notna() & v.atr.notna()].copy()
res_cache = {}
def wr(idxs):
    outs = [res_cache.setdefault(i, race(i)) for i in idxs]
    outs = [o for o in outs if o is not None]
    n = len(outs)
    if n < 150: return None
    return n, sum(1 for o in outs if o == 1) / n * 100, sum(1 for o in outs if o == 0) / n * 100

base = wr(D.index)
print(f'底部区基线: n={base[0]} 胜{base[1]:.1f}% 止损{base[2]:.1f}%\n')
factors = [
    ('市况·防守日(大盘MA20下/5日<-2%)', D.defensive),
    ('深度·回撤30~45%(vs其他)', (D.dd60 <= -30) & (D.dd60 > -45)),
    ('时间·离60日低点>5天(盘整过)', D.days_low > 5),
    ('时间·刚创新低(≤1天)', D.days_low <= 1),
    ('结构·二次探底不破(W右脚)', D.wbottom),
    ('承接·恐慌放量(volx≥2且跌≥3%)', D.climax),
    ('衰竭·低开2%+收回', D.gap_reclaim),
    ('急跌·5日≤-10%(vs阴跌)', D.ret5 <= -10),
    ('连跌·≥4连阴', D.downstk >= 4),
    ('修复·站回MA5', D.above_ma5),
    ('修复·站回MA10', D.above_ma10),
    ('修复·MACD金叉当日', D.gold),
    ('修复·DIF3日向上', D.dif_up),
    ('超卖·RSV14≤15(深超卖)', D.rsv <= 15),
    ('超卖回升·RSV从<20回到20-40', (D.rsv > 20) & (D.rsv <= 40)),
    ('波动·ATR≤4(低波阴跌型)', D.atr <= 4),
    ('波动·ATR≥7(高波V底型)', D.atr >= 7),
    ('基因·20日内有过涨停', D.zt20),
]
rows = []
for lab, m in factors:
    on = wr(D[m].index); off = wr(D[~m].index)
    if on and off:
        rows.append((lab, on[0], on[1], on[2], on[1] - off[1], on[2] - off[2]))
print('%-30s %7s %7s %8s %9s %9s' % ('因子(on侧)', 'n', '胜率', '止损率', 'Δ胜率', 'Δ止损'))
for r in sorted(rows, key=lambda x: -x[4]):
    print('%-30s %7d %6.1f%% %7.1f%% %+8.1fpp %+8.1fpp' % r)
D.to_pickle(scr + r'\bottomD.pkl')
import pickle
pickle.dump(res_cache, open(scr + r'\race_cache.pkl', 'wb'))
print('\nD/race cache saved')
