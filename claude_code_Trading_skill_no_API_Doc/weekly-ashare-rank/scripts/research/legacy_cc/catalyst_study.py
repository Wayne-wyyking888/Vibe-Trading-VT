# -*- coding: utf-8 -*-
"""催化剂诅咒检验：历史各期选股HTML里,有新鲜催化的票 vs 无/存量催化的票,选出后的真实收益对比"""
import re, glob, json, os, time, datetime as dt
import urllib.request

RPT = r'C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank\reports'

def parse_report(path):
    h = open(path, encoding='utf-8').read()
    m = re.search(r'T\(数据截止\)</b>：(\d{4}-\d{2}-\d{2})', h)
    if not m: return None
    T = m.group(1)
    cards = re.split(r"<div class='card'?[^>]*>", h)[1:]
    rows = []
    for c in cards:
        mm = re.search(r'class=rank>(\d+)</div>.*?class=title>([^<]+)</span><span class=tk>(\d{6})</span>', c, re.S)
        if not mm: continue
        rank, name, code = int(mm.group(1)), mm.group(2), mm.group(3)
        badge = re.search(r"class=badge[^>]*>([^<]+)</span>", c)
        cat = re.search(r"催化剂</span>(.*?)</div>", c, re.S)
        cat_text = re.sub(r'<[^>]+>', '', cat.group(1)) if cat else ''
        rows.append(dict(T=T, rank=rank, code=code, name=name,
                         badge=badge.group(1) if badge else '', cat=cat_text.strip()))
    return T, rows

def classify(cat, T):
    """新鲜=文本含T-7日内的日期且无'无新鲜/存量'否定; 否则 无/存量"""
    if not cat: return '无/存量'
    if re.search(r'无[^。;；,，]{0,8}(新鲜|利好|催化)|存量逻辑|存量为主|旧闻|未证实|证伪', cat):
        return '无/存量'
    Td = dt.date.fromisoformat(T)
    dates = re.findall(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})', cat)
    for y, mo, dd in dates:
        try:
            d0 = dt.date(int(y) if y else Td.year, int(mo), int(dd))
        except ValueError:
            continue
        if 0 <= (Td - d0).days <= 7:
            return '新鲜'
    if '⚡' in cat or '新鲜' in cat:
        return '新鲜'
    return '无/存量'

_kcache = {}
def kline(code):
    if code in _kcache: return _kcache[code]
    pre = 'sh' if code[0] in '69' else 'sz'
    sym = pre + code
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,120,qfq'
    try:
        j = json.loads(urllib.request.urlopen(url, timeout=12).read())
        rows = j['data'][sym].get('qfqday') or j['data'][sym].get('day') or []
        out = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]  # date,open,close,high,low
    except Exception as e:
        out = []
    _kcache[code] = out
    time.sleep(0.15)
    return out

def fwd(code, T):
    ks = kline(code)
    idx = {d[0]: i for i, d in enumerate(ks)}
    if T not in idx: return None
    i = idx[T]
    if i + 1 >= len(ks): return None
    c0 = ks[i][2]; o1, c1, lo1 = ks[i+1][1], ks[i+1][2], ks[i+1][4]
    r = dict(r1_oc=(c1/o1-1)*100, r1_cc=(c1/c0-1)*100, gap=(o1/c0-1)*100)
    j3 = min(i+3, len(ks)-1)
    r['r3_cc'] = (ks[j3][2]/c0-1)*100
    r['n_fwd'] = j3 - i
    r['mdd'] = (min(k[4] for k in ks[i+1:j3+1])/c0-1)*100
    return r

# ---- main ----
files = sorted(glob.glob(os.path.join(RPT, 'ashare_rank_cn_*.html')))
byT = {}
for f in files:
    p = parse_report(f)
    if p: byT[p[0]] = p[1]   # 同T多版取最后(文件名时间升序)

recs = []
for T, rows in sorted(byT.items()):
    for r in rows:
        r['cls'] = classify(r['cat'], T)
        fw = fwd(r['code'], T)
        if fw: r.update(fw)
        recs.append(r)

done = [r for r in recs if 'r1_cc' in r and r['n_fwd'] >= 1 and r['T'] < '2026-07-10']
today = [r for r in recs if r['T'] == '2026-07-10']

def agg(rs, label):
    if not rs: return
    n = len(rs)
    def m(k): return sum(x[k] for x in rs)/n
    win = sum(1 for x in rs if x['r1_oc'] > 0)/n*100
    win3 = sum(1 for x in rs if x['r3_cc'] > 0)/n*100
    print(f'{label:<26} n={n:<3} 跳空{m("gap"):+.2f}%  T+1开→收{m("r1_oc"):+.2f}%(胜{win:.0f}%)  T+1收{m("r1_cc"):+.2f}%  3日{m("r3_cc"):+.2f}%(胜{win3:.0f}%)  最大回撤{m("mdd"):+.2f}%')

print(f'=== 样本: {len(byT)}期 T∈[{min(byT)},{max(byT)}], 有效票次 {len(done)} (T=2026-07-10当期未走完,另列) ===\n')
agg(done, '全部')
agg([r for r in done if r['cls']=='新鲜'], '有新鲜催化(≤7天带日期)')
agg([r for r in done if r['cls']=='无/存量'], '无/存量催化')
print()
agg([r for r in done if r['rank']<=3], '排名前3')
agg([r for r in done if r['rank']>3], '排名4以后')
print()
agg([r for r in done if r['cls']=='新鲜' and r['rank']<=3], '前3且新鲜催化')
agg([r for r in done if r['cls']=='无/存量' and r['rank']<=3], '前3且无/存量')
agg([r for r in done if r['cls']=='新鲜' and r['rank']>3], '4名后且新鲜催化')
agg([r for r in done if r['cls']=='无/存量' and r['rank']>3], '4名后且无/存量')
# 可买 vs 剔除/观察
print()
agg([r for r in done if '可买' in r['badge']], '状态=可买')
agg([r for r in done if '可买' not in r['badge']], '状态=观察/剔除')
agg([r for r in done if '可买' in r['badge'] and r['cls']=='新鲜'], '可买且新鲜催化')
agg([r for r in done if '可买' in r['badge'] and r['cls']=='无/存量'], '可买且无/存量')

print('\n=== T+1开盘买入后 最差15笔 ===')
for r in sorted(done, key=lambda x: x['r1_oc'])[:15]:
    print(f"{r['T']} #{r['rank']} {r['code']} {r['name']:<5} {r['cls']:<4} {r['badge']:<10} T+1开→收{r['r1_oc']:+.2f}% 3日{r['r3_cc']:+.2f}% | {r['cat'][:48]}")
print('\n=== 最好10笔 ===')
for r in sorted(done, key=lambda x: -x['r1_oc'])[:10]:
    print(f"{r['T']} #{r['rank']} {r['code']} {r['name']:<5} {r['cls']:<4} {r['badge']:<10} T+1开→收{r['r1_oc']:+.2f}% 3日{r['r3_cc']:+.2f}% | {r['cat'][:48]}")

json.dump(recs, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalyst_study_raw.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nraw ->', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalyst_study_raw.json'))
