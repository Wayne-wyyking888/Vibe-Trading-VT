#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场环境·情绪闸门（Agent⓪ 的量化底座）：指数环境 + 涨停/炸板/跌停情绪 → 环境分 + 总仓闸门。

为什么需要：选股 skill 之前只看个股，对"大盘跳空/风格切换/情绪退潮"没有总闸门
（例：2026-06-05 创业板-3%+费半暴跌，06-08 沪指竞价直接低开-2.2%；煤炭5板大有能源
情绪高潮次日即跌停）。本脚本把这些环境信号量化成 0-100 环境分 + 总仓上限 + 跳空预案。

用法（选股前或盘前都可跑，约10秒）:
  python market_gate.py
  python market_gate.py --out C:/Trading_analysis/data/market_gate_latest.json
输出：
  - stdout markdown 摘要（指数表 + 情绪表 + 裁定）
  - JSON（score/regime/max_total_position/advice，供 SKILL 流程与报告引用）
数据源：腾讯指数日线（免费）+ 东财涨停/炸板/跌停池（免费，失败自动降级只用指数）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eng", HERE / "ashare_weekly_rank.py")
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

INDICES = [
    ("sh000001", "上证指数", 1.0),
    ("sz399001", "深证成指", 0.5),
    ("sz399006", "创业板指", 1.0),
    ("sh000688", "科创50", 0.5),
]

# ---- P12 隔夜/外盘引擎直拉（新浪全球行情，报价自带北京时间戳，与本机时钟/时区无关）----
# 教训 2026-07-07：本机时区是UTC-6，跑skill的LLM按本机日期拼出"美股 收盘 7月3日"（该日美股
# 还休市），把大涨的美股周一时段判成偏空。外盘指数类信息一律由本函数直出，WebSearch只补新闻。
_OVERNIGHT_SYMS = [
    ("gb_ixic", "纳斯达克"),
    ("gb_sox", "费城半导体SOX"),
    ("gb_dji", "道琼斯"),
    ("gb_inx", "标普500"),
    ("hf_CHA50CFD", "富时中国A50期货"),
]


