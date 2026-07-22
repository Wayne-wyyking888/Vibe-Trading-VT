# -*- coding: utf-8 -*-
"""研究1: U2'高位限定收紧(85/30 → 75/20)面板回测
研究2: 防守市况下 防守因子(下行β/ATR/涨停基因/红日抗跌)与动量的IC对比 + 高动量桶内防守再排序"""
import sys, json, time, urllib.request
import numpy as np, pandas as pd

sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import ashare_weekly_rank as eng

spot = eng.get_spot(600)
codes = []
for _, r in spot.iterrows():
    code, name = str(r.get('代码', '')), str(r.get('名称', ''))
    if not code or len(code) != 6: continue
    if code.startswith(('68', '8', '4')): continue
    if 'ST' in name.upper() or '退' in name: continue
    codes.append(code)
codes = codes[:220]
print(f'universe: {len(codes)} 只', flush=True)

def tx_kline(sym, n=250):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq'
    j = json.loads(urllib.request.urlopen(url, timeout=12).read())
    rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day') or []
    return pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                         for r in rows], columns=['d', 'o', 'c', 'h', 'l', 'v'])

idx = tx_kline('sz399006')
idx['iret'] = idx.c.pct_change()
idx['ma20'] = idx.c.rolling(20).mean()
idx['i5'] = idx.c.pct_change(5)
idx['defensive'] = (idx.c < idx.ma20) | (idx.i5 < -0.02)
idxm = idx.set_index('d')

