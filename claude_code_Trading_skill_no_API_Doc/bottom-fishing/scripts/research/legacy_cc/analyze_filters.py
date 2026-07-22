# -*- coding: utf-8 -*-
"""filter面板分析: 基线(引擎线) 分年×分窗 + 候选filter的Δ胜率/Δ爆雷 + 2024毒年拆检"""
import pathlib

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
df = pd.read_csv(HERE / "signals_2024plus.csv", dtype={"code": str})
df["year"] = df["year"].astype(str)
pd.set_option("display.width", 200)


def race_stats(sub: pd.DataFrame, wd: int, tgt: int) -> dict:
    col = f"race{wd}_{tgt}"
    d = sub[sub[col].isin(["win", "stop", "timeout"])]
    n = len(d)
    if n < 8:
        return dict(n=n, win=np.nan, stop=np.nan, ev=np.nan)
    win = (d[col] == "win").mean() * 100
    stop = (d[col] == "stop").mean() * 100
    to = d[d[col] == "timeout"]
    to_ret = to[f"ret{wd}"].mean() if len(to) else 0.0
    ev = (d[col] == "win").mean() * tgt + (d[col] == "stop").mean() * (-8) \
        + (d[col] == "timeout").mean() * (to_ret if not np.isnan(to_ret) else 0)
    return dict(n=n, win=round(win, 1), stop=round(stop, 1), ev=round(ev, 2))


def block(sub: pd.DataFrame, label: str) -> None:
    print(f"\n### {label}  (信号{len(sub)}笔, {sub['code'].nunique()}只)")
    rows = []
    for wd in (5, 20, 30, 60):
        r5 = race_stats(sub, wd, 5)
        r10 = race_stats(sub, wd, 10)
        rets = sub[f"ret{wd}"].dropna()
        rows.append(dict(窗口=f"{wd}日", 已了结n=r5["n"],
                         先到5pct=r5["win"], 先到10pct=r10["win"], 先砸8pct=r5["stop"],
                         EV5=r5["ev"], EV10=r10["ev"],
                         平均收益=round(rets.mean(), 2) if len(rets) else np.nan,
                         中位=round(rets.median(), 2) if len(rets) else np.nan,
                         正收益占比=round((rets > 0).mean() * 100, 1) if len(rets) else np.nan))
    print(pd.DataFrame(rows).to_string(index=False))


print(f"# 面板总览: {len(df)}笔 {df['T'].min()}..{df['T'].max()}, {df['code'].nunique()}只")
print(df.groupby("year").size().rename("信号数").to_string())

block(df, "基线=引擎双路径线(全部信号)")
for y in sorted(df.year.unique()):
    block(df[df.year == y], f"基线·{y}年")

# ---- 候选filter: 20日+5%赛跑(主口径) 与 60日ret, 全期 vs 2024 ----
FILTERS = [
    ("个股在MA250上方", df.above_ma250 == True),                     # noqa: E712
    ("个股MA60向上(10日)", df.ma60_up == True),                      # noqa: E712
    ("企稳>5天(距60日低点)", df.days_since_low > 5),
    ("企稳2-5天", df.days_since_low.between(2, 5)),
    ("RSI10日背离(代理)", df.rsi_div == True),                       # noqa: E712
    ("缩量volx<0.8", df.volx < 0.8),
    ("放量volx>=1.5", df.volx >= 1.5),
    ("量波动vstd20高(>=中位)", df.vstd20 >= df.vstd20.median()),
    ("长期深跌dd250<=-50", df.dd250 <= -50),
    ("低价股<3元", df.price_lt3 == True),                            # noqa: E712
    ("进场跳空低开<=-2%(P9)", df.gap_entry <= -2),
    ("进场高开>3%(纪律放弃区)", df.gap_entry > 3),
    ("旋转门: 10日内重复过线", df.repeat10 == True),                 # noqa: E712
    ("剔除旋转门(repeat10=F)", df.repeat10 == False),                # noqa: E712
    ("防守日>=9天", df.def_days >= 9),
    ("大盘RSV 15-40", df.idx_rsv.between(15, 40)),
    ("防守日路径", df.defensive == True),                            # noqa: E712
    ("非防守路径", df.defensive == False),                           # noqa: E712
]
base20 = race_stats(df, 20, 5)
base60 = race_stats(df, 60, 10)
d24 = df[df.year == "2024"]
b24 = race_stats(d24, 20, 5)
print(f"\n# filter表  [基线全期: 先到5%={base20['win']}% 爆雷={base20['stop']}% EV={base20['ev']} | "
      f"2024: {b24['win']}%/{b24['stop']}%/EV{b24['ev']}]")
rows = []
for name, mask in FILTERS:
    sub, sub24 = df[mask.fillna(False)], d24[mask.reindex(d24.index).fillna(False)]
    r = race_stats(sub, 20, 5)
    r60 = race_stats(sub, 60, 10)
    r24 = race_stats(sub24, 20, 5)
    ret60 = sub["ret60"].dropna()
    rows.append(dict(filter=name, n=r["n"], 占比=f"{len(sub)/len(df)*100:.0f}%",
                     胜20=r["win"], 雷20=r["stop"], EV20=r["ev"],
                     Δ胜=round((r["win"] or 0) - base20["win"], 1) if not np.isnan(r["win"]) else np.nan,
                     Δ雷=round((r["stop"] or 0) - base20["stop"], 1) if not np.isnan(r["stop"]) else np.nan,
                     胜60_10=r60["win"], ret60均=round(ret60.mean(), 2) if len(ret60) else np.nan,
                     n24=r24["n"], 胜24=r24["win"], 雷24=r24["stop"], EV24=r24["ev"]))
print(pd.DataFrame(rows).to_string(index=False))

# 月度分布(看成簇)
print("\n# 月度信号数&20日爆雷率")
m = df[df[f"race20_5"].isin(["win", "stop", "timeout"])].groupby("month").agg(
    n=("code", "size"), 雷pct=("race20_5", lambda s: round((s == "stop").mean() * 100, 0)))
print(m.to_string())
