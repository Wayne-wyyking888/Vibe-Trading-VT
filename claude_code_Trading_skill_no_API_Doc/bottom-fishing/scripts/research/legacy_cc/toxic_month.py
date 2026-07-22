# -*- coding: utf-8 -*-
"""毒月专项: 2022-10~2026-07 扩展面板(900根) → 推荐线分年OOS + 毒月识别 + 集中性归因 + 崩盘快刀适用性"""
import sys, json, time, urllib.request, pathlib
import pandas as pd, numpy as np
scr = pathlib.Path(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad')
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import ashare_weekly_rank as WK

def tx(sym, n=900):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq'
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})
            j = json.loads(urllib.request.urlopen(req, timeout=15).read())
            rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day') or []
            return pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                                 for r in rows], columns=['d', 'o', 'c', 'h', 'l', 'v'])
        except Exception:
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f'tx fail {sym}')

PKL = scr / 'panel900.pkl'
if PKL.exists():
    panel = pd.read_pickle(PKL); print('panel900 cache hit:', len(panel))
else:
    spot = WK.get_spot(600)
    metas = []
    for _, r in spot.iterrows():
        code, name = str(r.get('代码', '')), str(r.get('名称', ''))
        if len(code) != 6 or code.startswith(('68', '8', '4')) or 'ST' in name.upper() or '退' in name:
            continue
        metas.append((code, name, str(r.get('行业', '') or '')))
    print('universe', len(metas), flush=True)
    rows = []
    for k, (code, name, ind) in enumerate(metas):
        sym = ('sh' if code[0] in '69' else 'sz') + code
        try:
            df = tx(sym)
        except Exception:
            time.sleep(0.5); continue
        if len(df) < 150: continue
        df['code'], df['ind'] = code, ind
        rows.append(df)
        if (k + 1) % 80 == 0: print(f'  {k+1}/{len(metas)}', flush=True)
        time.sleep(0.04)
    panel = pd.concat(rows, ignore_index=True)
    panel.to_pickle(PKL)
    print('saved', len(panel))

ix = tx('sz399006')
ix['ma20'] = ix.c.rolling(20).mean(); ix['i5'] = ix.c.pct_change(5)
ix['defensive'] = (ix.c < ix.ma20) | (ix.i5 < -0.02)
cnt, dd_ = 0, []
for v in ix.defensive:
    cnt = cnt + 1 if v else 0
    dd_.append(cnt)
ix['def_days'] = dd_
lo14 = ix.l.rolling(14).min(); hi14 = ix.h.rolling(14).max()
ix['idx_rsv'] = (ix.c - lo14) / (hi14 - lo14 + 1e-9) * 100
ix['ifwd10'] = ix.c.shift(-10) / ix.c - 1   # 大盘随后10日(β归因用)

p = panel.sort_values(['code', 'd']).reset_index(drop=True)
g = p.groupby('code', group_keys=False)
p['hi60'] = g.h.transform(lambda s: s.rolling(60).max()); p['lo60'] = g.l.transform(lambda s: s.rolling(60).min())
p['dd60'] = (p.c / p.hi60 - 1) * 100
p['pos60'] = (p.c - p.lo60) / (p.hi60 - p.lo60 + 1e-9) * 100
p['ret'] = g.c.transform(lambda s: s.pct_change()); p['ret5'] = g.c.transform(lambda s: s.pct_change(5)) * 100
p['vma20'] = g.v.transform(lambda s: s.rolling(20).mean()); p['volx'] = p.v / p.vma20
p['ma5'] = g.c.transform(lambda s: s.rolling(5).mean()); p['ma10'] = g.c.transform(lambda s: s.rolling(10).mean())
tr = pd.concat([p.h - p.l, (p.h - g.c.shift(1)).abs(), (p.l - g.c.shift(1)).abs()], axis=1).max(axis=1)
p['atr'] = tr.groupby(p.code).transform(lambda s: s.rolling(14).mean()) / p.c * 100
p['ema12'] = g.c.transform(lambda s: s.ewm(span=12, adjust=False).mean())
p['ema26'] = g.c.transform(lambda s: s.ewm(span=26, adjust=False).mean())
p['dif'] = p.ema12 - p.ema26; p['dif_up'] = g.dif.transform(lambda s: s.diff(3)) > 0
plo14 = g.l.transform(lambda s: s.rolling(14).min()); phi14 = g.h.transform(lambda s: s.rolling(14).max())
p['rsv'] = (p.c - plo14) / (phi14 - plo14 + 1e-9) * 100
p['is_low'] = p.l <= p.lo60 * 1.001
def dsl(s):
    out, cnt = [], 999
    for v in s:
        cnt = 0 if v else cnt + 1
        out.append(cnt)
    return pd.Series(out, index=s.index)
