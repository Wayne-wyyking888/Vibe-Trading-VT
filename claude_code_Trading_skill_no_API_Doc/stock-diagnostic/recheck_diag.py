#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次日盘前/盘中快速复核：读上次诊断结果，拉实时价，对照成本与价位地图给即时建议。

用法（买入/加仓当天早上 9:15–9:30 或盘中跑）:
  python recheck_diag.py                       # 读默认 diag_latest.json
  python recheck_diag.py --in C:/.../diag.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("diag", HERE / "stock_diagnostic.py")
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)
eng = diag.eng


def main() -> None:
    ap = argparse.ArgumentParser(description="个股诊断盘前复核")
    ap.add_argument("--in", dest="inp",
                    default=r"C:\Trading_analysis\data\diag_latest.json", help="诊断结果JSON")
    args = ap.parse_args()

    p = pathlib.Path(args.inp)
    if not p.exists():
        print(f"找不到诊断文件: {p}（先跑一次 stock_diagnostic.py --out 到这里）")
        return
    r = json.loads(p.read_text(encoding="utf-8"))
    code = r["code"]
    q = eng._tx_quote([code]).get(code, {})
    cur = q.get("price")
    t_close = r["technical"]["close"]
    pm = r["price_map"]
    ca = r.get("cost_analysis")
    stop = pm["stop"]

    print(f"\n## 个股复核 — {r['name']}({code})  T收盘={t_close}")
    if not cur:
        print("拉不到实时价（盘前可能未开盘）。价位地图：")
    else:
        gap = (cur / t_close - 1) * 100
        print(f"现价≈{round(cur,2)}  相对T收盘 {gap:+.2f}%")
        if ca:
            pnl_now = (cur / ca["cost"] - 1) * 100
            print(f"相对成本 {ca['cost']}: {pnl_now:+.2f}%（{'浮盈' if pnl_now>=0 else '浮亏'}）")
        # 即时建议
        if cur <= stop:
            advice = "⚠ 已跌破止损位，按纪律止损/减仓"
        elif cur <= pm["add_zone"][1] and cur >= pm["add_zone"][0]:
            advice = "进入加仓/补仓区（须趋势条件满足，详见裁决）"
        elif cur >= pm["take_profit"][0]:
            advice = "进入止盈区，考虑减仓锁利"
        else:
            advice = "在支撑~压力之间，按裁决持有/观察"
        print(f"即时建议：{advice}")

    print(f"\n价位地图：止损 {stop} | 关键支撑 {pm['key_support']} | 加仓区 {pm['add_zone'][0]}–{pm['add_zone'][1]} "
          f"| MA20 {pm['ma20']} | 压力 {pm['key_resist']} | 止盈区 {pm['take_profit'][0]}–{pm['take_profit'][1]}")
    if r.get("final_action"):
        print(f"上次裁决动作：{r['final_action']}（{r.get('final_confidence','')}）")
    print("\n注：盘前现价为集合竞价参考，9:25 后更可靠；最终以诊断裁决的纪律为准。")


if __name__ == "__main__":
    main()
