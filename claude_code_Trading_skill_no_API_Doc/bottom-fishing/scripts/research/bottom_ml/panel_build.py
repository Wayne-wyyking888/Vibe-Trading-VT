# -*- coding: utf-8 -*-
"""从缓存K线逐日复算引擎因子/hits/score + 生成标签 → panel.parquet。
口径严格对齐 bottom_fishing.py 的 index_features / stock_factors / review。只读研究。"""
import sys, pathlib
import numpy as np, pandas as pd

ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
WEEKLY = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
sys.path.insert(0, str(WEEKLY))
import ashare_weekly_rank as WK  # noqa

# —— 引擎常量(照抄, 不改) ——
W = dict(defensive=8.6, above_ma10=5.2, dif_up=4.5, rsv_recover=3.9, dd_sweet=3.7,
         above_ma5=3.7, gap_reclaim=4.4,
         rsv_deep=-7.4, downstk4=-6.3, zt20=-5.4, atr_hi=-3.5, fresh_low=-3.1)
STOP_PCT, TGT1, MAX_HOLD = -8.0, 5.0, 20
TH_TOTAL, TH_STOCK, TH_ATR = 18.0, 15.0, 4.0

TODAY = WK._cn_now().strftime("%Y-%m-%d")
NOW = WK._cn_now()
DROP_TODAY = (NOW.hour, NOW.minute) < (15, 5)


def index_panel() -> pd.DataFrame:
    df = pd.read_parquet(ROOT / "index_399006.parquet").reset_index(drop=True)
    if DROP_TODAY and df.d.iloc[-1] == TODAY:
        df = df.iloc[:-1].reset_index(drop=True)
    ma20 = df.c.rolling(20).mean()
    i5 = df.c.pct_change(5)
    defensive = (df.c < ma20) | (i5 < -0.02)
    dd, cnt = [], 0
    for v in defensive:
        cnt = cnt + 1 if v else 0
        dd.append(cnt)
    lo14 = df.l.rolling(14).min(); hi14 = df.h.rolling(14).max()
    idx_rsv = (df.c - lo14) / (hi14 - lo14 + 1e-9) * 100
    return pd.DataFrame({"d": df.d, "defensive": defensive.values,
                         "def_days": dd, "idx_rsv": idx_rsv.round(1).values,
                         "idx_chg1": (df.c / df.c.shift(1) - 1).mul(100).round(2).values})


