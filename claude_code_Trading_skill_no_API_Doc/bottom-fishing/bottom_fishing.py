# -*- coding: utf-8 -*-
"""抄底扫描引擎（0 API·东财/腾讯免费源）
方法论: 2026-07-14 可行性研究(476股×401日≈19万股票日, 见 README.md / weekly DOC§研究记忆)
- 底部区 = 距60日高点回撤≥20% 且 60日位≤25
- 修复确认打分(手工权重=面板Δ胜率, 多元学习权重已被OOS否决勿改回)
- 双路径推荐线: 防守日·总分≥18 ∥ 非防守日·个股分≥15, 均须 ATR≤4
- 走样本(OOS含退潮段): 胜75.2~77.8% / 先砸-8%=13.8~13.9% / EV≈+2.7%/笔; 月度胜率45~92%会大摆
- 影子复验: 每次过线票记入 shadow log, --review 自动对账, 累计30笔前均为影子期
用法:  python bottom_fishing.py            # T日扫描+HTML
       python bottom_fishing.py --review   # 对账历史过线票(+5%先到 vs -8%先砸)
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request

import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
WEEKLY = HERE.parent / "weekly-ashare-rank"
sys.path.insert(0, str(WEEKLY))
import ashare_weekly_rank as WK  # noqa: E402  复用: get_spot(带缓存) / _cn_now(北京时间)

DATA = pathlib.Path(r"C:\Trading_analysis\data")
SHADOW = DATA / "bottom_shadow_log.jsonl"
OUT_JSON = DATA / "bottom_latest.json"
ADJUD = DATA / "bottom_adjudication.json"   # Agent②裁定(人工消息面), --adjudicate 合并进HTML
REPORTS = HERE / "reports"

# 推荐线参数(2026-07-14 定版, 改动须先过走样本)
TH_TOTAL, TH_STOCK, TH_ATR = 18.0, 15.0, 4.0
STOP_PCT, TGT1, TGT2, MAX_HOLD = -8.0, 5.0, 10.0, 20
POS_CAP = 3.5   # 单票仓位上限%(跌停封死风险>动量票, 必须比P14的6%小)

W = dict(defensive=8.6, above_ma10=5.2, dif_up=4.5, rsv_recover=3.9, dd_sweet=3.7,
         above_ma5=3.7, gap_reclaim=4.4,
         rsv_deep=-7.4, downstk4=-6.3, zt20=-5.4, atr_hi=-3.5, fresh_low=-3.1)
W_LABEL = dict(defensive="防守日", above_ma10="站回MA10", dif_up="DIF3日向上", rsv_recover="RSV回升20-40",
               dd_sweet="回撤30-45%", above_ma5="站回MA5", gap_reclaim="低开2%收回",
               rsv_deep="深超卖RSV≤15", downstk4="≥4连阴", zt20="20日涨停基因", atr_hi="ATR≥7",
               fresh_low="刚创新低")


# 多域名故障切换: web.ifzq 会被腾讯WAF按出口IP限流(2026-07-14实测501), 三域返回同一份qfq数据
_TX_HOSTS = ["https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
             "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
             "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"]


def tx_kline(sym: str, n: int = 140) -> pd.DataFrame | None:
    for base in _TX_HOSTS:
        try:
            j = json.loads(urllib.request.urlopen(f"{base}?param={sym},day,,,{n},qfq", timeout=12).read())
            rows = j["data"][sym].get("qfqday") or j["data"][sym].get("day") or []
            df = pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                               for r in rows], columns=["d", "o", "c", "h", "l", "v"])
            if len(df) >= 90:
                _TX_HOSTS.remove(base)          # 命中的域提到最前, 避免每票都先撞被封域
                _TX_HOSTS.insert(0, base)
                return df
            return None  # 数据不足(次新/长停牌), 换域也一样
        except Exception:  # noqa: BLE001
            continue
    return None


def _drop_intraday(df: pd.DataFrame) -> pd.DataFrame:
    """盘中跑时最后一根是未完成K线 → 丢弃, T=最近已收盘日。"""
    now = WK._cn_now()
    if df.iloc[-1].d == now.strftime("%Y-%m-%d") and (now.hour, now.minute) < (15, 5):
        return df.iloc[:-1]
    return df


def index_features() -> dict:
    df = _drop_intraday(tx_kline("sz399006", 140))
    df["ma20"] = df.c.rolling(20).mean()
    df["i5"] = df.c.pct_change(5)
    df["defensive"] = (df.c < df.ma20) | (df.i5 < -0.02)
    cnt, dd = 0, []
    for v in df.defensive:
        cnt = cnt + 1 if v else 0
        dd.append(cnt)
    lo14 = df.l.rolling(14).min()
    hi14 = df.h.rolling(14).max()
    rsv = (df.c - lo14) / (hi14 - lo14 + 1e-9) * 100
    r = df.iloc[-1]
    return dict(T=r.d, close=round(r.c, 2), defensive=bool(r.defensive),
                def_days=int(dd[-1]), idx_rsv=round(float(rsv.iloc[-1]), 1),
                chg1=round((r.c / df.iloc[-2].c - 1) * 100, 2))


def stock_factors(df: pd.DataFrame, defensive: bool) -> dict | None:
    """在最后一根已收盘K线上算全部因子+打分。不在底部区返回 None。"""
    s = df
    hi60 = s.h.rolling(60).max()
    lo60 = s.l.rolling(60).min()
    c = s.iloc[-1]
    dd60 = (c.c / hi60.iloc[-1] - 1) * 100
    pos60 = (c.c - lo60.iloc[-1]) / (hi60.iloc[-1] - lo60.iloc[-1] + 1e-9) * 100
    if not (dd60 <= -20 and pos60 <= 25):
        return None
    ret = s.c.pct_change()
    ma5 = s.c.rolling(5).mean().iloc[-1]
    ma10 = s.c.rolling(10).mean().iloc[-1]
    tr = pd.concat([s.h - s.l, (s.h - s.c.shift()).abs(), (s.l - s.c.shift()).abs()], axis=1).max(axis=1)
    atr = (tr.rolling(14).mean() / s.c).iloc[-1] * 100
    ema12 = s.c.ewm(span=12, adjust=False).mean()
    ema26 = s.c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    lo14 = s.l.rolling(14).min()
    hi14 = s.h.rolling(14).max()
    rsv = float(((s.c - lo14) / (hi14 - lo14 + 1e-9) * 100).iloc[-1])
    is_low = s.l <= lo60 * 1.001
    cnt = 999
    for v in is_low:
        cnt = 0 if v else cnt + 1
    downstk = 0
    for r_ in reversed(ret.tolist()):
        if pd.isna(r_) or r_ >= 0:
            break
        downstk += 1
    hits = dict(
        defensive=defensive,
        above_ma10=bool(c.c > ma10),
        dif_up=bool((dif.iloc[-1] - dif.iloc[-4]) > 0),
        rsv_recover=bool(20 < rsv <= 40),
        dd_sweet=bool(-45 < dd60 <= -30),
        above_ma5=bool(c.c > ma5),
        gap_reclaim=bool(c.o < s.c.iloc[-2] * 0.98 and c.c > c.o),
        rsv_deep=bool(rsv <= 15),
        downstk4=bool(downstk >= 4),
        zt20=bool((ret.iloc[-20:] >= 0.093).any()),
        atr_hi=bool(atr >= 7),
        fresh_low=bool(cnt <= 1),
    )
    score = sum(W[k] for k, v in hits.items() if v)
    stock_score = score - (W["defensive"] if defensive else 0)
    vma20 = s.v.rolling(20).mean().iloc[-1]
    return dict(T=c.d, close=round(float(c.c), 2), dd60=round(float(dd60), 1),
                pos60=round(float(pos60), 1), atr=round(float(atr), 2), rsv=round(rsv, 1),
                ret5=round(float(s.c.pct_change(5).iloc[-1] * 100), 2),
                volx=round(float(c.v / vma20), 2) if vma20 else None,
                score=round(float(score), 1), stock_score=round(float(stock_score), 1),
                hits={k: bool(v) for k, v in hits.items()})


def scan() -> dict:
    idx = index_features()
    print(f"[bottom] T={idx['T']} 创业板{idx['chg1']:+.2f}% 防守日={idx['defensive']}"
          f"(已{idx['def_days']}天) 大盘RSV={idx['idx_rsv']}")
    spot = WK.get_spot(600)
    codes = []
    for _, r in spot.iterrows():
        code, name = str(r.get("代码", "")), str(r.get("名称", ""))
        if len(code) != 6 or code.startswith(("68", "8", "4")):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        codes.append((code, name, str(r.get("行业", "") or "")))
    print(f"[bottom] universe={len(codes)}, 拉K线扫描底部区(约2-4分钟) ...")
    cands, obs = [], []
    for k, (code, name, ind) in enumerate(codes):
        sym = ("sh" if code[0] in "69" else "sz") + code
        df = tx_kline(sym)
        if df is None:
            continue
        df = _drop_intraday(df)
        if df.iloc[-1].d != idx["T"]:
            continue  # 停牌/数据滞后
        f = stock_factors(df, idx["defensive"])
        if f is None:
            continue
        f.update(code=code, name=name, industry=ind)
        path_ok = (idx["defensive"] and f["score"] >= TH_TOTAL) or \
                  ((not idx["defensive"]) and f["stock_score"] >= TH_STOCK)
        f["qualified"] = bool(path_ok and f["atr"] <= TH_ATR)
        f["fail_why"] = "" if f["qualified"] else \
            ("ATR>%.0f" % TH_ATR if path_ok else ("总分<%.0f" % TH_TOTAL if idx["defensive"] else "个股分<%.0f" % TH_STOCK))
        (cands if f["qualified"] else obs).append(f)
        if (k + 1) % 100 == 0:
            print(f"[bottom]   {k+1}/{len(codes)}  底部区累计{len(cands)+len(obs)}")
        time.sleep(0.05)
    cands.sort(key=lambda x: -x["score"])
    obs.sort(key=lambda x: -x["score"])
    # 权威交易日历: 买入日T+1 / 最晚离场日T+MAX_HOLD 的真实日期
    try:
        win = WK._trade_window(idx["T"], MAX_HOLD)
        buy_str = f"{win['buy_date']}({win['buy_dow']})"
        sell_str = f"{win['sell_by']}({win['sell_dow']})"
    except Exception:  # noqa: BLE001
        buy_str, sell_str = "T+1(下一交易日)", f"T+{MAX_HOLD}"
    # F10客观种子(仅过线票, 复用weekly引擎已验证接口): 业绩预告/近14天公告/扣非/PE → 供Agent②错杀裁定
    if cands:
        try:
            fund = WK.fetch_fundamentals([c["code"] for c in cands])
        except Exception:  # noqa: BLE001
            fund = {}
        for c in cands:
            f10 = fund.get(c["code"], {})
            c["kcfj_yoy"], c["pe"] = f10.get("kcfj_yoy"), f10.get("pe")
            try:
                nt = WK.fetch_recent_notices(c["code"], idx["T"])
                c["forecast"] = nt.get("forecast")
                c["notices"] = [f"{n['date']} {n['title'][:28]}" for n in (nt.get("notices") or [])[:4]]
                fc = nt.get("forecast") or {}
                bad_fc = any(k in str(fc.get("type") or "") for k in ("亏", "减"))
                bad_nt = any(k in t for t in c["notices"] for k in ("减持", "解除质押", "质押", "解禁", "立案", "警示"))
                c["f10_flag"] = "⚠F10负面待裁定" if (bad_fc or bad_nt) else ""
            except Exception:  # noqa: BLE001
                c["forecast"], c["notices"], c["f10_flag"] = None, [], ""
            time.sleep(0.2)
    for i, c in enumerate(cands, 1):
        c["rank"] = i
        stop = round(c["close"] * (1 + STOP_PCT / 100), 2)
        c["plan"] = dict(entry=f"{buy_str} 开盘市价(竞价高开>3%放弃·不追)",
                         stop=stop, stop_note=f"{stop}(较T收盘{STOP_PCT:.0f}%·成交即挂券商条件单)",
                         tgt1=round(c["close"] * (1 + TGT1 / 100), 2),
                         tgt2=round(c["close"] * (1 + TGT2 / 100), 2),
                         hold=f"最晚{sell_str}收盘离场", pos=f"≤{POS_CAP}%")
    result = dict(generated_at=WK._cn_now().isoformat(timespec="seconds"), T=idx["T"],
                  market=idx, threshold=dict(total=TH_TOTAL, stock=TH_STOCK, atr=TH_ATR),
                  n_bottom_zone=len(cands) + len(obs), candidates=cands, observe=obs[:10],
                  disclosure=dict(win_oos="75.2~77.8%", stop_oos="13.8~13.9%(CI≤16.3)",
                                  monthly_range="月度胜率45~92%·爆雷2~55%成簇(毒月内61%的雷集中在同一周,78%来自反复过线的同批票)",
                                  regime_risk="⚠市况依赖型: 2024式阴跌熊市全年EV为负(51.6%胜/42.7%雷,2024全年实测)——"
                                              "毒月里输家与赢家的个股特征完全相同,选股端无解,牛熊闸门(MA250)已测无效",
                                  window_bias="2025-26窗口+幸存者池,数字打七折",
                                  shadow="影子复验期(30笔前):建议纸面跟踪或仓位减半; 滚动停做:近20笔雷率≥30%即停"))
    DATA.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(SHADOW, "a", encoding="utf-8") as fh:
        for c in cands:
            fh.write(json.dumps(dict(T=idx["T"], code=c["code"], name=c["name"], close=c["close"],
                                     score=c["score"], atr=c["atr"], def_days=idx["def_days"],
                                     idx_rsv=idx["idx_rsv"], outcome=None), ensure_ascii=False) + "\n")
    _print_report(result)
    html = render_html(result)
    print(f"\n📄 HTML: {html}\n💾 JSON: {OUT_JSON}\n🗒 影子日志: {SHADOW}")
    return result


def _hits_str(c: dict) -> str:
    pos = [W_LABEL[k] for k, v in c["hits"].items() if v and W[k] > 0]
    neg = [W_LABEL[k] for k, v in c["hits"].items() if v and W[k] < 0]
    return "+".join(pos) + (" | ⚠" + "+".join(neg) if neg else "")


def _print_report(r: dict) -> None:
    m = r["market"]
    print(f"\n## 抄底扫描 T={r['T']}  市况: {'防守日(第%d天)' % m['def_days'] if m['defensive'] else '非防守日'}"
          f" · 大盘RSV={m['idx_rsv']} · 底部区{r['n_bottom_zone']}只")
    if not r["candidates"]:
        print("\n**本期结论: 空手** —— 无过线票(合格线宁缺毋滥, 187日面板中约6成日子无票, 属正常)")
    else:
        print(f"\n| 排名 | 代码 | 名称 | 行业 | 收盘 | 总分 | 个股分 | ATR% | 回撤% | 命中 |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for c in r["candidates"]:
            print(f"| {c['rank']} | {c['code']} | {c['name']} | {c['industry']} | {c['close']} | "
                  f"{c['score']} | {c['stock_score']} | {c['atr']} | {c['dd60']} | {_hits_str(c)} |")
        print(f"\n执行纪律: T+1开盘进场(高开>3%放弃) · 止损-8%条件单立挂 · 买入日收≤-5%次日无条件出(崩盘快刀)"
              f" · +5%落袋一半/+10%清 · ≤{MAX_HOLD}日到期离场 · 单票≤{POS_CAP}% · 月度抄底总亏损≤账户3%触顶停做")
    if r["observe"]:
        print("\n观察池(底部区未过线): " + ", ".join(
            f"{o['code']}{o['name']}(分{o['score']}·{o['fail_why']})" for o in r["observe"][:8]))
    d = r["disclosure"]
    print(f"\n⚖ 真实口径: OOS胜率{d['win_oos']} · 先砸-8%={d['stop_oos']} · {d['monthly_range']} · "
          f"{d['window_bias']} · {d['shadow']}")


def render_html(r: dict) -> pathlib.Path:
    REPORTS.mkdir(exist_ok=True)
    m = r["market"]
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    regime = f"防守日·第{m['def_days']}天" if m["defensive"] else "非防守日"
    cards = ""
    _BADGE = {"✓": "<span class=badge>✓ 错杀可入·可执行(影子期)</span>",
              "?": "<span class=badge style='background:#e08a00'>? 存疑降级·不进买入</span>",
              "✗": "<span class=badge style='background:#b23'>✗ 否决(裁定)</span>"}
    for c in r["candidates"]:
        p = c.get("plan")   # ?/✗票在 adjudicate() 已撤方案(P13-3: 非买入票不给价)
        j = c.get("judge")
        cards += (f"<div class=card{' style=opacity:.55' if j == '✗' else ''}>"
                  f"<div class=top><span class=rk>{c['rank']}</span>"
                  f"<b>{esc(c['name'])}</b><span class=tk>{c['code']}·{esc(c['industry'])}</span>"
                  f"{_BADGE.get(j, '<span class=badge>过线·待裁定(影子期)</span>')}<span class=sc>总分 {c['score']}</span></div>"
                  f"<div class=row>收盘 <b>{c['close']}</b> · 回撤{c['dd60']}% · 60日位{c['pos60']} · "
                  f"ATR {c['atr']}% · RSV {c['rsv']} · 5日{c['ret5']}%</div>"
                  f"<div class=row>信号: {esc(_hits_str(c))}</div>"
                  + ((f"<div class=row style='background:#f3f0fa;border-radius:6px;padding:6px 9px'>"
                      f"<b>裁定{esc(j)}</b> {esc(c.get('judge_why', ''))}</div>") if j else "")
                  + "".join(f"<div class={'alert-hi' if a.get('level') == 'high' else 'alert-md'}>"
                            f"⚠ {esc(a.get('text', ''))}</div>" for a in c.get("alerts") or [])
                  + ((f"<div class=row>F10: 扣非同比{c.get('kcfj_yoy')}% · PE {c.get('pe')} "
                      + (f"<b style='color:#b23'>{esc(c['f10_flag'])}</b>" if c.get('f10_flag') else '')
                      + (("<br>公告: " + esc('; '.join(c['notices'][:3]))) if c.get('notices') else '')
                      + "</div>") if c.get('kcfj_yoy') is not None or c.get('notices') else "")
                  + ((f"<div class=plan>进场 <b>{esc(p['entry'])}</b><br>止损 <b>{esc(p.get('stop_note', p['stop']))}</b><br>"
                      f"目标 <b>{p['tgt1']}(+5%落袋半仓) / {p['tgt2']}(+10%清)</b> · {esc(p['hold'])} · 仓位{p['pos']}</div>"
                      f"<div class=row style='color:#b23'>纪律: 竞价高开&gt;3%放弃(不追) · 买入日收盘≤-5%→次日开盘无条件出(崩盘快刀) · "
                      f"止损条件单成交即挂(跌停封死风险高于动量票,故仓位≤{POS_CAP}%)</div>") if p else "")
                  + "</div>")
    if not r["candidates"]:
        cards = "<div class=empty>🈳 <b>本期空手</b> —— 无过线票。合格线宁缺毋滥(历史约6成日子无票)，不降格凑数。</div>"
    n_ok = sum(1 for c in r["candidates"] if c.get("judge") == "✓")
    adj_note = f" · Agent②裁定后可入<b>{n_ok}</b>只" if r.get("adjudicated") else ""
    galerts = "".join(f"<div class={'alert-hi' if a.get('level') == 'high' else 'alert-md'}>"
                      f"⚠ {esc(a.get('text', ''))}</div>" for a in r.get("alerts") or [])
    obs = "".join(f"<li>{o['code']} {esc(o['name'])}（分{o['score']}·{esc(o['fail_why'])}）</li>"
                  for o in r["observe"])
    d = r["disclosure"]
    html = f"""<meta charset=utf-8><title>抄底扫描 {r['T']}</title>
