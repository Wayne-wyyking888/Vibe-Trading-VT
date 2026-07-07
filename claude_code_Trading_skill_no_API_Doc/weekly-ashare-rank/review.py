#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动复盘（复盘模式引擎）：把上次选股的"计划"对照其后真实行情做规则仿真，算每只票战绩。

为什么需要：选股 skill 出表后没有闭环——不知道推荐到底赚没赚、止损止盈有没有被触发、
赢家输家各有什么特征。本脚本按报告自己的纪律（买入区间/高开放弃/止损/目标/T+1次日才能卖/
最晚 sell_by 离场）仿真每只票，输出战绩表并累计到 track_record.json，形成可统计的真实样本。

用法（任意时间跑，通常买入日之后 1~N 天）:
  python review.py                                 # 读默认 rank_latest.json
  python review.py --in C:/.../rank_xxx.json       # 复盘任一期历史结果
仿真纪律（与 SKILL/报告一致，保守口径）:
  - 竞价高开 > 放弃阈值(从 abort 文本解析，默认3%) → 放弃不买
  - 开盘价落在买入区间 → 开盘成交；高于区间 → 等回落到区间上沿成交（窗口内任一天）；
    低于区间下沿但高于止损 → 更优价按开盘成交；开盘已 ≤ 止损 → 放弃(竞价破位)
  - 入场当日不可卖(A股T+1)；次日起 最低≤止损 → 止损价出(跳空低开穿越按开盘价)，
    最高≥目标 → 目标价出；同日双触保守按止损算
  - 到 sell_by 收盘仍未触发 → 按收盘离场；数据不足(还没到期) → 标记"持仓中"给浮动盈亏
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import time

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eng", HERE / "ashare_weekly_rank.py")
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)

BENCH = "sh000001"


def _fresh_kline(code: str) -> list[dict] | None:
    """绕过引擎缓存拉最新日线（复盘必须包含选股之后的新K线）。"""
    for fn in (eng._em_kline, eng._tx_kline):
        df = fn(code, 90)
        if df is not None and len(df) >= 5:
            rows = df.to_dict("records")
            return [{"date": str(r["日期"]), "o": float(r["开盘"]), "c": float(r["收盘"]),
                     "h": float(r["最高"]), "l": float(r["最低"])} for r in rows]
    return None


def _bench_kline() -> list[dict] | None:
    try:
        r = eng._SESSION.get(eng._TX_KLINE, params={"param": f"{BENCH},day,,,60,qfq"}, timeout=15)
        node = r.json().get("data", {}).get(BENCH, {}) or {}
        kl = node.get("qfqday") or node.get("day") or []
    except Exception:  # noqa: BLE001
        return None
    return [{"date": x[0], "o": float(x[1]), "c": float(x[2])} for x in kl] or None


def _category(c: dict) -> str:
    txt = f"{c.get('exp_return', '')}{c.get('confidence', '')}{c.get('verify_mark', '')}"
    if "剔除" in txt:
        return "剔除"
    if "观察" in txt:
        return "观察"
    return "建仓"


def _abort_gap(c: dict) -> float:
    m = re.search(r"高开\s*[>＞]\s*(\d+(?:\.\d+)?)\s*%", str(c.get("abort", "")))
    return float(m.group(1)) if m else 3.0


def simulate(c: dict, days: list[dict], sell_by: str) -> dict:
    """按计划纪律仿真一只票。days = buy_date 起的日线。"""
    buy_lo, buy_hi = c.get("buy_low"), c.get("buy_high")
    stop, target, t_close = c.get("stop"), c.get("target"), c.get("close")
    if not (buy_lo and buy_hi and stop and t_close):
        return {"status": "数据不全"}
    d0 = days[0]
    gap = (d0["o"] / t_close - 1) * 100
    if gap > _abort_gap(c):
        return {"status": f"放弃(高开{gap:+.1f}%)", "gap": round(gap, 2)}
    if d0["o"] <= stop:
        return {"status": f"放弃(竞价{d0['o']}已破止损{stop})", "gap": round(gap, 2)}

    entry = entry_date = None
    entry_i = 0
    for i, d in enumerate(days):
        if d["date"] > sell_by:
            break
        if i == 0:
            if d["o"] <= buy_hi:           # 区间内或更优价（已排除破止损）
                entry, entry_date, entry_i = d["o"], d["date"], i
                break
            if d["l"] <= buy_hi:           # 高于区间开盘，盘中回落到区间上沿
                entry, entry_date, entry_i = buy_hi, d["date"], i
                break
        else:                              # 等回调型：窗口内后续日回落到区间
            if d["o"] <= stop:
                return {"status": "未成交(等回调期间已破止损,放弃)"}
            if d["l"] <= buy_hi:
                entry, entry_date, entry_i = min(d["o"], buy_hi), d["date"], i
                break
    if entry is None:
        return {"status": "未成交(窗口内未回落到买入区)"}

    mfe = 0.0  # 最高浮盈%
    for d in days[entry_i:]:
        if d["date"] > sell_by:
            break
        mfe = max(mfe, (d["h"] / entry - 1) * 100)
        if d["date"] == entry_date:        # T+1：入场当日不可卖
            continue
        if d["l"] <= stop:
            px = min(d["o"], stop)         # 跳空穿越按开盘
            return {"status": "止损", "entry": entry, "entry_date": entry_date,
                    "exit": px, "exit_date": d["date"],
                    "ret": round((px / entry - 1) * 100, 2), "mfe": round(mfe, 1)}
        if target and d["h"] >= target:
            px = max(d["o"], target)
            return {"status": "止盈(目标)", "entry": entry, "entry_date": entry_date,
                    "exit": px, "exit_date": d["date"],
                    "ret": round((px / entry - 1) * 100, 2), "mfe": round(mfe, 1)}
        if d["date"] >= sell_by:
            return {"status": "到期离场", "entry": entry, "entry_date": entry_date,
                    "exit": d["c"], "exit_date": d["date"],
                    "ret": round((d["c"] / entry - 1) * 100, 2), "mfe": round(mfe, 1)}
    last = days[-1]
    note = ""
    if target and last["c"] >= target * 0.985:
        note = "⚠已近/到目标价,提醒分批止盈"
    elif last["c"] <= stop * 1.015:
        note = "⚠逼近止损,次日跌破必须出"
    return {"status": "持仓中", "entry": entry, "entry_date": entry_date,
            "exit": last["c"], "exit_date": last["date"] + "(最新)",
            "ret": round((last["c"] / entry - 1) * 100, 2), "mfe": round(mfe, 1),
            "note": note}


