# -*- coding: utf-8 -*-
"""P13-1/2 是否够格直接改:显著性 + 政策仿真(历史上按新规执行,可买篮子收益变化)"""
import json, math, re, datetime as dt, statistics as st
import urllib.request, time

raw = json.load(open(r'C:\Users\18297\AppData\Local\Temp\claude\c--Trading-analysis\e21be88c-e82a-4cca-87ed-69420236c89f\scratchpad\catalyst_study_raw.json', encoding='utf-8'))
done = [r for r in raw if 'r1_cc' in r and r.get('n_fwd',0) >= 1 and r['T'] < '2026-07-10']

def welch(a, b):
    if len(a) < 3 or len(b) < 3: return float('nan')
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va/len(a) + vb/len(b))
    return (ma-mb)/se if se else float('nan')

def grp(rs, k): return [r[k] for r in rs]

buy = [r for r in done if '可买' in r['badge']]
buy_fresh = [r for r in buy if r['cls']=='新鲜']
buy_stale = [r for r in buy if r['cls']=='无/存量']
tail_fresh = [r for r in done if r['rank']>3 and r['cls']=='新鲜']
tail_stale = [r for r in done if r['rank']>3 and r['cls']=='无/存量']

print('== 显著性(Welch t, |t|>2≈95%置信) ==')
for k in ['r1_oc','r3_cc']:
    print(f'可买×无新鲜({len(buy_stale)}) vs 可买×新鲜({len(buy_fresh)})  {k}: t={welch(grp(buy_stale,k),grp(buy_fresh,k)):.2f}  均值 {st.mean(grp(buy_stale,k)):+.2f} vs {st.mean(grp(buy_fresh,k)):+.2f}')
for k in ['r1_oc','r3_cc']:
    print(f'4名后×新鲜({len(tail_fresh)}) vs 4名后×无({len(tail_stale)})  {k}: t={welch(grp(tail_fresh,k),grp(tail_stale,k)):.2f}  均值 {st.mean(grp(tail_fresh,k)):+.2f} vs {st.mean(grp(tail_stale,k)):+.2f}')

print('\n== 政策仿真: 历史"可买"篮子, 按 P13-1(前3且新鲜才可买) 重算 ==')
keep = [r for r in buy if r['rank']<=3 and r['cls']=='新鲜']
cut  = [r for r in buy if not (r['rank']<=3 and r['cls']=='新鲜')]
for lab, rs in [('原可买篮子', buy), ('新规保留', keep), ('新规砍掉', cut)]:
    if rs:
        print(f"{lab:<8} n={len(rs):<3} T+1开→收{st.mean(grp(rs,'r1_oc')):+.2f}% 3日{st.mean(grp(rs,'r3_cc')):+.2f}% 胜率{sum(1 for x in rs if x['r1_oc']>0)/len(rs)*100:.0f}%")
print('被砍的赢家(冤杀):', [(r['T'],r['name'],round(r['r1_oc'],1)) for r in cut if r['r1_oc']>1])
print('被砍的输家(立功):', [(r['T'],r['name'],round(r['r1_oc'],1)) for r in cut if r['r1_oc']<-1])
# 每期剩几只可买
from collections import Counter
ck = Counter(r['T'] for r in keep); cb = Counter(r['T'] for r in buy)
print('每期可买数变化:', {t: f"{cb[t]}->{ck.get(t,0)}" for t in sorted(cb)})

print('\n== P13-2 检验: 新鲜催化票按"催化日→T 已涨停过或累涨>10%"分组 ==')
_k = {}
def kline(code):
    if code in _k: return _k[code]
    pre = 'sh' if code[0] in '69' else 'sz'; sym = pre+code
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,120,qfq'
    try:
        j = json.loads(urllib.request.urlopen(url, timeout=12).read())
        rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day') or []
        _k[code] = [(r[0], float(r[2])) for r in rows]
    except Exception: _k[code] = []
    time.sleep(0.12); return _k[code]

fresh_all = [r for r in done if r['cls']=='新鲜']
pin, notpin = [], []
for r in fresh_all:
    Td = dt.date.fromisoformat(r['T'])
    cds = []
    for y, mo, dd in re.findall(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})', r['cat']):
        try: d0 = dt.date(int(y) if y else Td.year, int(mo), int(dd))
        except ValueError: continue
        if 0 <= (Td-d0).days <= 7: cds.append(d0)
    if not cds: continue
    cd = min(cds)
    ks = kline(r['code'])
    seg = [(d,c) for d,c in ks if cd.isoformat() <= d <= r['T']]
    if len(seg) < 2: notpin.append(r); continue
    ret = seg[-1][1]/seg[0][1]-1
    zt = any(seg[i][1]/seg[i-1][1]-1 >= 0.093 for i in range(1,len(seg)))
    (pin if (ret > 0.10 or zt) else notpin).append(r)
for lab, rs in [('已兑现(涨停过/累涨>10%)', pin), ('未兑现', notpin)]:
    if rs:
        print(f"{lab:<18} n={len(rs):<3} T+1开→收{st.mean(grp(rs,'r1_oc')):+.2f}% 3日{st.mean(grp(rs,'r3_cc')):+.2f}% 胜率{sum(1 for x in rs if x['r1_oc']>0)/len(rs)*100:.0f}%  t(r1)={welch(grp(pin,'r1_oc'),grp(notpin,'r1_oc')):.2f}" if lab.startswith('已') else f"{lab:<18} n={len(rs):<3} T+1开→收{st.mean(grp(rs,'r1_oc')):+.2f}% 3日{st.mean(grp(rs,'r3_cc')):+.2f}% 胜率{sum(1 for x in rs if x['r1_oc']>0)/len(rs)*100:.0f}%")
