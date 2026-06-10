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
    print("\n注：若在A股交易时段(9:30-15:00)运行，最后一根K线为盘中数据——"
          "量比/涨停家数是盘中值会偏低，环境分仅供盘中参考；收盘后/盘前跑最准。")

    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "indices": idx, "sentiment": senti, **verdict,
           "degraded": senti is None}
    p = pathlib.Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {p}")


if __name__ == "__main__":
    main()
