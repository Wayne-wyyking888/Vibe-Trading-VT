#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weekly A-share quant screener — Agent 1 engine for the weekly-ashare-rank skill.

拉取沪深A股实时行情 + 历史K线（akshare，免费无需API key），计算短线
动量/量能/技术因子，剔除 ST/次新/低流动性，输出按综合因子打分排序的候选股。

这是纯数据、确定性的引擎。Claude Code 在此输出之上扮演：
  - Agent 2 catalyst_analyst：WebSearch 查催化剂/政策/资金面
  - Agent 3 risk_challenger：综合打分、风险挑战、给入场/目标/止损价

用法:
  python ashare_weekly_rank.py --top 15 --pool 40
  python ashare_weekly_rank.py --sector 煤炭行业 --top 8
  python ashare_weekly_rank.py --out C:/Trading_analysis/data/rank.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

try:
    import akshare as ak
except ImportError:
    sys.stderr.write("需要 akshare: pip install akshare\n")
    sys.exit(1)


def log(msg: str) -> None:
    """进度信息打到 stderr，stdout 只留最终 JSON/表格。"""
    sys.stderr.write(f"[engine] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 数据获取

def get_spot() -> pd.DataFrame:
    """全市场A股实时快照。列为中文：代码 名称 最新价 涨跌幅 换手率 量比 市盈率-动态
    市净率 总市值 流通市值 成交量 成交额 ..."""
    log("拉取全市场实时行情 stock_zh_a_spot_em() ...")
    df = ak.stock_zh_a_spot_em()
    log(f"  收到 {len(df)} 行")
    return df


def restrict_to_sector(df: pd.DataFrame, sector: str) -> pd.DataFrame:
    """按东方财富行业板块/概念板块名缩小universe。best-effort，失败则返回原表。"""
    codes = None
    for fn, label in (
        (ak.stock_board_industry_cons_em, "行业板块"),
        (ak.stock_board_concept_cons_em, "概念板块"),
    ):
        try:
            cons = fn(symbol=sector)
            if cons is not None and len(cons) > 0:
                codes = set(cons["代码"].astype(str))
                log(f"  {label}『{sector}』成分股 {len(codes)} 只")
                break
        except Exception as e:  # noqa: BLE001
            log(f"  {label}『{sector}』查询失败: {e}")
    if not codes:
        log(f"  ⚠ 未能解析板块『{sector}』，回退全市场")
        return df
    return df[df["代码"].astype(str).isin(codes)]


# ---------------------------------------------------------------- 初筛

def prefilter(
    df: pd.DataFrame,
    min_float_cap: float = 15e8,
    min_amount: float = 1e8,
    max_chg: float = 9.0,
    min_chg: float = -7.0,
) -> pd.DataFrame:
    """剔除 ST/退市/北交所/低流动性/今日涨跌停附近的股票。"""
    out = df.copy()
    n0 = len(out)

    out = out[~out["名称"].astype(str).str.contains("ST|退", case=False, na=False)]
    n1 = len(out)

    code = out["代码"].astype(str)
    out = out[code.str.match(r"^(60|00|30|68)")]  # 沪市主板/科创板 + 深市主板/创业板
    n2 = len(out)

    fcap = pd.to_numeric(out["流通市值"], errors="coerce")
    out = out[fcap >= min_float_cap]
    n3 = len(out)

    amt = pd.to_numeric(out["成交额"], errors="coerce")
    out = out[amt >= min_amount]
    n4 = len(out)

    chg = pd.to_numeric(out["涨跌幅"], errors="coerce")
    out = out[(chg < max_chg) & (chg > min_chg)]
    n5 = len(out)

    lb = pd.to_numeric(out["量比"], errors="coerce")
    out = out[lb >= 1.0]
    n6 = len(out)

    log(
        "初筛漏斗: "
        f"{n0} →ST {n1} →板块 {n2} →流通市值≥{min_float_cap/1e8:.0f}亿 {n3} "
        f"→成交额≥{min_amount/1e8:.1f}亿 {n4} →非涨跌停 {n5} →量比≥1 {n6}"
    )
    return out


def prescore(df: pd.DataFrame) -> pd.DataFrame:
    """用快照里的 量比/换手率/涨幅 做一个粗排，挑出值得拉历史K线的候选。"""
    d = df.copy()
    d["量比"] = pd.to_numeric(d["量比"], errors="coerce")
    d["换手率"] = pd.to_numeric(d["换手率"], errors="coerce")
    d["涨跌幅"] = pd.to_numeric(d["涨跌幅"], errors="coerce")

    turn = d["换手率"].clip(0, 30)
    turn_score = 1 - (turn - 8).abs() / 22  # 换手率甜区~8%
    d["prescore"] = (
        d["量比"].rank(pct=True) * 0.5
        + turn_score * 0.3
        + d["涨跌幅"].clip(0, 9).rank(pct=True) * 0.2
    )
    return d.sort_values("prescore", ascending=False)


# ---------------------------------------------------------------- 历史因子

def hist_factors(code: str, name: str) -> dict | None:
    """拉~120天前复权日线，算 MA排列/动量/量比/ATR/MACD/区间位置。"""
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=160)).strftime("%Y%m%d")
    try:
        h = ak.stock_zh_a_hist(
            symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
        )
    except Exception as e:  # noqa: BLE001
        log(f"  {code} {name} 历史拉取失败: {e}")
        return None
    if h is None or len(h) < 25:
        return None

    h = h.rename(
        columns={"收盘": "close", "最高": "high", "最低": "low", "开盘": "open", "成交量": "vol"}
    ).sort_values("日期")
    close = h["close"].astype(float)
    high = h["high"].astype(float)
    low = h["low"].astype(float)
    vol = h["vol"].astype(float)

    ma5, ma10, ma20 = (close.rolling(w).mean() for w in (5, 10, 20))
    last = float(close.iloc[-1])
    bull = bool(ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1])
    ret5 = last / float(close.iloc[-6]) - 1 if len(close) > 6 else np.nan
    ret20 = last / float(close.iloc[-21]) - 1 if len(close) > 21 else np.nan
    vr = (
        float(vol.iloc[-5:].mean()) / float(vol.iloc[-20:].mean())
        if len(vol) >= 20 and vol.iloc[-20:].mean() > 0
        else np.nan
    )

    prev_close = close.shift()
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    dist_ma10 = last / float(ma10.iloc[-1]) - 1
    win = min(60, len(close))
    lo, hi = float(low.iloc[-win:].min()), float(high.iloc[-win:].max())
    rng_pos = (last - lo) / (hi - lo) if hi > lo else 0.5

    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    macd_gold = bool(dif.iloc[-1] > dea.iloc[-1])

    return dict(
        code=code, name=name, close=round(last, 2), bull=bull,
        ret5=round(ret5 * 100, 2), ret20=round(ret20 * 100, 2),
        vr=round(vr, 2) if vr == vr else None,
        atr14=round(atr14, 3), atr_pct=round(atr14 / last * 100, 2),
        dist_ma10=round(dist_ma10 * 100, 2), rng_pos=round(rng_pos * 100, 1),
        macd_gold=macd_gold, ma5=round(float(ma5.iloc[-1]), 2),
        ma10=round(float(ma10.iloc[-1]), 2), ma20=round(float(ma20.iloc[-1]), 2),
    )


