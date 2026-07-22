# -*- coding: utf-8 -*-
"""抄底可行性: T日底部信号 → T+1开盘进场 → 先到+X%还是先砸-8%(赛跑, 20日窗)
口径: A股T+1不可当日卖 → 赛跑从进场次日起算; 进场日收盘≤-8%则次日开盘算止损(跳空穿越按开盘)"""
import pandas as pd, numpy as np
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
p = pd.read_pickle(scr + r'\panel480.pkl').sort_values(['code', 'd']).reset_index(drop=True)
g = p.groupby('code', group_keys=False)
p['hi60'] = g.h.transform(lambda s: s.rolling(60).max())
p['lo60'] = g.l.transform(lambda s: s.rolling(60).min())
p['dd60'] = (p.c / p.hi60 - 1) * 100
p['pos60'] = (p.c - p.lo60) / (p.hi60 - p.lo60 + 1e-9) * 100
p['ret20'] = g.c.transform(lambda s: s.pct_change(20)) * 100
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean())
p['volx'] = p.v / p.vma20
p['prevc'] = g.c.shift(1); p['prevh'] = g.h.shift(1)
p['yang'] = p.c >= p.o
p['lowsh'] = (p[['o', 'c']].min(axis=1) - p.l) / p.c * 100
p['body'] = (p.c - p.o).abs() / p.c * 100
p['falling'] = p.c < p.prevc

arr = {}
for code, gg in p.groupby('code'):
    arr[code] = gg[['o', 'c', 'h', 'l']].values
pos_in_code = p.groupby('code').cumcount().values
codes_arr = p.code.values

def race(row_idx, target_pct, stop_pct=-8.0, horizon=20):
    """returns (outcome, days): outcome 1=win 0=stop -1=timeout; days从进场日数起"""
    code = codes_arr[row_idx]; i = pos_in_code[row_idx]
    a = arr[code]
    if i + 2 >= len(a): return None
    entry = a[i + 1][0]                      # T+1开盘进场
    stop = entry * (1 + stop_pct / 100); tgt = entry * (1 + target_pct / 100)
    # 进场日(不可卖): 收盘已≤止损 → 次日开盘出=止损
    if a[i + 1][1] <= stop:
        return (0, 1)
    end = min(i + 1 + horizon, len(a) - 1)
    for j in range(i + 2, end + 1):
        o_, c_, h_, l_ = a[j]
        d = j - (i + 1)
        hit_s = l_ <= stop; hit_t = h_ >= tgt
        if hit_s and hit_t: return (0, d)    # 同日双触保守按止损
        if hit_s: return (0, d)
        if hit_t: return (1, d)
    return (-1, end - (i + 1))

def stats(idxs, target, label, horizon=20):
    res = [race(i, target, horizon=horizon) for i in idxs]
    res = [r for r in res if r]
    if len(res) < 80:
        print(f'{label:<44} n={len(res)} 样本过小'); return
    n = len(res)
    win = [r for r in res if r[0] == 1]; stop = [r for r in res if r[0] == 0]
    dw = np.median([r[1] for r in win]) if win else float('nan')
    w3 = sum(1 for r in win if r[1] <= 3) / n * 100
    print(f'{label:<44} n={n:<6} 先到+{target}%={len(win)/n*100:4.1f}%(中位{dw:.0f}天,3天内{w3:.1f}%)  先砸-8%={len(stop)/n*100:4.1f}%  20日无果={100-len(win)/n*100-len(stop)/n*100:4.1f}%')

v = p.reset_index()
DEEP = v[(v.dd60 <= -20) & (v.pos60 <= 25) & v.dd60.notna() & v.volx.notna()]
rng = np.random.RandomState(7)
BASE = v[v.dd60.notna() & v.volx.notna()].sample(20000, random_state=7)
hi_th = v.groupby('d').ret20.transform(lambda s: s.quantile(0.70))
HIMOM = v[(v.ret20 >= hi_th) & v.ret20.notna()].sample(15000, random_state=7)

print(f'面板 {len(v)} 股票日 | 深回撤底部区(dd60≤-20%且60位≤25) = {len(DEEP)} ({len(DEEP)/len(v)*100:.1f}%)\n')
print('== 目标+5% 的赛跑(先到+5%还是先砸-8%, 20日窗) ==')
stats(BASE.index, 5, '基线·随机股票日')
stats(HIMOM.index, 5, '对照·高动量票(现skill画像)')
stats(DEEP.index, 5, '底部区·无信号(单纯超跌)')
D = DEEP
stats(D[D.falling].index, 5, '底部区·T日仍在跌(用户问的"还在跌就买")')
stats(D[D.yang & (D.volx <= 0.75)].index, 5, '底部区·缩量阳企稳')
stats(D[(D.c > D.prevh) & (D.volx >= 1.3)].index, 5, '底部区·放量反包')
stats(D[(D.lowsh >= 1.5) & (D.lowsh >= 1.5 * D.body)].index, 5, '底部区·长下影')
stats(D[D.defensive].index, 5, '底部区·防守日(大盘也在跌)')
stats(D[~D.defensive].index, 5, '底部区·非防守日(大盘已企稳)')
stats(D[(D.c > D.prevh) & (D.volx >= 1.3) & (~D.defensive)].index, 5, '底部区·放量反包×大盘企稳')

print('\n== 不同目标幅度: 底部区·放量反包×大盘企稳 (最优组) vs 单纯超跌 ==')
BEST = D[(D.c > D.prevh) & (D.volx >= 1.3) & (~D.defensive)]
for t in [5, 10, 20, 30]:
    stats(BEST.index, t, f'最优组 目标+{t}%')
for t in [10, 20]:
    stats(D.index, t, f'单纯超跌 目标+{t}%')
