# -*- coding: utf-8 -*-
"""中电港 07-13 -9.5% 归因：β分解 / T日与买入日分时结构 / 红日抗跌统计 / 8只候选T+1复盘"""
import json, urllib.request, time, statistics as st

def tx_kline(sym, n=120):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq'
    j = json.loads(urllib.request.urlopen(url, timeout=12).read())
    rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day')
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]  # d,o,c,h,l,v

def em_min5(secid, lmt=400):
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + secid +
           '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=5&fqt=1&end=20500101&lmt=' + str(lmt))
    j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'}), timeout=12).read())
    kl = (j.get('data') or {}).get('klines') or []
    out = []
    for s in kl:
        f = s.split(',')  # time,o,c,h,l,vol,amt
        out.append((f[0], float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5]), float(f[6])))
    return out

zdg = tx_kline('sz001287'); cyb = tx_kline('sz399006')
dz = {d[0]: d for d in zdg}; dc = {d[0]: d for d in cyb}
common = [d for d in dz if d in dc]
common.sort()
rets_s, rets_i = [], []
for a, b in zip(common[:-1], common[1:]):
    rets_s.append(dz[b][2]/dz[a][2]-1); rets_i.append(dc[b][2]/dc[a][2]-1)
n = len(rets_s)
mb, ms = st.mean(rets_i), st.mean(rets_s)
cov = sum((x-ms)*(y-mb) for x, y in zip(rets_s, rets_i))/n
var = sum((y-mb)**2 for y in rets_i)/n
beta = cov/var
# 下行beta: 只用创业板跌日
dn = [(x, y) for x, y in zip(rets_s, rets_i) if y < 0]
mbd = st.mean([y for _, y in dn]); msd = st.mean([x for x, _ in dn])
covd = sum((x-msd)*(y-mbd) for x, y in dn)/len(dn)
vard = sum((y-mbd)**2 for _, y in dn)/len(dn)
beta_dn = covd/vard
# 红日(创业板跌>1%)时中电港表现
red = [(x, y) for x, y in zip(rets_s, rets_i) if y <= -0.01]
print(f'=== β分解(近{n}个交易日, 对创业板指) ===')
print(f'全样本β={beta:.2f}  下行β(创跌日)={beta_dn:.2f}  样本{len(dn)}跌日')
print(f'创业板跌>1%的日子({len(red)}天): 中电港平均{st.mean([x for x,_ in red])*100:+.2f}% vs 创业板{st.mean([y for _,y in red])*100:+.2f}%')
lose = sum(1 for x, y in red if x < y)
print(f'  其中跌得比创业板还多的天数: {lose}/{len(red)}')
print(f'07-13实测: 创业板-3.10% × 下行β{beta_dn:.2f} = 预期{-3.10*beta_dn:.1f}%  实际-9.54% → 残差(个股α){-9.54-(-3.10*beta_dn):+.1f}%')

print('\n=== T日(07-10) 5分钟结构 ===')
m5 = em_min5('0.001287', 500)
for day, lab in [('2026-07-10', 'T日'), ('2026-07-13', '买入日')]:
    bars = [b for b in m5 if b[0].startswith(day)]
    if not bars: print(day, '无分时数据'); continue
    o, c = bars[0][1], bars[-1][2]
    hi = max(b[3] for b in bars); lo = min(b[4] for b in bars)
    vtot = sum(b[5] for b in bars)
    am = [b for b in bars if b[0][11:16] <= '11:30']; pm = [b for b in bars if b[0][11:16] > '13:00']
    am_ret = am[-1][2]/o-1 if am else 0
    pm_ret = pm[-1][2]/pm[0][1]-1 if pm else 0
    tail = [b for b in bars if b[0][11:16] >= '14:30']
    tail_ret = tail[-1][2]/tail[0][1]-1 if tail else 0
    tail_vol = sum(b[5] for b in tail)/vtot*100 if vtot else 0
    # 分时高点回落
    peak_t = max(bars, key=lambda b: b[3])
    print(f'{lab} {day}: 开{o} 收{c} 高{hi}({peak_t[0][11:16]}) 低{lo} | 上午{am_ret*100:+.1f}% 下午{pm_ret*100:+.1f}% 尾盘30min{tail_ret*100:+.1f}%(量占{tail_vol:.0f}%) | 高点回落{(c/hi-1)*100:+.1f}%')

print('\n=== 8只候选 T日% → 买入日% (T日抗跌是否=买入日补跌) ===')
eight = {'001287':'中电港','600288':'大恒科技','603118':'共进股份','000066':'中国长城','002396':'星网锐捷','002077':'大港股份','603087':'甘李药业','301191':'菲菱科思'}
tchg = {'001287':0.88,'600288':-1.43,'603118':5.81,'000066':-2.16,'002396':-5.15,'002077':-4.77,'603087':5.95,'301191':-2.51}
pairs=[]
for code, name in eight.items():
    sym = ('sh' if code[0] in '69' else 'sz')+code
    ks = tx_kline(sym, 8)
    d13 = [k for k in ks if k[0]=='2026-07-13']
    d10 = [k for k in ks if k[0]=='2026-07-10']
    if d13 and d10:
        r = (d13[0][2]/d10[0][2]-1)*100
        pairs.append((tchg[code], r))
        print(f'{name:<5} T日{tchg[code]:+.2f}% → 买入日{r:+.2f}%')
    time.sleep(0.1)
mx=st.mean([p[0] for p in pairs]); my=st.mean([p[1] for p in pairs])
r_corr = sum((a-mx)*(b-my) for a,b in pairs)/((sum((a-mx)**2 for a,b in pairs)*sum((b-my)**2 for a,b in pairs))**0.5)
print(f'相关系数(T日% vs 买入日%): {r_corr:+.2f}  (n=8, 仅提示性)')