def stock_rows(df: pd.DataFrame, idx: pd.DataFrame) -> pd.DataFrame:
    """对单只票逐日算因子+hits+score+标签, 返回底部区行(带label)。"""
    df = df.sort_values("d").reset_index(drop=True)
    if DROP_TODAY and df.d.iloc[-1] == TODAY:
        df = df.iloc[:-1].reset_index(drop=True)
    n = len(df)
    if n < 90:
        return pd.DataFrame()
    c, h, l, o, v = df.c, df.h, df.l, df.o, df.v
    hi60 = h.rolling(60).max(); lo60 = l.rolling(60).min()
    dd60 = (c / hi60 - 1) * 100
    pos60 = (c - lo60) / (hi60 - lo60 + 1e-9) * 100
    ret = c.pct_change()
    ma5 = c.rolling(5).mean(); ma10 = c.rolling(10).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / c * 100
    ema12 = c.ewm(span=12, adjust=False).mean(); ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dif_up = (dif - dif.shift(3)) > 0
    lo14 = l.rolling(14).min(); hi14 = h.rolling(14).max()
    rsv = (c - lo14) / (hi14 - lo14 + 1e-9) * 100
    is_low = l <= lo60 * 1.001
    # cnt: 距最近一次新低的K线数(当日即新低=0)
    cnt_arr, cc = np.empty(n), 999
    for i in range(n):
        cc = 0 if bool(is_low.iloc[i]) else cc + 1
        cnt_arr[i] = cc
    # downstk: 结尾连续阴线数(含当日)
    negrun = np.zeros(n, int); run = 0
    rv = ret.values
    for i in range(n):
        if i == 0 or np.isnan(rv[i]) or rv[i] >= 0:
            run = 0
        else:
            run += 1
        negrun[i] = run
    zt20 = (ret >= 0.093).rolling(20).max().fillna(0) > 0
    dd250 = (c / h.rolling(250).max() - 1) * 100

    hits = pd.DataFrame({
        "above_ma10": c > ma10,
        "dif_up": dif_up,
        "rsv_recover": (rsv > 20) & (rsv <= 40),
        "dd_sweet": (dd60 > -45) & (dd60 <= -30),
        "above_ma5": c > ma5,
        "gap_reclaim": (o < c.shift(1) * 0.98) & (c > o),
        "rsv_deep": rsv <= 15,
        "downstk4": pd.Series(negrun >= 4, index=df.index),
        "zt20": zt20,
        "atr_hi": atr >= 7,
        "fresh_low": pd.Series(cnt_arr <= 1, index=df.index),
    })
    # 合并大盘防守日(按日期对齐)
    dmap = idx.set_index("d")
    defensive = df.d.map(dmap["defensive"]).fillna(False).astype(bool).values
    pos_w = (hits["above_ma10"] * W["above_ma10"] + hits["dif_up"] * W["dif_up"]
             + hits["rsv_recover"] * W["rsv_recover"] + hits["dd_sweet"] * W["dd_sweet"]
             + hits["above_ma5"] * W["above_ma5"] + hits["gap_reclaim"] * W["gap_reclaim"]
             + hits["rsv_deep"] * W["rsv_deep"] + hits["downstk4"] * W["downstk4"]
             + hits["zt20"] * W["zt20"] + hits["atr_hi"] * W["atr_hi"] + hits["fresh_low"] * W["fresh_low"])
    stock_score = pos_w                       # 不含防守日项
    score = pos_w + np.where(defensive, W["defensive"], 0.0)

    # —— 标签: T+1开盘进场, 先+5%(high)还是先-8%(low), 20交易日; 买入日收<=stop=崩盘止损 ——
    O, H, L, C = o.values, h.values, l.values, c.values
    outcome = np.full(n, "", object); days = np.full(n, np.nan)
    for i in range(n):
        if i + 2 >= n:
            continue
        entry = O[i + 1]
        stop = entry * (1 + STOP_PCT / 100); tgt = entry * (1 + TGT1 / 100)
        if C[i + 1] <= stop:
            outcome[i], days[i] = "stop", 1; continue
        res, dd_ = "", np.nan
        end = min(i + 1 + MAX_HOLD, n - 1)
        for j in range(i + 2, end + 1):
            if L[j] <= stop:
                res, dd_ = "stop", j - (i + 1); break
            if H[j] >= tgt:
                res, dd_ = "win", j - (i + 1); break
        if res == "" and i + 1 + MAX_HOLD <= n - 1:
            res, dd_ = "timeout", MAX_HOLD
        outcome[i], days[i] = res, dd_

    out = pd.DataFrame({
        "code": df.code.iloc[0] if "code" in df else "?",
        "d": df.d.values, "close": C.round(2),
        "dd60": dd60.round(1).values, "pos60": pos60.round(1).values, "atr": atr.round(2).values,
        "rsv": rsv.round(1).values, "ret5": (c.pct_change(5) * 100).round(2).values,
        "volx": (v / v.rolling(20).mean()).round(2).values,
        "dd250": dd250.round(1).values, "downstk": negrun,
        "defensive": defensive, "score": score.round(1).values,
        "stock_score": stock_score.round(1).values,
        "rsv_deep_hit": hits["rsv_deep"].values, "zt20_hit": hits["zt20"].values,
        "fresh_low_hit": hits["fresh_low"].values, "atr_hi_hit": hits["atr_hi"].values,
        "outcome": outcome, "hold_days": days,
    })
    # 底部区筛选(引擎口径) + 已了结(有标签)
    zone = (dd60.values <= -20) & (pos60.values <= 25)
    out = out[zone & (out.outcome != "")].copy()
    return out


def main():
    idx = index_panel()
    idx.to_parquet(ROOT / "index_panel.parquet")
    allk = pd.read_parquet(ROOT / "klines.parquet")
    meta = pd.read_parquet(ROOT / "meta.parquet").set_index("code")
    rows = []
    for code, g in allk.groupby("code"):
        r = stock_rows(g.assign(code=code), idx)
        if len(r):
            rows.append(r)
    panel = pd.concat(rows, ignore_index=True)
    panel = panel.merge(idx.rename(columns={"defensive": "mkt_def"}), on="d", how="left")
    panel["industry"] = panel.code.map(meta["industry"])
    panel["month"] = panel.d.str[:7]
    panel.to_parquet(ROOT / "panel.parquet")
    # 汇总
    n = len(panel)
    w = (panel.outcome == "win").mean(); s = (panel.outcome == "stop").mean()
    print(f"[panel] 底部区已了结样本 = {n}  ({panel.d.min()} → {panel.d.max()})")
    print(f"[panel] 全底部区 base rate: 胜(先+5%)={w*100:.1f}%  雷(先-8%)={s*100:.1f}%  "
          f"timeout={(panel.outcome=='timeout').mean()*100:.1f}%")
    q = panel[((panel.mkt_def) & (panel.score >= TH_TOTAL)) |
              ((~panel.mkt_def) & (panel.stock_score >= TH_STOCK))]
    q = q[q.atr <= TH_ATR]
    if len(q):
        print(f"[panel] 引擎过线子集 n={len(q)}: 胜={ (q.outcome=='win').mean()*100:.1f}%  "
              f"雷={ (q.outcome=='stop').mean()*100:.1f}%")
    print(f"[panel] saved → panel.parquet")


if __name__ == "__main__":
    main()