def main() -> None:
    ap = argparse.ArgumentParser(description="选股结果自动复盘 → 战绩 + track record")
    ap.add_argument("--in", dest="inp",
                    default=r"C:\Trading_analysis\data\rank_latest.json")
    ap.add_argument("--track", default=r"C:\Trading_analysis\data\track_record.json")
    args = ap.parse_args()

    p = pathlib.Path(args.inp)
    if not p.exists():
        print(f"找不到结果文件: {p}")
        return
    res = json.loads(p.read_text(encoding="utf-8"))
    cands = res.get("candidates", [])
    buy_date, sell_by = res.get("buy_date", ""), res.get("sell_by", "9999-12-31")
    if not cands or not buy_date:
        print("结果文件无候选或缺 buy_date。")
        return

    bench = _bench_kline() or []
    bwin = [b for b in bench if buy_date <= b["date"] <= sell_by]
    bench_ret = round((bwin[-1]["c"] / bwin[0]["o"] - 1) * 100, 2) if len(bwin) >= 1 else None

    print(f"\n## 选股复盘  T={res.get('as_of')}  买入日={buy_date}  最晚卖出={sell_by}  "
          f"(基准沪指同窗口 {bench_ret:+.2f}% )" if bench_ret is not None else
          f"\n## 选股复盘  T={res.get('as_of')}  买入日={buy_date}  最晚卖出={sell_by}")
    print("| 类别 | 代码 | 名称 | 计划买入区 | 止损/目标 | 实际入场 | 离场 | 收益% | 最高浮盈% | 状态 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    rows, rets_closed, rets_all = [], [], []
    for c in cands:
        cat = _category(c)
        days = None
        k = _fresh_kline(c["code"])
        if k:
            days = [d for d in k if d["date"] >= buy_date]
        if not days:
            r = {"status": "无行情"}
        else:
            r = simulate(c, days, sell_by)
        if cat == "建仓" and "ret" in r:
            rets_all.append(r["ret"])
            if r["status"] != "持仓中":
                rets_closed.append(r["ret"])
        rows.append({"code": c["code"], "name": c["name"], "category": cat, **r})
        print(f"| {cat} | {c['code']} | {c['name']} | {c.get('buy_zone','-')} | "
              f"{c.get('stop','-')}/{c.get('target','-')} | "
              f"{r.get('entry','-')}@{r.get('entry_date','-')} | "
              f"{r.get('exit','-')}@{r.get('exit_date','-')} | "
              f"{r.get('ret','-')} | {r.get('mfe','-')} | "
              f"{r['status']}{(' ' + r.get('note','')) if r.get('note') else ''} |")
        time.sleep(0.2)

    summary = {}
    if rets_all:
        wins = sum(1 for x in rets_all if x > 0)
        summary = {"n_buy": len(rets_all), "win_rate": round(wins / len(rets_all) * 100, 1),
                   "avg_ret": round(sum(rets_all) / len(rets_all), 2),
                   "bench_ret": bench_ret,
                   "excess": (round(sum(rets_all) / len(rets_all) - bench_ret, 2)
                              if bench_ret is not None else None)}
        print(f"\n**建仓票战绩**：{summary['n_buy']}只 · 胜率 {summary['win_rate']}% · "
              f"平均 {summary['avg_ret']:+.2f}%"
              + (f" · 同期沪指 {bench_ret:+.2f}% · 超额 {summary['excess']:+.2f}%"
                 if bench_ret is not None else ""))
        if rets_closed and len(rets_closed) < len(rets_all):
            print(f"（其中已了结 {len(rets_closed)} 只，其余为持仓浮动盈亏）")

    # 累计 track record（同一期 generated_at 重跑则覆盖）
    tp = pathlib.Path(args.track)
    track = []
    if tp.exists():
        try:
            track = json.loads(tp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            track = []
    key = res.get("generated_at") or res.get("as_of")
    track = [t for t in track if t.get("key") != key]
    track.append({"key": key, "as_of": res.get("as_of"), "buy_date": buy_date,
                  "sell_by": sell_by, "hold_days": res.get("hold_days"),
                  "reviewed_at": eng._cn_now().strftime("%Y-%m-%d %H:%M:%S") + " (北京时间)",
                  "summary": summary, "picks": rows})
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(json.dumps(track, ensure_ascii=False, indent=1), encoding="utf-8")
    n_runs = len(track)
    alls = [t["summary"].get("avg_ret") for t in track if t.get("summary", {}).get("avg_ret") is not None]
    if alls:
        print(f"\n累计 track record：{n_runs} 期 · 期均收益 {sum(alls)/len(alls):+.2f}% → 已写 {tp}")
    print("\n注：仿真为保守口径(同日双触按止损、跳空按开盘)，与实盘成交会有出入；"
          "「持仓中」按最新价算浮动盈亏。")


if __name__ == "__main__":
    main()
