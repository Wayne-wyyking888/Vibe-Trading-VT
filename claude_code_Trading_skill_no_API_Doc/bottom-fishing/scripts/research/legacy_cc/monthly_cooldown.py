# -*- coding: utf-8 -*-
"""当前整套系统(推荐线+旋转门冷却) 2024→今 逐月回测 vs 无冷却旧口径
冷却语义(照引擎): 同票距上次『过线』(含被冷却压下的过线)≤10个交易日 → 本次不推荐, 但刷新过线时间"""
import pandas as pd, numpy as np, sys, pathlib, json, urllib.request
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
exec(src.split("print('\\n== A.")[0])   # → L: 过线笔(win/stopped/days), p, pos_map

L = L.copy()
L['pos'] = [pos_map[i] for i in L.index]        # 该票自身K线内的交易日序号
L = L.sort_values(['code', 'd'])
rec = []
for code, gg in L.groupby('code'):
    last_q = None
    for i, t in gg.iterrows():
        is_rec = (last_q is None) or (t.pos - last_q > 10)
        rec.append((i, is_rec))
        last_q = t.pos                            # 冷却票也刷新(照引擎shadow log语义)
rec = dict(rec)
L['recommended'] = L.index.map(rec)
NEW, OLD = L[L.recommended], L

def row(sub):
    n = len(sub)
    if n == 0: return '     —'
    return f'{n:>4}笔 {sub.win.mean()*100:3.0f}%/{sub.stopped.mean()*100:3.0f}%'

print(f'全期: 旧口径 {len(OLD)}笔 胜{OLD.win.mean()*100:.1f}%/雷{OLD.stopped.mean()*100:.1f}%  →  '
      f'新口径(+冷却) {len(NEW)}笔 胜{NEW.win.mean()*100:.1f}%/雷{NEW.stopped.mean()*100:.1f}%')
cool = L[~L.recommended]
print(f'被冷却压下的 {len(cool)}笔: 胜{cool.win.mean()*100:.1f}%/雷{cool.stopped.mean()*100:.1f}% (对照: 比推荐组差=规则在省雷)\n')

print('%-9s %-22s %-22s' % ('月份', '旧口径 n/胜/雷', '新口径(+冷却) n/胜/雷'))
for ym in sorted(L.ym.unique()):
    if ym < '2024-01': continue
    o, nn = OLD[OLD.ym == ym], NEW[NEW.ym == ym]
    if len(o) < 5: continue
    mark = ' ⚠' if len(nn) and nn.stopped.mean() >= 0.3 else ''
    print('%-9s %-22s %-22s%s' % (ym, row(o), row(nn), mark))
print()
for y, gg in L.groupby(L.d.str[:4]):
    o, nn = OLD[OLD.d.str[:4] == y], NEW[NEW.d.str[:4] == y]
    if len(o) < 30: continue
    evo = o.win.mean() * 5 - o.stopped.mean() * 8
    evn = nn.win.mean() * 5 - nn.stopped.mean() * 8
    print(f'{y}: 旧 {len(o)}笔 胜{o.win.mean()*100:.1f}%/雷{o.stopped.mean()*100:.1f}%/EV{evo:+.2f}  →  '
          f'新 {len(nn)}笔 胜{nn.win.mean()*100:.1f}%/雷{nn.stopped.mean()*100:.1f}%/EV{evn:+.2f}')
