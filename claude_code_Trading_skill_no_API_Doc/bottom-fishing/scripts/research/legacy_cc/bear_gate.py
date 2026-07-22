# -*- coding: utf-8 -*-
"""牛熊闸门检验: 创业板 vs MA250(年线) — 能否把2024式毒月与2025-26黄金期分开"""
import pandas as pd, numpy as np, sys, pathlib
scr = pathlib.Path(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad')
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import json, urllib.request, time


def em_index(n=900):
    """新浪日K(第三源): 指数900根"""
    url = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol=sz399006&scale=240&ma=no&datalen={n}')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                               'Referer': 'https://finance.sina.com.cn/'})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8', 'ignore'))
    return pd.DataFrame([(r['day'], float(r['open']), float(r['close']), float(r['high']),
                          float(r['low']), float(r['volume'])) for r in data],
                        columns=['d', 'o', 'c', 'h', 'l', 'v'])


src = (scr / 'toxic_month.py').read_text(encoding='utf-8')
src = src.replace("ix = tx('sz399006')", "ix = em_index()")
exec(src.split("print('\\n== A.")[0])  # 复用面板构建到 L(过线笔+race结果)

ix['ma250'] = ix.c.rolling(250, min_periods=180).mean()
ix['bull'] = ix.c >= ix.ma250
L = L.merge(ix[['d', 'bull']], on='d', how='left')
L['y'] = L.d.str[:4]

def ev(sub, lab):
    n = len(sub)
    if n < 30:
        print(f'{lab:<30} n={n} 过小'); return
    print(f'{lab:<30} n={n:<5} 胜{sub.win.mean()*100:5.1f}%  先砸-8%={sub.stopped.mean()*100:4.1f}%  EV≈{sub.win.mean()*5-sub.stopped.mean()*8:+.2f}%')

print('== 牛熊闸门: 创业板 vs MA250 ==')
ev(L[L.bull == True], '年线上方(牛市回调抄底)')   # noqa: E712
ev(L[L.bull == False], '年线下方(熊市接刀)')      # noqa: E712
print()
for y, gg in L.groupby('y'):
    for b, lab in [(True, '年线上'), (False, '年线下')]:
        s = gg[gg.bull == b]
        if len(s) >= 30:
            ev(s, f'{y}·{lab}')
above = L[L.bull == True]  # noqa: E712
tox_n = sum(1 for ym, g in above.groupby(above.d.str[:7]) if len(g) >= 15 and g.stopped.mean() >= 0.3)
print(f'\n年线上方: 毒月数 {tox_n} (无闸门时11个) · 覆盖{above.d.nunique()}/{L.d.nunique()}天 · '
      f'其中2025-26占{(above.y >= "2025").mean()*100:.0f}%')
# 关键蒙混检验: 年线上方的2024样本(若有)成绩如何 — 防止闸门只是"2024全滤掉"的化名
a24 = above[above.y == '2024']
print(f'年线上方·2024子样本: n={len(a24)}' + (f' 胜{a24.win.mean()*100:.0f}% 雷{a24.stopped.mean()*100:.0f}%' if len(a24) >= 20 else ' (过小/无)'))
