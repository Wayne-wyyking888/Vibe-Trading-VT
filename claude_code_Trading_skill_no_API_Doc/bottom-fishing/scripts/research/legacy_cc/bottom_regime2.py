# -*- coding: utf-8 -*-
"""①崩了多久(def_days)/大盘RSV → 分离度测试 → 加进规则 → OOS验证
②推荐线的 胜率拆档(+5/10/15/20/30) 与 爆雷拆档(-5/-8/-10/-15/-20) 触及曲线"""
import pandas as pd, numpy as np, pickle, json, urllib.request
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'

# ---- 大盘因子 ----
j = json.loads(urllib.request.urlopen(
    'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz399006,day,,,420,qfq', timeout=12).read())
rows = j['data']['sz399006'].get('qfqday') or j['data']['sz399006']['day']
ix = pd.DataFrame([(r[0], float(r[2]), float(r[3]), float(r[4])) for r in rows], columns=['d', 'c', 'h', 'l'])
ix['ma20'] = ix.c.rolling(20).mean(); ix['i5'] = ix.c.pct_change(5)
ix['defensive'] = (ix.c < ix.ma20) | (ix.i5 < -0.02)
cnt, out = 0, []
for v in ix.defensive:
    cnt = cnt + 1 if v else 0
    out.append(cnt)
ix['def_days'] = out                                   # 防守已持续天数(0=非防守)
lo14 = ix.l.rolling(14).min(); hi14 = ix.h.rolling(14).max()
ix['idx_rsv'] = (ix.c - lo14) / (hi14 - lo14 + 1e-9) * 100
ix['idx_rsv_up'] = ix.idx_rsv.diff(3) > 0              # 大盘RSV 3日回升

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
D = D.merge(ix[['d', 'def_days', 'idx_rsv', 'idx_rsv_up']], on='d', how='left', suffixes=('', '_ix'))
D = D[D.def_days.notna()]

def cell(sub, lab):
    n = len(sub)
    if n < 80: print(f'{lab:<36} n={n} 过小'); return
    print(f'{lab:<36} n={n:<5} 胜{sub.win.mean()*100:5.1f}%  先砸-8%={sub.stopped.mean()*100:4.1f}%')

print('== ① 崩了多久(def_days) 分离度 — 底部池全体 ==')
for lo, hi, lab in [(0, 0, '非防守日(0天)'), (1, 3, '刚转防守1~3天(崩盘初期)'), (4, 8, '防守4~8天'), (9, 99, '防守≥9天(崩了很久)')]:
    cell(D[(D.def_days >= lo) & (D.def_days <= hi)], lab)
print('\n== 大盘RSV位置 分离度 ==')
for lo, hi, lab in [(0, 15, '大盘RSV≤15(崩最深处)'), (15, 40, '15~40'), (40, 70, '40~70'), (70, 100, '>70(大盘高位)')]:
    cell(D[(D.idx_rsv > lo) & (D.idx_rsv <= hi)], lab)
print('\n== 大盘RSV是否回升(3日) ==')
cell(D[D.idx_rsv_up], '大盘RSV回升中')
cell(D[~D.idx_rsv_up], '大盘RSV仍下行')
print('\n== 交叉: 防守≥4天 × 大盘RSV回升 ==')
cell(D[(D.def_days >= 4) & D.idx_rsv_up], '崩够久+大盘回升(假设的黄金组)')
cell(D[(D.def_days >= 1) & (D.def_days <= 3) & ~D.idx_rsv_up], '刚崩+仍下行(假设的毒组)')

# ---- ② 新规则 + OOS ----
cut = sorted(D.d.unique())[min(250, len(set(D.d)) - 90)]
L_old = (D.hand >= 18) & (D.atr <= 4)
mkt_ok = (D.def_days >= 4) | ((D.def_days == 0) & D.idx_rsv_up)   # 排除刚崩1~3天; 非防守需大盘回升
L_new = L_old & mkt_ok
print(f'\n== ② 新市况条件(排除刚转防守1~3天; 非防守日要求大盘RSV回升) 加进推荐线 ==')
for lab, m in [('旧推荐线·全期', L_old), ('新推荐线·全期', L_new),
               ('旧推荐线·样本外', L_old & (D.d >= cut)), ('新推荐线·样本外', L_new & (D.d >= cut))]:
    sub = D[m]
    ev = sub.win.mean() * 5 - sub.stopped.mean() * 8
    print(f'{lab:<18} n={len(sub):<5} 覆盖{sub.d.nunique():>3}天 胜{sub.win.mean()*100:5.1f}%  先砸-8%={sub.stopped.mean()*100:4.1f}%  EV≈{ev:+.2f}%')
worst = D[L_old & (D.d.str[:7].isin(['2026-02', '2026-06']))]
worst_new = D[L_new & (D.d.str[:7].isin(['2026-02', '2026-06']))]
print(f'毒月(2026-02/06)对比: 旧线 n={len(worst)} 爆雷{worst.stopped.mean()*100:.0f}% → 新线 n={len(worst_new)} 爆雷{(worst_new.stopped.mean()*100 if len(worst_new) else float("nan")):.0f}%')

# ---- ③ 拆档触及曲线(新推荐线) ----
p = pd.read_pickle(scr + r'\panel480.pkl').sort_values(['code', 'd']).reset_index(drop=True)
g = p.groupby('code', group_keys=False)
p['o1'] = g.o.shift(-1)
p['maxhi'] = g.h.transform(lambda s: s.shift(-2).rolling(20, min_periods=1).max().shift(-19)) / p.o1 - 1
p['minlo'] = g.l.transform(lambda s: s.shift(-2).rolling(20, min_periods=1).min().shift(-19)) / p.o1 - 1
SEL = D[L_new].copy()
SEL['maxhi'] = p.maxhi.reindex(SEL.index); SEL['minlo'] = p.minlo.reindex(SEL.index)
SEL = SEL.dropna(subset=['maxhi', 'minlo'])
print(f'\n== ③ 新推荐线 拆档触及率(T+1开盘进场, 次日起20日窗, n={len(SEL)}) ==')
print('上行(最高价曾触及): ' + '  '.join(f'≥+{t}%: {(SEL.maxhi>=t/100).mean()*100:.1f}%' for t in [5, 10, 15, 20, 30]))
print('下行(最低价曾触及): ' + '  '.join(f'≤-{t}%: {(SEL.minlo<=-t/100).mean()*100:.1f}%' for t in [5, 8, 10, 15, 20]))
print('(下行即分档爆雷率——-8%档=策略实际止损率; 上行为可止盈机会率, 两者同一笔可都发生)')
