# -*- coding: utf-8 -*-
"""资金面增量测试: 东财逐日主力资金流历史 × 底部池 (子样本~180只, 限流即止)"""
import pandas as pd, numpy as np, pickle, json, time, urllib.request
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'
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

top_codes = D.code.value_counts().head(180).index.tolist()  # 底部日最多的180只(样本效率最高)
UA = {'User-Agent': 'Mozilla/5.0'}
def fflow_hist(code):
    secid = ('1.' if code[0] in '69' else '0.') + code
    url = ('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101'
           f'&secid={secid}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53')
    j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read())
    kl = (j.get('data') or {}).get('klines') or []
    out = []
    for s in kl:
        f = s.split(',')
        try: out.append((f[0], float(f[1]), float(f[2])))   # date, 主力净额(元), 主力净占比%
        except ValueError: continue
    return out

flows, fail = {}, 0
for k, code in enumerate(top_codes):
    try:
        rows = fflow_hist(code)
        if rows: flows[code] = pd.DataFrame(rows, columns=['d', 'net', 'ratio'])
        else: fail += 1
    except Exception:
        fail += 1
        if fail > 25: print('限流过多,中止于', k); break
    time.sleep(0.25)
    if (k + 1) % 60 == 0: print(f'  {k+1}/{len(top_codes)} ok={len(flows)}', flush=True)
print(f'资金流历史: {len(flows)} 只成功, {fail} 失败')

if len(flows) >= 60:
    fr = []
    for code, df in flows.items():
        df = df.sort_values('d')
        df['r5'] = df.ratio.rolling(5).mean()          # 5日主力净占比均值
        df['pos5'] = (df.net > 0).rolling(5).sum()
        df['code'] = code
        fr.append(df[['code', 'd', 'r5', 'pos5']])
    FR = pd.concat(fr)
    M = D.merge(FR, on=['code', 'd'], how='inner')
    print(f'合并到底部池: {len(M)} 行 (原{len(D)})')
    def ev(sub, lab):
        n = len(sub)
        if n < 100: print(f'{lab:<42} n={n} 过小'); return
        w = sub.win.mean()*100; s = sub.stopped.mean()*100
        print(f'{lab:<42} n={n:<5} 胜{w:5.1f}%  先砸-8%={s:4.1f}%  EV≈{w/100*5-s/100*8:+.2f}%')
    print('\n== 底部池全体 × 5日主力流向 ==')
    ev(M, '基线(有资金数据的底部行)')
    ev(M[M.r5 >= 2], '主力5日净占比≥+2%(强流入)')
    ev(M[(M.r5 > 0) & (M.r5 < 2)], '温和流入0~2%')
    ev(M[(M.r5 <= 0) & (M.r5 > -2)], '温和流出0~-2%')
    ev(M[M.r5 <= -2], '强流出≤-2%')
    ev(M[M.pos5 >= 4], '5日中≥4天净流入')
    print('\n== 叠加到手工分≥18 上(增量检验) ==')
    ev(M[M.hand >= 18], '手工分≥18(该子样本)')
    ev(M[(M.hand >= 18) & (M.r5 > 0)], '手工分≥18 + 主力5日流入')
    ev(M[(M.hand >= 18) & (M.r5 <= -2)], '手工分≥18 + 强流出(应剔?)')
    ev(M[(M.hand >= 18) & (M.atr <= 4) & (M.r5 > 0)], '≥18+ATR≤4+流入(全叠)')
    M.to_pickle(scr + r'\bottomM_flow.pkl')
else:
    print('样本不足,资金面测试失败(限流)')
