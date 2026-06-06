#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T+1 盘前/开盘复核：读上次选股结果，拉实时价，算相对 T 收盘的跳空，给可买/等回调/放弃建议。

用法（T+1 早上 9:15–9:30 跑）:
  python recheck.py                       # 读默认 rank_latest.json
  python recheck.py --in C:/.../rank.json
规则：
  - 高开 > +3%        → 等回调（追高摊薄盈亏比；冲高回落风险）
  - 现价 ≤ ATR止损位   → 放弃（已破位）
  - 低开 −2%~0 且未破位 → 较好低吸点
  - 其余               → 可按计划买入
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eng", HERE / "ashare_weekly_rank.py")
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)


def main() -> None:
    ap = argparse.ArgumentParser(description="T+1 盘前复核：跳空 + 入场建议")
    ap.add_argument("--in", dest="inp",
                    default=r"C:\Trading_analysis\data\rank_latest.json", help="选股结果JSON")
    args = ap.parse_args()

    p = pathlib.Path(args.inp)
    if not p.exists():
        print(f"找不到结果文件: {p}（先跑一次选股并 --out 到这里）")
        return
    res = json.loads(p.read_text(encoding="utf-8"))
    cands = res.get("candidates", [])
    if not cands:
        print("结果文件无候选。")
        return

    codes = [c["code"] for c in cands]
    quotes = eng._tx_quote(codes)  # 实时价（盘前为昨收/集合竞价参考价）

    print(f"\n## T+1 盘前复核  (T={res.get('as_of','?')}  买入日={res.get('buy_date','?')}  "
          f"持有≤{res.get('hold_days','?')}日)")
    print("| 代码 | 名称 | T收盘 | 现价 | 跳空% | ATR止损 | 距止损% | 建议 |")
    print("|---|---|---|---|---|---|---|---|")
    for c in cands:
        q = quotes.get(c["code"], {})
        cur = q.get("price")
        t_close = c.get("close")
        atr = c.get("atr14") or 0
        # 优先用引擎/Agent③ 已写入JSON的止损(含-6%封顶/人工改写)，保持全流程一致；
        # 缺失才回退到 ATR 公式
        stop = c.get("stop")
        if stop is None and t_close:
            stop = round(t_close - 1.3 * atr, 2)
        if not cur or not t_close:
            print(f"| {c['code']} | {c['name']} | {t_close} | - | - | {stop} | - | 无实时报价 |")
            continue
        gap = (cur / t_close - 1) * 100
        dist_stop = (cur / stop - 1) * 100 if stop else None
        if stop and cur <= stop:
            advice = "放弃(已破位)"
        elif gap > 3:
            advice = "等回调(高开过多)"
        elif gap > 1.5:
            advice = "谨慎/小仓"
        elif -2 <= gap <= 0:
            advice = "★低吸好点"
        else:
            advice = "可按计划买入"
        print(f"| {c['code']} | {c['name']} | {t_close} | {round(cur,2)} | {round(gap,2)} | "
              f"{stop} | {round(dist_stop,1) if dist_stop is not None else '-'} | {advice} |")
    print("\n注：盘前现价为集合竞价参考，9:25 后更可靠；ATR止损为引擎参考位，实际以 Agent③ 给的止损为准。")


if __name__ == "__main__":
    main()
