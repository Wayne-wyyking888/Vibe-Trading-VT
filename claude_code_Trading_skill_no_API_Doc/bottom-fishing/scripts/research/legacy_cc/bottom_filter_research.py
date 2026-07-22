# -*- coding: utf-8 -*-
"""抄底skill候选filter面板研究(2024-01 → 2026-07, 只读引擎不改规则)
- 复现引擎双路径推荐线(防守日总分>=18 ∥ 非防守个股分>=15, ATR<=4)为基线
- 每笔信号: T+1开盘进场, 赛跑口径(先到+5%/+10% vs 先砸-8%)按 5/20/30/60日窗
- 候选filter(全部OHLCV可算): 个股MA250上方/MA60向上/企稳天数/RSI背离代理/缩量放量/
  量波动/dd250/低价股/进场跳空/旋转门重复过线/def_days>=9/idx_rsv带
偏差声明: 股票池=今日成交额前600(幸存者), qfq, 分数权重拟合窗=2025-26(2024为窗外真OOS)
"""
import json
import pickle
import pathlib
import sys
import time

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ENG = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\bottom-fishing")
sys.path.insert(0, str(ENG))
sys.path.insert(0, str(ENG.parent / "weekly-ashare-rank"))
import bottom_fishing as BF  # noqa: E402  只复用 tx_kline(多域) 与权重, 不改任何东西
import ashare_weekly_rank as WK  # noqa: E402

W = BF.W
BARS = 900
START = "2024-01-01"
WINDOWS = (5, 20, 30, 60)
CACHE = HERE / "kline_cache_900.pkl"
OUT = HERE / "signals_2024plus.csv"


def idx_series() -> pd.DataFrame:
    df = BF._drop_intraday(BF.tx_kline("sz399006", BARS))
    df["ma20"] = df.c.rolling(20).mean()
    df["i5"] = df.c.pct_change(5)
    df["defensive"] = (df.c < df.ma20) | (df.i5 < -0.02)
    cnt, dd = 0, []
    for v in df.defensive:
        cnt = cnt + 1 if v else 0
        dd.append(cnt)
    df["def_days"] = dd
    lo14, hi14 = df.l.rolling(14).min(), df.h.rolling(14).max()
    df["idx_rsv"] = (df.c - lo14) / (hi14 - lo14 + 1e-9) * 100
    return df.set_index("d")[["defensive", "def_days", "idx_rsv"]]