rows = []
for k, code in enumerate(codes):
    sym = ('sh' if code[0] in '69' else 'sz') + code
    try:
        df = tx_kline(sym)
    except Exception:
        continue
    if len(df) < 90: continue
    df = df.merge(idx[['d', 'iret', 'defensive']], on='d', how='inner')
    df['ret'] = df.c.pct_change()
    df['upsh'] = (df.h - df[['o', 'c']].max(axis=1)) / df.c * 100
    lo60 = df.l.rolling(60).min(); hi60 = df.h.rolling(60).max()
    df['pos60'] = (df.c - lo60) / (hi60 - lo60 + 1e-9) * 100
    df['ret20'] = df.c.pct_change(20) * 100
    tr = pd.concat([df.h - df.l, (df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean() / df.c * 100
    zt_th = 0.195 if code.startswith('30') else 0.095
    df['zt'] = (df.ret >= zt_th).astype(int)
    df['zt5'] = df.zt.rolling(5).sum().clip(0, 1)
    # 60日滚动 下行β / 红日抗跌
    dnb, res = [np.nan]*len(df), [np.nan]*len(df)
    r_s = df.ret.values; r_i = df.iret.values
    for t in range(60, len(df)):
        ws, wi = r_s[t-59:t+1], r_i[t-59:t+1]
        m = ~(np.isnan(ws) | np.isnan(wi))
        ws, wi = ws[m], wi[m]
        dn = wi < 0
        if dn.sum() >= 15:
            x, y = ws[dn], wi[dn]
            dnb[t] = np.cov(x, y)[0, 1] / (np.var(y) + 1e-12)
        red = wi <= -0.01
        if red.sum() >= 5:
            res[t] = np.mean(ws[red] - wi[red]) * 100
    df['dnbeta'] = dnb; df['resil'] = res
    df['n1_oc'] = (df.c.shift(-1) / df.o.shift(-1) - 1) * 100
    df['n1_cc'] = (df.c.shift(-1) / df.c - 1) * 100
    df['n1_low'] = (df.l.shift(-1) / df.c - 1) * 100
    df['n3_cc'] = (df.c.shift(-3) / df.c - 1) * 100
    df['code'] = code
    rows.append(df.iloc[60:-3])
    if (k+1) % 50 == 0: print(f'  {k+1}/{len(codes)}', flush=True)
    time.sleep(0.08)

panel = pd.concat(rows, ignore_index=True).dropna(subset=['upsh', 'pos60', 'ret20', 'n1_cc', 'n3_cc'])
print(f'面板: {len(panel)} 股票日, {panel.d.nunique()} 个交易日, 防守日占比 {panel.defensive.mean()*100:.0f}%\n', flush=True)

def stats(g, label):
    if len(g) == 0: print(f'{label:<30} n=0'); return
    print(f'{label:<30} n={len(g):<6} 次日开→收{g.n1_oc.mean():+.2f}%  次日收{g.n1_cc.mean():+.2f}%  '
          f'P(次日≤-5%)={(g.n1_cc<=-5).mean()*100:.1f}%  P(盘中≤-7%)={(g.n1_low<=-7).mean()*100:.1f}%  3日{g.n3_cc.mean():+.2f}%')

print('=== 研究1: U2 高位限定 85/30 vs 75/20 (上影≥3%) ===')
u2cur = panel[(panel.upsh >= 3) & ((panel.pos60 > 85) | (panel.ret20 > 30))]
u2new = panel[(panel.upsh >= 3) & ((panel.pos60 > 75) | (panel.ret20 > 20))]
inc = u2new[~u2new.index.isin(u2cur.index)]
stats(panel, '基准(全部股票日)')
stats(panel[panel.upsh >= 3], '仅上影≥3%(无高位限定)')
stats(u2cur, '现行U2(85/30)')
stats(inc, '增量带(75/20新增部分)')
print('-- 防守日子样本 --')
stats(panel[panel.defensive], '基准(防守日)')
stats(u2cur[u2cur.defensive], '现行U2(防守日)')
stats(inc[inc.defensive], '增量带(防守日)')
zdg = panel[(panel.code == '001287') & (panel.d == '2026-07-10')]
if len(zdg):
    z = zdg.iloc[0]
    print(f'中电港07-10: upsh={z.upsh:.1f} pos60={z.pos60:.0f} ret20={z.ret20:.0f} → 现行U2命中={bool((z.upsh>=3)and(z.pos60>85 or z.ret20>30))} 新带命中={bool((z.upsh>=3)and(z.pos60>75 or z.ret20>20))} 次日收{z.n1_cc:+.1f}%')

print('\n=== 研究2: 防守日 因子IC(Fama-MacBeth 日均Spearman → 3日收益) ===')
def fm_ic(sub, col, fwd='n3_cc'):
    ics = []
    for _, g in sub.groupby('d'):
        g = g.dropna(subset=[col, fwd])
        if len(g) < 30: continue
        ics.append(g[col].rank().corr(g[fwd].rank()))
    if not ics: return None, None, 0
    ics = np.array(ics)
    return ics.mean(), ics.mean()/(ics.std()/np.sqrt(len(ics))+1e-12), len(ics)
for regime, sub in [('防守日', panel[panel.defensive]), ('非防守日', panel[~panel.defensive])]:
    print(f'-- {regime} ({sub.d.nunique()}个截面) --')
    for col in ['ret20', 'dnbeta', 'atr', 'zt5', 'resil', 'upsh']:
        m, t, nn = fm_ic(sub.dropna(subset=[col]), col)
        if m is not None:
            print(f'  {col:<8} IC={m:+.4f}  t={t:+.1f}  截面{nn}')

print('\n=== 研究2b: 高动量桶(ret20前30%)内, 防守惩罚分再排序的效果 ===')
def zs(s): return (s - s.mean()) / (s.std() + 1e-9)
out = {'防守日': [], '非防守日': []}
for d, g in panel.groupby('d'):
    g = g.dropna(subset=['ret20', 'dnbeta', 'atr', 'resil', 'n3_cc'])
    if len(g) < 60: continue
    hi = g[g.ret20 >= g.ret20.quantile(0.70)].copy()
    if len(hi) < 20: continue
    hi['pen'] = zs(hi.dnbeta) + zs(hi.atr) + hi.zt5 * 1.0 - zs(hi.resil)
    safe = hi[hi.pen <= hi.pen.median()]; risky = hi[hi.pen > hi.pen.median()]
    key = '防守日' if g.defensive.iloc[0] else '非防守日'
    out[key].append((safe.n3_cc.mean(), risky.n3_cc.mean()))
for k, v in out.items():
    if v:
        a = np.array(v)
        d = a[:, 0] - a[:, 1]
        t = d.mean() / (d.std() / np.sqrt(len(d)) + 1e-12)
        print(f'{k}({len(v)}个截面): 高动量内 防守组3日{a[:,0].mean():+.2f}% vs 高险组{a[:,1].mean():+.2f}%  价差{d.mean():+.2f}pp t={t:+.1f}')

panel.to_pickle(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad\panel.pkl')
print('\npanel -> scratchpad/panel.pkl')