def composite(f: dict) -> dict:
    """综合因子打分: 动量40 + 量能30 + 技术30 - 过热惩罚。"""
    ret5 = f["ret5"] if f["ret5"] == f["ret5"] else 0
    ret20 = f["ret20"] if f["ret20"] == f["ret20"] else 0
    vr = f["vr"] if f["vr"] else 1.0

    mom = min(max(ret5, -5), 10) / 10 * 20 + min(max(ret20, -10), 20) / 20 * 20
    vol = min(max(vr - 1, 0), 1.0) * 20 + (10 if 1.2 <= vr <= 2.5 else 0)
    tech = (
        (15 if f["bull"] else 0)
        + (8 if f["macd_gold"] else 0)
        + (7 if -2 <= f["dist_ma10"] <= 6 else 0)
    )
    pen = (10 if f["dist_ma10"] > 12 else 0) + (8 if f["rng_pos"] > 92 else 0)

    score = max(0.0, min(100.0, mom + vol + tech - pen))
    f.update(
        score=round(score, 1), mom=round(mom, 1),
        vol_score=round(vol, 1), tech=round(tech, 1), penalty=pen,
    )
    return f


# ---------------------------------------------------------------- 主流程

def run(sector: str | None, pool: int, top: int, out_path: str | None) -> dict:
    spot = get_spot()
    if sector and sector not in ("全市场", "all", "ALL"):
        spot = restrict_to_sector(spot, sector)

    filt = prefilter(spot)
    if filt.empty:
        log("初筛后无候选，放宽流动性阈值重试")
        filt = prefilter(spot, min_float_cap=8e8, min_amount=5e7)
    ranked_pre = prescore(filt).head(pool)
    log(f"进入历史因子计算的候选: {len(ranked_pre)} 只")

    rows = []
    for i, (_, r) in enumerate(ranked_pre.iterrows(), 1):
        code, name = str(r["代码"]), str(r["名称"])
        f = hist_factors(code, name)
        if f is None:
            continue
        f = composite(f)
        # 带上快照面的字段供下游 agent 参考
        f["chg_today"] = float(pd.to_numeric(r.get("涨跌幅"), errors="coerce"))
        f["turnover"] = float(pd.to_numeric(r.get("换手率"), errors="coerce"))
        f["amount_yi"] = round(float(pd.to_numeric(r.get("成交额"), errors="coerce")) / 1e8, 2)
        f["float_cap_yi"] = round(float(pd.to_numeric(r.get("流通市值"), errors="coerce")) / 1e8, 1)
        rows.append(f)
        if i % 10 == 0:
            log(f"  已处理 {i}/{len(ranked_pre)}")

    rows.sort(key=lambda x: x["score"], reverse=True)
    top_rows = rows[:top]

    result = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "sector": sector or "全市场",
        "universe_after_filter": len(filt),
        "scored": len(rows),
        "candidates": top_rows,
    }

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        log(f"已写出 JSON: {out_path}")

    return result


