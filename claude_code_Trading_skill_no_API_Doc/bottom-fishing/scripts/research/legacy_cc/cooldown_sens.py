# -*- coding: utf-8 -*-
"""旋转门冷却天数敏感性: N=0/3/5/7/10/15 (语义照引擎: 距上次过线≤N交易日→压下, 压下也刷新计时)"""
import pandas as pd, numpy as np, sys, pathlib
scr = pathlib.Path(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad')
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import json as _json, urllib.request as _ur


def sina_index(n=900):
    url = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol=sz399006&scale=240&ma=no&datalen={n}')
    req = _ur.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
    data = _json.loads(_ur.urlopen(req, timeout=15).read().decode('utf-8', 'ignore'))
    return pd.DataFrame([(r['day'], float(r['open']), float(r['close']), float(r['high']),
                          float(r['low']), float(r['volume'])) for r in data],
                        columns=['d', 'o', 'c', 'h', 'l', 'v'])


src = (scr / 'toxic_month.py').read_text(encoding='utf-8')
src = src.replace("ix = tx('sz399006')", "ix = sina_index()")
exec(src.split("print('\\n== A.")[0])

L = L.copy()
L['pos'] = [pos_map[i] for i in L.index]
L['y'] = L.d.str[:4]
L = L.sort_values(['code', 'd'])

def sim(N):
    if N == 0:
        return L
    keep = []
    for code, gg in L.groupby('code'):
        last_q = None
        for i, t in gg.iterrows():
            if (last_q is None) or (t.pos - last_q > N):
                keep.append(i)
            last_q = t.pos
    return L.loc[keep]

print('%-5s %7s %8s %7s %7s %8s | %-24s %-24s %-24s' %
      ('N', '保留笔', '保留%', '胜率', '雷率', 'EV/笔', '2024', '2025', '2026'))
for N in [0, 3, 5, 7, 10, 15]:
    S = sim(N)
    parts = []
    for y in ['2024', '2025', '2026']:
        g = S[S.y == y]
        ev = g.win.mean() * 5 - g.stopped.mean() * 8
        parts.append(f'{len(g)}笔 {g.win.mean()*100:.0f}/{g.stopped.mean()*100:.0f} EV{ev:+.2f}')
    ev_all = S.win.mean() * 5 - S.stopped.mean() * 8
    print('%-5s %6d %7.0f%% %6.1f%% %6.1f%% %+7.2f%% | %-24s %-24s %-24s' %
          (N, len(S), len(S)/len(L)*100, S.win.mean()*100, S.stopped.mean()*100, ev_all, *parts))

print('\n每日平均推荐数(有票日): ', end='')
for N in [0, 3, 5, 7, 10]:
    S = sim(N)
    print(f'N={N}: {len(S)/S.d.nunique():.1f}只/{S.d.nunique()}天  ', end='')
print()
