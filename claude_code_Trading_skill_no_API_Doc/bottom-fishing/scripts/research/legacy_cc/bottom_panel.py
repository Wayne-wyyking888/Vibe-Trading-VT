# -*- coding: utf-8 -*-
"""抄底可行性研究·第一步: 建大面板 ~480只 × 400日"""
import sys, json, time, urllib.request
import pandas as pd, numpy as np
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import ashare_weekly_rank as eng
scr = r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad'

spot = eng.get_spot(600)
codes = []
for _, r in spot.iterrows():
    code, name = str(r.get('代码', '')), str(r.get('名称', ''))
    if len(code) != 6 or code.startswith(('68', '8', '4')): continue
    if 'ST' in name.upper() or '退' in name: continue
    codes.append(code)
print(f'universe: {len(codes)}', flush=True)

def tx_kline(sym, n=400):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq'
    j = json.loads(urllib.request.urlopen(url, timeout=12).read())
    rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day') or []
    return pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                         for r in rows], columns=['d', 'o', 'c', 'h', 'l', 'v'])

idx = tx_kline('sz399006')
idx['iret'] = idx.c.pct_change(); idx['ma20'] = idx.c.rolling(20).mean(); idx['i5'] = idx.c.pct_change(5)
idx['defensive'] = (idx.c < idx.ma20) | (idx.i5 < -0.02)

rows = []
for k, code in enumerate(codes):
    sym = ('sh' if code[0] in '69' else 'sz') + code
    try:
        df = tx_kline(sym)
    except Exception:
        time.sleep(0.3); continue
    if len(df) < 120: continue
    df = df.merge(idx[['d', 'iret', 'defensive']], on='d', how='inner')
    df['code'] = code
    rows.append(df)
    if (k + 1) % 80 == 0: print(f'  {k+1}/{len(codes)}', flush=True)
    time.sleep(0.06)

panel = pd.concat(rows, ignore_index=True)
panel.to_pickle(scr + r'\panel480.pkl')
print(f'saved: {len(panel)} rows, {panel.code.nunique()} stocks, {panel.d.nunique()} days')