<style>body{{font-family:'Microsoft YaHei',sans-serif;max-width:880px;margin:24px auto;padding:0 14px;color:#222;background:#f6f7f9}}
.env{{border:2px solid {'#c62828' if m['defensive'] else '#1565c0'};border-radius:10px;padding:10px 14px;background:#fff;margin:10px 0}}
.warn{{background:#fff8e1;border:1px solid #e6c26a;border-radius:8px;padding:9px 12px;font-size:13px;margin:8px 0}}
.card{{background:#fff;border:1px solid #dde;border-radius:12px;padding:12px 15px;margin:10px 0;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
.top{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #eef;padding-bottom:7px;margin-bottom:7px}}
.rk{{background:#1565c0;color:#fff;border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-weight:700}}
.tk{{color:#889;font-family:monospace}}.badge{{background:#2e7d32;color:#fff;border-radius:9px;padding:2px 9px;font-size:12px}}
.sc{{margin-left:auto;background:#eef4ff;color:#1565c0;border-radius:6px;padding:2px 8px;font-weight:700}}
.row{{font-size:13.5px;margin:4px 0;color:#445}}.plan{{background:#f3f6fa;border-radius:8px;padding:7px 10px;font-size:13.5px;margin:6px 0}}
.empty{{background:#fff;border:1px dashed #aab;border-radius:12px;padding:26px;text-align:center;font-size:16px;color:#556}}
.alert-hi{{background:#fdecea;border:1.5px solid #e57373;color:#b71c1c;border-radius:8px;padding:7px 10px;font-size:13.5px;font-weight:600;margin:6px 0}}
.alert-md{{background:#fff8e1;border:1px solid #e6c26a;color:#8a6d1a;border-radius:8px;padding:6px 10px;font-size:13px;margin:5px 0}}
.foot{{font-size:12px;color:#788;margin-top:16px;border-top:1px solid #dde;padding-top:9px}}
ul{{font-size:13px;color:#556}}</style>
<h2>🎣 抄底扫描 — T={r['T']}（买入日=T+1）</h2>
<div class=env><b>市况: {regime}</b> · 创业板T日{m['chg1']:+.2f}% · 大盘RSV={m['idx_rsv']} ·
底部区候选{r['n_bottom_zone']}只 · 过线<b>{len(r['candidates'])}</b>只{adj_note}
<br><span style='font-size:12.5px;color:#667'>推荐线: {'总分≥%.0f(防守日路径)' % TH_TOTAL if m['defensive'] else '个股分≥%.0f(非防守日路径)' % TH_STOCK} 且 ATR≤{TH_ATR:.0f}%</span></div>
{galerts}
<div class=warn>⚖ <b>真实口径(必读)</b>: 走样本 胜率{d['win_oos']}(先到+5% vs 先砸-8%,20日窗) · 爆雷率{d['stop_oos']} ·
{d['monthly_range']} · {d['window_bias']} · <b>{d['shadow']}</b> · 拆档: 触-10%=12% / -15%=4% / +10%=53% / +20%=19%
<br>{d.get('regime_risk','')}</div>
{cards}
<h3>观察池（底部区·未过线）</h3><ul>{obs or '<li>无</li>'}</ul>
<div class=foot>生成(北京) {r['generated_at']} · 数据: 腾讯qfq/东财快照(免费) · 本报告为研究分析非投资建议 ·
月度预算: 抄底累计亏损达账户-3%当月停做 · 复盘: python bottom_fishing.py --review</div>"""
    tag = "_裁定版" if r.get("adjudicated") else ""
    path = REPORTS / f"bottom_{r['T']}_{WK._cn_now().strftime('%H-%M-%S')}{tag}.html"
    path.write_text(html, encoding="utf-8")
    return path


def adjudicate() -> None:
    """合并 Agent②裁定(bottom_adjudication.json) → 分层重排 → 裁定版HTML + 影子日志回写。
    纪律: 不造新数值权重(未经走样本校准) —— 只按 ✓>?>✗ 分层, 层内仍按引擎总分排序;
    ?/✗票撤除买入方案(P13-3: 非买入票不给价), 卡片保留供对照。"""
    if not (OUT_JSON.exists() and ADJUD.exists()):
        print("缺 bottom_latest.json 或 bottom_adjudication.json")
        return
    r = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    adj = json.loads(ADJUD.read_text(encoding="utf-8"))
    if adj.get("T") != r["T"]:
        print(f"⚠ 裁定文件T={adj.get('T')} ≠ 最新扫描T={r['T']}, 拒绝合并(先重跑扫描或更新裁定)")
        return
    rulings = adj.get("rulings", {})
    r["alerts"] = adj.get("alerts") or []          # 报告级全局警示(高亮横幅)
    for c in r["candidates"]:
        v = rulings.get(c["code"], {})
        c["judge"], c["judge_why"] = v.get("verdict", "?"), v.get("why", "未裁定(按存疑处理)")
        c["alerts"] = v.get("alerts") or []        # 个股警示: [{"level":"high|med","text":...}]
        if c["judge"] != "✓":
            c.pop("plan", None)
    order = {"✓": 0, "?": 1, "✗": 2}
    r["candidates"].sort(key=lambda c: (order.get(c["judge"], 1), -c["score"]))
    for i, c in enumerate(r["candidates"], 1):
        c["rank"] = i
    r["adjudicated"] = True
    r["adjudicated_at"] = WK._cn_now().isoformat(timespec="seconds")
    OUT_JSON.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    # 影子日志回写裁定 → --review 可拆「引擎线 vs 裁定✓子集」两条战绩, 检验Agent②增量
    if SHADOW.exists():
        lines = [json.loads(x) for x in SHADOW.read_text(encoding="utf-8").splitlines() if x.strip()]
        for e in lines:
            if e["T"] == r["T"] and e["code"] in rulings:
                e["judge"] = rulings[e["code"]].get("verdict")
        SHADOW.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in lines) + "\n", encoding="utf-8")
    print(f"## 裁定后重排 T={r['T']}  (✓可入 {sum(1 for c in r['candidates'] if c['judge']=='✓')}只)")
    for c in r["candidates"]:
        print(f"  {c['rank']}. {c['judge']} {c['code']} {c['name']} 总分{c['score']} — {c['judge_why'][:48]}")
    html = render_html(r)
    removed = 0
    for old in REPORTS.glob(f"bottom_{r['T']}_*.html"):   # 同T旧版(含旧裁定版)一律清理, 只留最新裁定版
        if old != html:
            old.unlink()
            removed += 1
    if removed:
        print(f"🧹 已清理同T旧版HTML {removed} 份")
    print(f"📄 裁定版HTML: {html}")


def review() -> None:
    """对账影子日志: 每笔过线票跑赛跑(T+1开盘进场, 先+5%还是先-8%, 20交易日)。"""
    if not SHADOW.exists():
        print("无影子日志")
        return
    lines = [json.loads(x) for x in SHADOW.read_text(encoding="utf-8").splitlines() if x.strip()]
    kcache: dict[str, pd.DataFrame] = {}
    changed = False
    for e in lines:
        if e.get("outcome") is not None:
            continue
        code = e["code"]
        sym = ("sh" if code[0] in "69" else "sz") + code
        if code not in kcache:
            kcache[code] = _drop_intraday(tx_kline(sym, 120))
            time.sleep(0.1)
        df = kcache[code]
        if df is None:
            continue
        ds = df.d.tolist()
        if e["T"] not in ds:
            continue
        i = ds.index(e["T"])
        if i + 2 >= len(df):
            continue  # T+1还没走完
        entry = df.iloc[i + 1].o
        stop, tgt = entry * (1 + STOP_PCT / 100), entry * (1 + TGT1 / 100)
        out, days = None, None
        if df.iloc[i + 1].c <= stop:
            out, days = "stop", 1
        else:
            for j in range(i + 2, min(i + 1 + MAX_HOLD, len(df) - 1) + 1):
                if df.iloc[j].l <= stop:
                    out, days = "stop", j - (i + 1)
                    break
                if df.iloc[j].h >= tgt:
                    out, days = "win", j - (i + 1)
                    break
            if out is None and i + 1 + MAX_HOLD <= len(df) - 1:
                out, days = "timeout", MAX_HOLD
        if out:
            e["outcome"], e["days"], e["entry"] = out, days, round(float(entry), 2)
            changed = True
    if changed:
        SHADOW.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in lines) + "\n", encoding="utf-8")
    done = [e for e in lines if e.get("outcome")]
    n = len(done)
    if not n:
        print("暂无已了结样本")
        return
    w = sum(1 for e in done if e["outcome"] == "win")
    s = sum(1 for e in done if e["outcome"] == "stop")
    print(f"## 抄底影子复验  已了结 {n} 笔 (目标30笔转正)")
    print(f"胜(先到+5%) {w}/{n} = {w/n*100:.0f}%   先砸-8% {s}/{n} = {s/n*100:.0f}%   "
          f"[回测口径: 75.2% / 13.9% — 偏离即报告]")
    jd = [e for e in done if e.get("judge") == "✓"]
    if jd:
        jw = sum(1 for e in jd if e["outcome"] == "win")
        js = sum(1 for e in jd if e["outcome"] == "stop")
        print(f"裁定✓子集 {len(jd)}笔: 胜{jw/len(jd)*100:.0f}% 雷{js/len(jd)*100:.0f}%  (对照引擎全线, 检验Agent②消息面裁定的增量)")
    # 滚动停做开关(2026-07-14 毒月研究: 2024式熊市EV为负且个股端无解, 唯一防御=预算型熔断)
    recent = done[-20:]
    rs = sum(1 for e in recent if e["outcome"] == "stop")
    if len(recent) >= 10 and rs / len(recent) >= 0.30:
        print(f"\n⛔ 滚动停做开关触发: 近{len(recent)}笔雷率{rs/len(recent)*100:.0f}%≥30% —— "
              f"疑似进入2024式毒月市况(选股端无解), 建议暂停抄底至该比率回落<20%")
    for e in done[-15:]:
        print(f"  {e['T']} {e['code']}{e['name']} 分{e['score']} → {e['outcome']}({e.get('days')}天)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true", help="对账影子日志")
    ap.add_argument("--adjudicate", action="store_true", help="合并Agent②裁定(bottom_adjudication.json)重排重出HTML")
    a = ap.parse_args()
    review() if a.review else (adjudicate() if a.adjudicate else scan())