def stock_signals(df: pd.DataFrame, idx: pd.DataFrame, code: str, name: str) -> list[dict]:
    s = df.reset_index(drop=True)
    n = len(s)
    if n < 130:
        return []
    c, o, h, l, v = s.c, s.o, s.h, s.l, s.v
    hi60, lo60 = h.rolling(60).max(), l.rolling(60).min()
    dd60 = (c / hi60 - 1) * 100
    pos60 = (c - lo60) / (hi60 - lo60 + 1e-9) * 100
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    ma60, ma250 = c.rolling(60).mean(), c.rolling(250).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / c * 100
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    rsv = (c - lo14) / (hi14 - lo14 + 1e-9) * 100
    ret = c.pct_change()
    neg = ret < 0
    downstk = neg.groupby((~neg).cumsum()).cumsum()
    zt20 = ret.rolling(20).max() >= 0.093
    is_low = l <= lo60 * 1.001
    pos_idx = np.arange(n, dtype=float)
    last_low = pd.Series(np.where(is_low, pos_idx, np.nan)).ffill()
    days_since_low = pos_idx - last_low.values
    # RSI14 (Wilder简化: SMA版) + 10日背离代理: 价创10日新低且RSI高于10日前
    up = ret.clip(lower=0).rolling(14).mean()
    dn = (-ret.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + up / (dn + 1e-12))
    rsi_div = (c <= c.rolling(10).min() * 1.001) & (rsi > rsi.shift(10))
    vma20 = v.rolling(20).mean()
    volx = v / vma20
    vstd20 = v.rolling(20).std() / (vma20 + 1e-9)
    dd250 = (c / h.rolling(250).max() - 1) * 100
    dmap_def = idx["defensive"]
    dmap_dd = idx["def_days"]
    dmap_rsv = idx["idx_rsv"]
    sigs, last_q = [], -99
    for t in range(65, n - 1):   # 腾讯个股上限640根(起点~2023-10), 预热65根→信号自2024-02起; ma250/dd250早期为None
        d = s.d[t]
        if d < START or d not in dmap_def.index:
            continue
        if not (dd60[t] <= -20 and pos60[t] <= 25):
            continue
        defensive = bool(dmap_def[d])
        hits = dict(defensive=defensive, above_ma10=c[t] > ma10[t],
                    dif_up=(dif[t] - dif[t - 3]) > 0, rsv_recover=20 < rsv[t] <= 40,
                    dd_sweet=-45 < dd60[t] <= -30, above_ma5=c[t] > ma5[t],
                    gap_reclaim=(o[t] < c[t - 1] * 0.98 and c[t] > o[t]),
                    rsv_deep=rsv[t] <= 15, downstk4=downstk[t] >= 4,
                    zt20=bool(zt20[t]), atr_hi=atr[t] >= 7,
                    fresh_low=(not np.isnan(days_since_low[t]) and days_since_low[t] <= 1))
        score = sum(W[k] for k, v_ in hits.items() if v_)
        stock_score = score - (W["defensive"] if defensive else 0)
        path_ok = (defensive and score >= 18) or ((not defensive) and stock_score >= 15)
        if not (path_ok and atr[t] <= 4):
            continue
        e = o[t + 1]
        if not e or np.isnan(e) or e <= 0:
            continue
        row = dict(code=code, name=name, T=d, close=float(c[t]), entry=float(e),
                   score=round(float(score), 1), atr=round(float(atr[t]), 2),
                   year=d[:4], month=d[:7],
                   defensive=defensive, def_days=int(dmap_dd[d]), idx_rsv=round(float(dmap_rsv[d]), 1),
                   gap_entry=round((e / c[t] - 1) * 100, 2),
                   above_ma250=bool(c[t] > ma250[t]) if not np.isnan(ma250[t]) else None,
                   ma60_up=bool(ma60[t] > ma60[t - 10]) if not np.isnan(ma60[t - 10]) else None,
                   days_since_low=int(days_since_low[t]) if not np.isnan(days_since_low[t]) else None,
                   rsi_div=bool(rsi_div[t]) if not np.isnan(rsi[t]) else None,
                   volx=round(float(volx[t]), 2) if not np.isnan(volx[t]) else None,
                   vstd20=round(float(vstd20[t]), 2) if not np.isnan(vstd20[t]) else None,
                   dd250=round(float(dd250[t]), 1) if not np.isnan(dd250[t]) else None,
                   dd60=round(float(dd60[t]), 1), price_lt3=bool(c[t] < 3),
                   repeat10=bool(t - last_q <= 10))
        last_q = t
        # 赛跑: 各窗口 × 目标(+5/+10), 止损-8%; 进场日只看收盘(引擎review口径)
        stop = e * 0.92
        arr_c, arr_h, arr_l = c.values, h.values, l.values
        for Wd in WINDOWS:
            for tgt_pct in (5, 10):
                tgt = e * (1 + tgt_pct / 100)
                out = None
                if arr_c[t + 1] <= stop:
                    out = "stop"
                else:
                    end = min(t + 1 + Wd, n - 1)
                    for j in range(t + 2, end + 1):
                        if arr_l[j] <= stop:
                            out = "stop"
                            break
                        if arr_h[j] >= tgt:
                            out = "win"
                            break
                    if out is None:
                        out = "timeout" if t + 1 + Wd <= n - 1 else "open"  # open=窗口未走完
                row[f"race{Wd}_{tgt_pct}"] = out
            row[f"ret{Wd}"] = round((arr_c[min(t + 1 + Wd, n - 1)] / e - 1) * 100, 2) \
                if t + 1 + Wd <= n - 1 else None
        sigs.append(row)
    return sigs


def main() -> None:
    idx = idx_series()
    print(f"[research] 指数就绪 {idx.index[0]}..{idx.index[-1]} ({len(idx)}天)")
    spot = WK.get_spot(600)
    codes = []
    for _, r in spot.iterrows():
        code, name = str(r.get("代码", "")), str(r.get("名称", ""))
        if len(code) != 6 or code.startswith(("68", "8", "4")):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        codes.append((code, name))
    print(f"[research] universe={len(codes)}")
    kc = pickle.loads(CACHE.read_bytes()) if CACHE.exists() else {}
    allsigs, fetched = [], 0
    for k, (code, name) in enumerate(codes):
        sym = ("sh" if code[0] in "69" else "sz") + code
        if code not in kc:
            df = BF.tx_kline(sym, BARS)
            kc[code] = BF._drop_intraday(df) if df is not None else None
            fetched += 1
            time.sleep(0.06)
            if fetched % 120 == 0:
                CACHE.write_bytes(pickle.dumps(kc))
        df = kc[code]
        if df is None:
            continue
        allsigs.extend(stock_signals(df, idx, code, name))
        if (k + 1) % 100 == 0:
            print(f"[research] {k+1}/{len(codes)} 信号累计{len(allsigs)}")
    CACHE.write_bytes(pickle.dumps(kc))
    df = pd.DataFrame(allsigs)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"[research] 完成: {len(df)}笔信号 → {OUT}")
    if len(df):
        print(df.groupby("year").size().to_string())


if __name__ == "__main__":
    main()