def print_table(result: dict) -> None:
    print(f"\n## Agent 1 量化筛选结果 — {result['sector']}  ({result['generated_at']})")
    print(f"初筛后universe={result['universe_after_filter']}  打分={result['scored']}  输出Top {len(result['candidates'])}\n")
    hdr = ["代码", "名称", "综合分", "动量", "量能", "技术", "收盘", "今涨%", "5日%", "20日%", "量比", "ATR%", "距MA10%", "60日位%", "多头", "MACD金叉"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "|".join(["---"] * len(hdr)) + "|")
    for c in result["candidates"]:
        print(
            f"| {c['code']} | {c['name']} | {c['score']} | {c['mom']} | {c['vol_score']} | "
            f"{c['tech']} | {c['close']} | {c.get('chg_today','')} | {c['ret5']} | {c['ret20']} | "
            f"{c.get('vr','')} | {c.get('atr_pct','')} | {c['dist_ma10']} | {c['rng_pos']} | "
            f"{'是' if c['bull'] else '否'} | {'是' if c['macd_gold'] else '否'} |"
        )
    print("\n候选股代码（供 Agent 2 查催化剂）:", ", ".join(c["code"] for c in result["candidates"]))


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly A-share quant screener (Agent 1 engine)")
    ap.add_argument("--sector", default=None, help="行业/概念板块名，如 煤炭行业；省略=全市场")
    ap.add_argument("--pool", type=int, default=40, help="进入历史因子计算的候选数（越大越慢）")
    ap.add_argument("--top", type=int, default=15, help="最终输出候选数")
    ap.add_argument("--out", default=None, help="把结果 JSON 写到此路径")
    args = ap.parse_args()

    result = run(args.sector, args.pool, args.top, args.out)
    print_table(result)


if __name__ == "__main__":
    main()