p['days_low'] = g.is_low.transform(dsl)
p['downstk'] = g.ret.transform(lambda s: (s < 0).astype(int).groupby((s >= 0).cumsum()).cumsum())
p['zt20'] = g.ret.transform(lambda s: (s >= 0.093).rolling(20).sum().fillna(0)) > 0
p['gap_reclaim'] = (p.o < g.c.shift(1) * 0.98) & (p.c > p.o)
p = p.merge(ix[['d', 'defensive', 'def_days', 'idx_rsv', 'ifwd10']], on='d', how='inner')

p['score'] = (8.6 * p.defensive.astype(float) + 5.2 * (p.c > p.ma10).astype(float)
              + 4.5 * p.dif_up.astype(float) + 3.9 * ((p.rsv > 20) & (p.rsv <= 40)).astype(float)
              + 3.7 * ((p.dd60 <= -30) & (p.dd60 > -45)).astype(float)
              + 3.7 * (p.c > p.ma5).astype(float) + 4.4 * p.gap_reclaim.astype(float)
              - 7.4 * (p.rsv <= 15).astype(float) - 6.3 * (p.downstk >= 4).astype(float)
              - 5.4 * p.zt20.astype(float) - 3.5 * (p.atr >= 7).astype(float)
              - 3.1 * (p.days_low <= 1).astype(float))
p['sscore'] = p.score - 8.6 * p.defensive.astype(float)
zone = (p.dd60 <= -20) & (p.pos60 <= 25)
line = zone & (p.atr <= 4) & ((p.defensive & (p.score >= 18)) | (~p.defensive & (p.sscore >= 15)))
L = p[line & p.dd60.notna() & p.volx.notna()].copy()
print(f'\n推荐线过线: {len(L)} 笔 / {L.d.nunique()} 天 / 全窗{p.d.nunique()}天')

arr = {c_: gg[['o', 'c', 'h', 'l']].values for c_, gg in p.groupby('code')}
pos_map = p.groupby('code').cumcount().values
def race(ri, tp=5.0, sp=-8.0, hz=20):
    a = arr[p.code.iloc[ri]]; i = pos_map[ri]
    if i + 2 >= len(a): return None, None, None
    e = a[i + 1][0]
    crash1 = a[i + 1][1] <= e * 0.95            # 买入日收≤-5%
    st, tg = e * (1 + sp / 100), e * (1 + tp / 100)
    if a[i + 1][1] <= st: return 0, 1, crash1
    for j in range(i + 2, min(i + 1 + hz, len(a) - 1) + 1):
        if a[j][3] <= st: return 0, j - (i + 1), crash1
        if a[j][2] >= tg: return 1, j - (i + 1), crash1
    return -1, hz, crash1
def next_open_ret(ri):
    a = arr[p.code.iloc[ri]]; i = pos_map[ri]
    if i + 2 >= len(a): return None
    return a[i + 2][0] / a[i + 1][0] - 1

res = [race(ri) for ri in L.index]
L['out'] = [r[0] for r in res]; L['days'] = [r[1] for r in res]; L['crash1'] = [r[2] for r in res]
L = L[L.out.notna()]
L['win'] = (L.out == 1).astype(float); L['stopped'] = (L.out == 0).astype(float)
L['y'] = L.d.str[:4]; L['ym'] = L.d.str[:7]

