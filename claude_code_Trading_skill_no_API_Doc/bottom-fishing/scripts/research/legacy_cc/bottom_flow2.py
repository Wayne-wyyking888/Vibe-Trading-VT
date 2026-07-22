# -*- coding: utf-8 -*-
"""资金面增量测试(新浪源): 逐日主力资金流历史 × 底部池, 底部日最多的150只"""
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

top_codes = D.code.value_counts().head(150).index.tolist()
def sina_flow(code, num=400):
    daima = ('sh' if code.startswith(('60', '68', '9', '5')) else 'sz') + code
    url = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={num}&sort=opendate&asc=0&daima={daima}')
    req = urllib.request.Request(url, headers={'Referer': 'https://vip.stock.finance.sina.com.cn/',
                                               'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'ignore'))
    rows = []
    for r in (data or []):
        try: rows.append((r.get('opendate'), float(r['netamount']), float(r['ratioamount']) * 100))
        except (TypeError, ValueError, KeyError): continue
    return rows[::-1]

flows, fail = {}, 0
for k, code in enumerate(top_codes):
    try:
        rows = sina_flow(code)
        if len(rows) >= 60: flows[code] = pd.DataFrame(rows, columns=['d', 'net', 'ratio'])
        else: fail += 1
    except Exception:
        fail += 1
        if fail > 30: print('失败过多,中止于', k); break
    time.sleep(0.3)
    if (k + 1) % 50 == 0: print(f'  {k+1}/{len(top_codes)} ok={len(flows)}', flush=True)
print(f'新浪资金流历史: {len(flows)} 只成功, {fail} 失败')

if len(flows) >= 50:
    fr = []
    for code, df in flows.items():
        df = df.sort_values('d')
        df['r5'] = df.ratio.rolling(5).mean()
        df['pos5'] = (df.net > 0).rolling(5).sum()
        df['code'] = code
        fr.append(df[['code', 'd', 'r5', 'pos5']])
    FR = pd.concat(fr)
    M = D.merge(FR, on=['code', 'd'], how='inner').dropna(subset=['r5'])
    print(f'合并底部池: {len(M)} 行')
    def ev(sub, lab):
        n = len(sub)
        if n < 100: print(f'{lab:<42} n={n} 过小'); return
        w = sub.win.mean()*100; s = sub.stopped.mean()*100
        print(f'{lab:<42} n={n:<5} 胜{w:5.1f}%  先砸-8%={s:4.1f}%  EV≈{w/100*5-s/100*8:+.2f}%')
    print('\n== 底部池 × 5日主力净占比(新浪口径) ==')
    ev(M, '基线(有资金数据的底部行)')
    ev(M[M.r5 >= 3], '强流入≥+3%')
    ev(M[(M.r5 > 0) & (M.r5 < 3)], '温和流入0~3%')
    ev(M[(M.r5 <= 0) & (M.r5 > -3)], '温和流出0~-3%')
    ev(M[M.r5 <= -3], '强流出≤-3%')
    ev(M[M.pos5 >= 4], '5日≥4天净流入')
    ev(M[M.pos5 <= 1], '5日≤1天净流入')
    print('\n== 叠加到手工分上(增量检验) ==')
    ev(M[M.hand >= 18], '手工分≥18(子样本基线)')
    ev(M[(M.hand >= 18) & (M.r5 > 0)], '≥18 + 5日净流入')
    ev(M[(M.hand >= 18) & (M.r5 <= 0)], '≥18 + 5日净流出')
    ev(M[(M.hand >= 18) & (M.r5 <= -3)], '≥18 + 强流出(候选veto)')
    ev(M[(M.hand >= 18) & (M.atr <= 4)], '≥18+ATR≤4(子样本)')
    ev(M[(M.hand >= 18) & (M.atr <= 4) & (M.r5 > 0)], '≥18+ATR≤4+流入(全叠)')
else:
    print('新浪源也失败')
