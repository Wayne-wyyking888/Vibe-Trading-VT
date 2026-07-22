# -*- coding: utf-8 -*-
"""2024修复因子测试: MA60斜率/大盘250日回撤/市场宽度/新低家数 —— 关键看2024内部分离度
+ 滚动停做开关历史仿真(2024全程开着能救回多少)"""
import pandas as pd, numpy as np, sys, pathlib, json, urllib.request
scr = pathlib.Path(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad')
sys.path.insert(0, r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank')
import json as _json, urllib.request as _ur, time


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
exec(src.split("print('\\n== A.")[0])   # → p(全特征), L(过线笔+win/stopped), ix

# 新市场因子
ix['ma60'] = ix.c.rolling(60).mean()
ix['ma60_slope'] = ix.ma60.pct_change(20) * 100          # MA60的20日斜率%
ix['hi250'] = ix.h.rolling(250, min_periods=120).max()
ix['idx_dd'] = (ix.c / ix.hi250 - 1) * 100               # 大盘距250日高点回撤
# 市场宽度(用面板算, 无幸存者外数据但同池一致)
p['above20'] = p.c > p.ma5.rolling(1).mean()  # placeholder避免重复算; 真实用ma20:
p['ma20s'] = p.groupby('code', group_keys=False).c.transform(lambda s: s.rolling(20).mean())
p['ab20'] = p.c > p.ma20s
p['nl60'] = p.l <= p.groupby('code', group_keys=False).l.transform(lambda s: s.rolling(60).min()) * 1.001
br = p.groupby('d').agg(breadth=('ab20', 'mean'), newlow=('nl60', 'mean')).reset_index()
br['breadth'] *= 100; br['newlow'] *= 100
br['breadth_chg5'] = br.breadth.diff(5)
M = ix[['d', 'ma60_slope', 'idx_dd']].merge(br, on='d', how='inner')
L2 = L.merge(M, on='d', how='left')
L2['y'] = L2.d.str[:4]

def ev(sub, lab):
    n = len(sub)
    if n < 25:
        print(f'{lab:<40} n={n} 过小'); return
    print(f'{lab:<40} n={n:<5} 胜{sub.win.mean()*100:5.1f}%  雷{sub.stopped.mean()*100:4.1f}%  EV≈{sub.win.mean()*5-sub.stopped.mean()*8:+.2f}%')

print('== 各因子: 全期 + 2024内部 分离度 ==')
factors = [
    ('MA60斜率>0(中期趋势向上)', L2.ma60_slope > 0),
    ('大盘250日回撤<-20%(深熊区)', L2.idx_dd <= -20),
    ('市场宽度>30%(3成股在MA20上)', L2.breadth > 30),
    ('宽度5日回升(breadth_chg5>0)', L2.breadth_chg5 > 0),
    ('新低家数<5%(杀跌衰竭)', L2.newlow < 5),
]
for lab, m in factors:
    print(f'\n-- {lab} --')
    ev(L2[m], '  ON ·全期'); ev(L2[~m], '  OFF·全期')
    y24 = L2[L2.y == '2024']
    ev(y24[m.reindex(y24.index).fillna(False)], '  ON ·2024内部')
    ev(y24[~m.reindex(y24.index).fillna(True)], '  OFF·2024内部')

print('\n== 滚动停做开关 历史仿真(近20笔已了结雷率≥30%停/回落<20%恢复, 无前视) ==')
ixdays = {d_: i for i, d_ in enumerate(sorted(ix.d.unique()))}
LS = L2.dropna(subset=['days']).sort_values('d').reset_index(drop=True)
LS['pos'] = LS.d.map(ixdays)
LS['resolve_pos'] = LS.pos + 1 + LS.days.astype(int)
taken, skipped = [], []
resolved: list[tuple[int, int]] = []   # (resolve_pos, stopped)
active = True
for _, t in LS.iterrows():
    done = [o for rp, o in resolved if rp <= t.pos]
    recent = done[-20:]
    if len(recent) >= 10:
        rate = sum(recent) / len(recent)
        if active and rate >= 0.30:
            active = False
        elif (not active) and rate < 0.20:
            active = True
    (taken if active else skipped).append(t)
    resolved.append((int(t.resolve_pos), int(t.stopped)))
    resolved.sort()
TK, SK = pd.DataFrame(taken), pd.DataFrame(skipped)
print(f'开关全程: 执行{len(TK)}笔 / 跳过{len(SK)}笔')
for y in ['2024', '2025', '2026']:
    tk, sk = TK[TK.y == y], SK[SK.y == y]
    line = f'{y}: 执行{len(tk)}笔'
    if len(tk) >= 25:
        line += f'(胜{tk.win.mean()*100:.0f}%/雷{tk.stopped.mean()*100:.0f}%/EV{tk.win.mean()*5-tk.stopped.mean()*8:+.2f}%)'
    line += f'  跳过{len(sk)}笔'
    if len(sk) >= 25:
        line += f'(被跳过组真实雷率{sk.stopped.mean()*100:.0f}%)'
    print(line)