def fetch_overnight() -> list[dict]:
    """新浪 hq.sinajs 全球指数/期货最新报价。gb_*=美股指数(字段3=北京时间戳)；
    hf_*=环球期货(字段6=时间,12=日期)。失败返回[]，绝不抛。"""
    try:
        url = "https://hq.sinajs.cn/list=" + ",".join(s for s, _ in _OVERNIGHT_SYMS)
        r = eng._SESSION.get(url, timeout=10,
                             headers={"Referer": "https://finance.sina.com.cn"})
        r.raise_for_status()
        txt = r.content.decode("gbk", errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    import datetime as dt
    cn_now = eng._cn_now()
    tz8 = dt.timezone(dt.timedelta(hours=8))
    out = []
    for line in txt.splitlines():
        if "=\"" not in line:
            continue
        sym = line.split("=")[0].replace("var hq_str_", "").strip()
        name = dict(_OVERNIGHT_SYMS).get(sym)
        if not name:
            continue
        f = line.split("\"")[1].split(",")
        try:
            if sym.startswith("gb_"):
                price, pct, ts_s = float(f[1]), float(f[2]), f[3]
                ts = dt.datetime.fromisoformat(ts_s).replace(tzinfo=tz8)
            else:  # hf_：0=最新价 7=昨结 6=时间 12=日期
                price = float(f[0])
                prev = float(f[7]) if f[7] else None
                pct = round((price / prev - 1) * 100, 2) if prev else None
                ts = dt.datetime.fromisoformat(f"{f[12]} {f[6]}").replace(tzinfo=tz8)
        except (ValueError, IndexError, TypeError):
            continue
        age_h = round((cn_now - ts).total_seconds() / 3600, 1)
        out.append({"sym": sym, "name": name, "price": price, "pct": pct,
                    "quote_time_cn": ts.strftime("%Y-%m-%d %H:%M"), "age_h": age_h})
    return out
_ZT_HOSTS = ["https://push2ex.eastmoney.com"]
_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _idx_kline(sec: str, bars: int = 40) -> list[dict] | None:
    """腾讯指数日线（指数代码已含 sh/sz 前缀，不能走引擎的 _tx_secid）。"""
    try:
        r = eng._SESSION.get(eng._TX_KLINE, params={"param": f"{sec},day,,,{bars},qfq"}, timeout=15)
        node = r.json().get("data", {}).get(sec, {})
    except Exception:  # noqa: BLE001
        return None
    kl = node.get("qfqday") or node.get("day") or []
    if len(kl) < 25:
        return None
    return [{"date": x[0], "o": float(x[1]), "c": float(x[2]),
             "h": float(x[3]), "l": float(x[4]), "v": float(x[5])} for x in kl]


def _pool(kind: str, date8: str) -> list[dict] | None:
    """东财涨停/炸板/跌停池。kind: ZT/ZB/DT。失败返回 None（降级，不报错）。"""
    path = {"ZT": "/getTopicZTPool", "ZB": "/getTopicZBPool", "DT": "/getTopicDTPool"}[kind]
    try:
        r = eng._SESSION.get(_ZT_HOSTS[0] + path, timeout=15, params={
            "ut": _UT, "dpt": "wz.ztzt", "Pageindex": 0, "pagesize": 600,
            "sort": "fbt:asc", "date": date8})
        data = (r.json() or {}).get("data") or {}
        return data.get("pool") or []
    except Exception:  # noqa: BLE001
        return None


def _idx_stats(k: list[dict]) -> dict:
    """单指数环境指标：T日涨跌/5日/MA20位置/量能/连跌。"""
    c = [x["c"] for x in k]
    v = [x["v"] for x in k]
    chg = (c[-1] / c[-2] - 1) * 100
    chg5 = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0.0
    ma20 = sum(c[-20:]) / 20
    vol_ratio = v[-1] / (sum(v[-6:-1]) / 5) if sum(v[-6:-1]) > 0 else 1.0
    down_streak = 0
    for i in range(len(c) - 1, 0, -1):
        if c[i] < c[i - 1]:
            down_streak += 1
        else:
            break
    return {"date": k[-1]["date"], "close": round(c[-1], 2), "chg": round(chg, 2),
            "chg5": round(chg5, 2), "above_ma20": c[-1] > ma20,
            "vol_ratio": round(vol_ratio, 2), "down_streak": down_streak,
            "heavy_sell": vol_ratio > 1.2 and chg < -0.5}


def _sentiment(date_iso: str, prev_iso: str | None) -> dict | None:
    """涨停情绪：家数/最高连板/炸板率/跌停 + 环比。任一池子拉不到就整体降级。"""
    d8 = date_iso.replace("-", "")
    zt = _pool("ZT", d8)
    if zt is None:
        return None
    time.sleep(0.3)
    zb = _pool("ZB", d8) or []
    time.sleep(0.3)
    dt = _pool("DT", d8) or []
    lbc = [int(x.get("lbc") or 1) for x in zt] or [0]
    out = {"date": date_iso, "zt_count": len(zt), "max_streak": max(lbc),
           "zb_count": len(zb), "dt_count": len(dt),
           "zb_rate": round(len(zb) / (len(zt) + len(zb)) * 100, 1) if (zt or zb) else 0.0}
    if prev_iso:
        time.sleep(0.3)
        zt_prev = _pool("ZT", prev_iso.replace("-", ""))
        if zt_prev is not None:
            out["zt_prev"] = len(zt_prev)
            out["zt_shrink"] = len(zt_prev) > 0 and len(zt) < 0.55 * len(zt_prev)
    return out


def assess(idx: dict[str, dict], senti: dict | None) -> dict:
    """环境分(0-100, 越高越友好) + 裁定 + 总仓闸门 + 操作预案。"""
    score, reasons = 50.0, []
    sh, cyb = idx.get("sh000001"), idx.get("sz399006")
    for tag, s, w in (("上证", sh, 1.0), ("创业板", cyb, 1.0)):
        if not s:
            continue
        if s["above_ma20"]:
            score += 8 * w
        else:
            score -= 10 * w
            reasons.append(f"{tag}跌破MA20")
        if s["chg"] <= -2:
            score -= 12 * w
            reasons.append(f"{tag}T日大跌{s['chg']}%")
        elif s["chg"] <= -0.7:
            score -= 6 * w
        elif s["chg"] >= 1:
            score += 4 * w
        if s["heavy_sell"]:
            score -= 8 * w
            reasons.append(f"{tag}放量下跌(量比{s['vol_ratio']})")
        if s["down_streak"] >= 3:
            score -= 5 * w
            reasons.append(f"{tag}连跌{s['down_streak']}天")
    if senti:
        zt, zbr, dtc = senti["zt_count"], senti["zb_rate"], senti["dt_count"]
        if zt >= 80:
            score += 10
        elif zt >= 40:
            score += 5
        elif zt < 25:
            score -= 10
            reasons.append(f"涨停仅{zt}家(赚钱效应弱)")
        if zbr > 40:
            score -= 10
            reasons.append(f"炸板率{zbr}%(分歧大)")
        elif zbr < 20 and zt >= 40:
            score += 5
        if dtc >= 20:
            score -= 10
            reasons.append(f"跌停{dtc}家")
        if dtc > zt:
            score -= 10
            reasons.append("跌停多于涨停(亏钱效应)")
        if senti["max_streak"] >= 6 and zbr > 35:
            score -= 5
            reasons.append(f"连板高度{senti['max_streak']}+高炸板(情绪高潮分歧,防退潮)")
        if senti.get("zt_shrink"):
            score -= 8
            reasons.append(f"涨停家数环比腰斩({senti.get('zt_prev')}→{senti['zt_count']})")
        # M5 情绪高潮亢奋降档：涨停环比激增(≥1.7倍)+绝对高(≥80)+炸板已现(≥22%) → 见顶回吐风险
        # (2026-06-29 医药政策涨停107 vs 前日60、炸板26%却给进攻80，次日 CRO 集体回吐的教训)
        ztp = senti.get("zt_prev")
        if ztp and zt >= 80 and zt >= ztp * 1.7 and zbr >= 22:
            score -= 10
            reasons.append(f"涨停环比激增({ztp}→{zt})+炸板{zbr}%(情绪高潮亢奋,次日追高易兑现回吐)")
    score = max(0.0, min(100.0, round(score, 1)))
    if score >= 70:
        regime, pos = "进攻(risk-on)", 60
        plan = "正常按计划执行；竞价高开>3%仍等回调。"
    elif score >= 55:
        regime, pos = "中性", 50
        plan = "按计划执行但不满仓；优先位置低+独立催化的票。"
    elif score >= 40:
        regime, pos = "防守(risk-off)", 30
        plan = ("防守期预案：① 总仓≤30%、单票≤6%；② 竞价大盘低开>1% → 首30分钟只看不买，"
                "只在买入区下半段挂单接回调；③ 只买独立催化票，回避前一日被集中抛售主题；"
                "④ 综合分权重切防守(量化0.35/催化0.40/风险0.25)。")
    else:
        regime, pos = "观望(risk-off重度)", 15
        plan = "亏钱效应主导：建议空仓或≤15%仓只打1只最强独立催化票；等大盘止跌信号再进。"
    return {"score": score, "regime": regime, "max_total_position_pct": pos,
            "reasons": reasons, "plan": plan}


# ---------------------------------------------------------------- T+1 前瞻 nowcast
# 设计原则（与本 skill 的诚实校准文化一致）：
# - 次日指数方向的真实样本外天花板只有 ~52-56%，绝不吹"预测涨跌"。这里只做透明的
#   概率化 nowcast = ① 历史类比基准率(无未来函数) + ② 情绪延续/退潮微调；外盘(美股/SOX/
#   富时A50/汇率)由 Claude 在 Agent⓪ 用 WebSearch 叠加（引擎无法联网搜）。
# - **展示 + 背离告警用，绝不自动改仓位/综合打分**。下行风险闸门(assess)永远独占仓位上限，
#   一个"偏多"的前瞻不许松开它（守住 SKILL.md P5：别靠预测择时去赌 V 反）。
# - 置信度由"回测命中率(扩张窗口、无前视)"诚实封顶；命中率≈50% 就明说"≈随机·仅供参考"。

_FEAT_W = np.array([1.3, 1.0, 1.1, 1.2, 0.8, 0.9])  # chg, chg5, vol_ratio, dist_ma20, down_streak, rng_pos20


def _feat_matrix(k: list[dict]):
    """每根K的状态特征矩阵 F 及其 T+1 收益/跳空（最后一根的 T+1 未知=nan）。无未来函数。"""
    c = np.array([x["c"] for x in k], float)
    o = np.array([x["o"] for x in k], float)
    v = np.array([x["v"] for x in k], float)
    n = len(c)
    F = np.full((n, 6), np.nan)
    for i in range(20, n):
        vavg = v[i - 5:i].mean()
        ma20 = c[i - 19:i + 1].mean()
        win = c[i - 19:i + 1]
        ds = 0
        for j in range(i, 0, -1):
            if c[j] < c[j - 1]:
                ds += 1
            else:
                break
        rng = (c[i] - win.min()) / (win.max() - win.min()) if win.max() > win.min() else 0.5
        F[i] = [(c[i] / c[i - 1] - 1) * 100, (c[i] / c[i - 5] - 1) * 100,
                v[i] / vavg if vavg > 0 else 1.0, (c[i] / ma20 - 1) * 100,
                min(ds, 6), rng * 100]
    nxt_ret = np.full(n, np.nan)
    nxt_gap = np.full(n, np.nan)
    nxt_ret[:-1] = (c[1:] / c[:-1] - 1) * 100
    nxt_gap[:-1] = (o[1:] / c[:-1] - 1) * 100
    return F, nxt_ret, nxt_gap


def _analog_predict(F, nxt_ret, nxt_gap, target: int, hist_hi: int) -> dict | None:
    """用历史行 [20, hist_hi) 中与 target 状态最像的 K 个邻居的 T+1 分布预测 target 的 T+1。
    邻居严格早于 hist_hi，预测目标的实现值不参与 → 无前视。"""
    tv = F[target]
    if np.isnan(tv).any():
        return None
    rows = np.arange(20, hist_hi)
    rows = rows[~np.isnan(F[rows]).any(axis=1) & ~np.isnan(nxt_ret[rows])]
    if len(rows) < 30:
        return None
    M = F[rows]
    sd = M.std(axis=0)
    sd[sd == 0] = 1.0
    diff = ((M - tv) / sd) * _FEAT_W
    dist = np.sqrt((diff * diff).sum(axis=1))
    k_neigh = max(20, len(rows) // 12)
    nn = rows[np.argsort(dist)[:k_neigh]]
    r, g = nxt_ret[nn], nxt_gap[nn]
    p_up = float((r > 0.1).mean())
    p_dn = float((r < -0.1).mean())
    return {"p_up": round(p_up, 3), "p_down": round(p_dn, 3),
            "exp_ret": round(float(np.median(r)), 2),
            "gap_lo": round(float(np.percentile(g, 25)), 2),
            "gap_hi": round(float(np.percentile(g, 75)), 2),
            "gap_med": round(float(np.median(g)), 2), "n": int(k_neigh)}


def _analog_backtest(F, nxt_ret, nxt_gap, warmup: int = 120) -> dict:
    """扩张窗口回测：在每个历史日只用更早的数据预测它的 T+1，统计方向命中率 + 跳空 MAE。
    只在模型有明确倾向(|p_up-p_down|≥0.10)时计入命中率，避免拿 50/50 灌水。"""
    n = len(F)
    hits = tot = 0
    gerr = []
    for i in range(warmup, n - 1):
        pr = _analog_predict(F, nxt_ret, nxt_gap, i, i)
        if not pr or np.isnan(nxt_ret[i]):
            continue
        if abs(pr["p_up"] - pr["p_down"]) >= 0.10:
            tot += 1
            hits += int((pr["p_up"] >= pr["p_down"]) == (nxt_ret[i] > 0))
        gerr.append(abs(pr["gap_med"] - nxt_gap[i]))
    return {"hit_rate": round(hits / tot, 3) if tot else None, "n_eval": tot,
            "gap_mae": round(float(np.mean(gerr)), 2) if gerr else None}


def _sentiment_lean(senti: dict | None):
    """情绪延续/退潮 → 对 p_up 的小幅微调(有界)。返回 (label, lean, tags)。"""
    if not senti:
        return "中性(无情绪数据)", 0.0, []
    lean, tags = 0.0, []
    zt, zbr = senti.get("zt_count", 0), senti.get("zb_rate", 0)
    if senti.get("zt_shrink"):
        lean -= 0.06
        tags.append("涨停腰斩→退潮")
    if zbr > 40:
        lean -= 0.05
        tags.append(f"炸板率{zbr}%→分歧")
    elif zbr < 20 and zt >= 40:
        lean += 0.04
        tags.append("低炸板高涨停→延续")
    if senti.get("max_streak", 0) >= 6 and zbr > 35:
        lean -= 0.04
        tags.append("高潮+高炸板→防退潮")
    label = "退潮" if lean <= -0.05 else "延续偏强" if lean >= 0.04 else "中性"
    return label, lean, tags


def forecast(idx: dict[str, dict], senti: dict | None, regime: str) -> dict | None:
    """T+1 前瞻 nowcast（展示 + 背离告警用，不改仓位/打分）。失败返回 None（降级）。"""
    klong = _idx_kline("sh000001", 800)
    if not klong or len(klong) < 150:
        return None
    F, nr, ng = _feat_matrix(klong)
    base = _analog_predict(F, nr, ng, len(klong) - 1, len(klong))
    if not base:
        return None
    bt = _analog_backtest(F, nr, ng)
    slabel, slean, stags = _sentiment_lean(senti)
    p_up = base["p_up"] + slean
    p_dn = base["p_down"] - slean
    s = p_up + p_dn
    if s > 0.98:  # 给"平"留余量，避免显示成 >100%
        p_up, p_dn = p_up * 0.98 / s, p_dn * 0.98 / s
    p_up, p_dn = round(min(0.9, max(0.1, p_up)), 2), round(min(0.9, max(0.1, p_dn)), 2)
    edge = abs(p_up - p_dn)
    hr = bt["hit_rate"]
    if hr is None or hr < 0.52 or edge < 0.10:
        conf = "低(≈随机)"
    elif hr < 0.55:
        conf = "中低"
    elif hr < 0.58:
        conf = "中" if edge < 0.15 else "中高"
    else:
        conf = "中高" if edge >= 0.15 else "中"
    direction = "偏多" if p_up - p_dn >= 0.08 else "偏空" if p_dn - p_up >= 0.08 else "中性"
    # 背离告警：滞后闸门 vs 前瞻方向不一致时点名（只提示、不自动改仓）
    gate_def = ("观望" in regime) or ("防守" in regime)
    diverge = ""
    if conf != "低(≈随机)":
        if gate_def and direction == "偏多":
            diverge = ("⚠背离：滞后闸门偏防守，但前瞻(历史类比+情绪)偏多——闸门是反应型指标，"
                       "底部 V 反前一天最易误杀；是否小仓试探请结合外盘自行定夺，本预测不自动改仓位/打分。")
        elif (not gate_def) and direction == "偏空":
            diverge = "⚠背离：闸门尚友好，但前瞻偏空——次日提防高开走弱/低开，控制追高。"
    drivers = [f"历史类比{base['n']}个相似日：涨{int(base['p_up'] * 100)}%/跌{int(base['p_down'] * 100)}%"
               f"(中位T+1 {base['exp_ret']:+.2f}%)"]
    if stags:
        drivers.append("情绪：" + "，".join(stags))
    return {"direction": direction, "prob_up": p_up, "prob_down": p_dn,
            "exp_gap_range": f"{base['gap_lo']:+.2f}~{base['gap_hi']:+.2f}%",
            "gap_median": base["gap_med"], "sentiment_continuation": slabel,
            "confidence": conf, "hit_rate": hr, "gap_mae": bt["gap_mae"],
            "n_eval": bt["n_eval"], "n_analogs": base["n"], "drivers": drivers,
            "divergence": diverge,
            "note": "外盘(美股/SOX/富时A50/汇率)由 Claude WebSearch 叠加；本块=历史类比+情绪，"
                    "展示+背离告警用，不自动改仓位/综合打分。"}


def main() -> None:
    ap = argparse.ArgumentParser(description="市场环境·情绪闸门（Agent⓪）")
    ap.add_argument("--out", default=r"C:\Trading_analysis\data\market_gate_latest.json")
    args = ap.parse_args()

    idx: dict[str, dict] = {}
    sh_dates: list[str] = []
    for sec, name, _ in INDICES:
        k = _idx_kline(sec)
        if k:
            idx[sec] = {"name": name, **_idx_stats(k)}
            if sec == "sh000001":
                sh_dates = [x["date"] for x in k]
        time.sleep(0.2)
    if "sh000001" not in idx:
        print("⚠ 指数行情拉取失败，无法评估环境。")
        return

    senti = _sentiment(idx["sh000001"]["date"],
                       sh_dates[-2] if len(sh_dates) >= 2 else None)
    verdict = assess(idx, senti)
    fc = forecast(idx, senti, verdict["regime"])

    print(f"\n## 市场环境·情绪闸门  (T={idx['sh000001']['date']})")
    print("| 指数 | 收盘 | T日% | 5日% | MA20上方 | 量比 | 连跌 | 放量下跌 |")
    print("|---|---|---|---|---|---|---|---|")
    for sec, name, _ in INDICES:
        s = idx.get(sec)
        if not s:
            continue
        print(f"| {name} | {s['close']} | {s['chg']} | {s['chg5']} | "
              f"{'✓' if s['above_ma20'] else '✗'} | {s['vol_ratio']} | "
              f"{s['down_streak']} | {'⚠' if s['heavy_sell'] else '-'} |")
    if senti:
        prev = f"(前日{senti['zt_prev']}家)" if "zt_prev" in senti else ""
        print(f"\n情绪：涨停 {senti['zt_count']}家{prev} · 最高连板 {senti['max_streak']} · "
              f"炸板率 {senti['zb_rate']}% · 跌停 {senti['dt_count']}家")
    else:
        print("\n情绪：⚠ 涨停池接口不可用，本次只按指数评估（环境分可信度降一档）")
    print(f"\n**环境分 {verdict['score']} → {verdict['regime']} · "
          f"总仓上限 {verdict['max_total_position_pct']}%**")
    for r in verdict["reasons"]:
        print(f"- ⚠ {r}")
    print(f"\n预案：{verdict['plan']}")

    if fc:
        print(f"\n🔮 **T+1 前瞻（展示用·不改仓位/打分）：{fc['direction']}** · "
              f"P涨 {fc['prob_up']}/P跌 {fc['prob_down']} · 预计跳空 {fc['exp_gap_range']} · "
              f"情绪{fc['sentiment_continuation']} · 置信度 {fc['confidence']}")
        print(f"   回测命中率 {fc['hit_rate']} · 跳空MAE {fc['gap_mae']}% · 评估样本 {fc['n_eval']}（扩张窗口·无前视）")
        for d in fc["drivers"]:
            print(f"   - {d}")
        if fc["divergence"]:
            print(f"   {fc['divergence']}")
    else:
        print("\n🔮 T+1 前瞻：指数长历史拉取不足，本次跳过前瞻（不影响环境闸门）。")

    # ---- P12 隔夜/外盘（引擎直拉，报价自带北京时间戳；LLM禁止再按本机日期拼搜索词）----
    ov = fetch_overnight()
    if ov:
        print("\n### 🌍 隔夜/外盘（新浪直拉 · 报价时间=北京时间 · 与本机时钟无关）")
        print("| 市场 | 最新 | 涨跌% | 报价时间(北京) | 距今 |")
        print("|---|---|---|---|---|")
        for o in ov:
            print(f"| {o['name']} | {o['price']} | {o['pct'] if o['pct'] is not None else '-'} | "
                  f"{o['quote_time_cn']} | {o['age_h']}h |")
        print("> 指数类外盘以上表为准（时间戳新鲜度自查）；WebSearch 仅补政策/新闻/个股类信息，"
              "**禁止在搜索词里拼日期**（本机时区不可信，2026-07-07 拼出'7月3日'查到美股休市日的教训）。")
    else:
        print("\n🌍 隔夜/外盘：新浪接口不可用，请 WebSearch 补外盘（用'最新/收盘'措辞，勿拼日期，"
              "并核对结果里的日期与时间戳）。")

    cn_now = eng._cn_now()
    wd, hm = cn_now.weekday(), cn_now.strftime("%H:%M")
    in_session = wd < 5 and "09:15" <= hm <= "15:05"
    if in_session:
        print(f"\n注：当前北京时间 {cn_now.strftime('%Y-%m-%d %H:%M')}（A股交易时段）——最后一根K线为盘中"
              "未完成数据，量比/涨停家数是盘中值会偏低，环境分仅供盘中参考；收盘后/盘前跑最准。")
    else:
        print(f"\n注：当前北京时间 {cn_now.strftime('%Y-%m-%d %H:%M %a')}（盘前/盘后/休市），"
              "数据为最近完整交易日口径。")

    out = {"generated_at": cn_now.strftime("%Y-%m-%d %H:%M:%S") + " (北京时间)",
           "cn_time": cn_now.isoformat(timespec="seconds"),
           "in_session": in_session,
           "indices": idx, "sentiment": senti, **verdict,
           "overnight": ov,
           "t1_forecast": fc, "degraded": senti is None}
    p = pathlib.Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