print('\n== A. 分年成绩(2023/2024=真·样本外, 打分权重来自2025-26面板) ==')
for y, gg in L.groupby('y'):
    if len(gg) < 30: continue
    print(f'{y}: n={len(gg):<5} 胜{gg.win.mean()*100:5.1f}%  先砸-8%={gg.stopped.mean()*100:4.1f}%  EV≈{gg.win.mean()*5-gg.stopped.mean()*8:+.2f}%')

print('\n== B. 毒月清单(先砸率≥30%且n≥15) ==')
tox_list = []
for ym, gg in L.groupby('ym'):
    if len(gg) >= 15 and gg.stopped.mean() >= 0.30:
        tox_list.append(ym)
        print(f'{ym}: n={len(gg):<4} 胜{gg.win.mean()*100:4.0f}% 雷{gg.stopped.mean()*100:4.0f}%  '
              f'大盘随后10日均值{gg.ifwd10.mean()*100:+.1f}%')
tox = L[L.ym.isin(tox_list)]; norm = L[~L.ym.isin(tox_list)]
print(f'毒月合计 {len(tox)} 笔 vs 正常月 {len(norm)} 笔(雷{norm.stopped.mean()*100:.0f}%)')

print('\n== C. 毒月集中性 ==')
ts = tox[tox.stopped == 1]
if len(ts) > 20:
    # 时间集中: 毒月内被雷笔的进场日 落在最密5交易日窗口的比例
    frac = []
    for ym, gg in ts.groupby('ym'):
        days_ = sorted(gg.d.unique()); best = 0
        alldays = sorted(L[L.ym == ym].d.unique())
        for i0 in range(len(alldays)):
            w = set(alldays[i0:i0 + 5])
            best = max(best, gg.d.isin(w).sum())
        frac.append(best / len(gg))
    print(f'时间: 单月被雷笔中落在最密5交易日窗内的比例 中位{np.median(frac)*100:.0f}%')
    print(f'行业: 毒月被雷Top行业占比 {ts.ind.value_counts(normalize=True).head(3).round(2).to_dict()}')
    print(f'个股重复: 被雷{len(ts)}笔来自{ts.code.nunique()}只 (重复率{1-ts.code.nunique()/len(ts):.0%})')
    print('\n特征对比(毒月被雷 vs 正常月被雷 vs 毒月赢):')
    for col, lab in [('def_days', '防守已持续天'), ('idx_rsv', '大盘RSV'), ('score', '总分'), ('atr', 'ATR'),
                     ('dd60', '回撤%'), ('ifwd10', '大盘随后10日%')]:
        a1 = ts[col].astype(float); a2 = norm[norm.stopped == 1][col].astype(float); a3 = tox[tox.win == 1][col].astype(float)
        f = 100 if col == 'ifwd10' else 1
        print(f'  {lab:<10} 毒月雷:{a1.median()*f:7.1f}  正常月雷:{a2.median()*f:7.1f}  毒月赢:{a3.median()*f:7.1f}')

print('\n== D. 崩盘快刀在抄底里的适用性 ==')
CR = L[L.crash1 == True]  # noqa: E712
print(f'买入日收≤-5%的过线票: {len(CR)}/{len(L)} = {len(CR)/len(L)*100:.1f}%')
if len(CR) >= 15:
    hold_ev = CR.win.mean() * 5 - CR.stopped.mean() * 8
    nrets = [next_open_ret(ri) for ri in CR.index]
    nrets = [x for x in nrets if x is not None]
    print(f'继续持有(赛跑): 胜{CR.win.mean()*100:.0f}% 雷{CR.stopped.mean()*100:.0f}% EV≈{hold_ev:+.2f}%')
    print(f'次日开盘无条件出: 平均收益{np.mean(nrets)*100:+.2f}% (确定小亏)')
