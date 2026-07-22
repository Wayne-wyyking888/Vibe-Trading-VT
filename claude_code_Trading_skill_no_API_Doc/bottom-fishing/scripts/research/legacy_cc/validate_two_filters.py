# -*- coding: utf-8 -*-
"""旋转门冷却 + 放量volx 两个候选filter的走样本验证
- 无拟合参数 → 验证重点: ①5个半年段方向稳定性 ②阈值敏感性(旋转门窗5/10/15/20日; volx 1.2/1.5/2.0)
- ③组合效果 ④信号量损耗(空手日会不会暴增)
通过线(事先定): 段内Δ雷≤0或Δ胜≥0 至少4/5段成立, 全期与2024均EV改善, 无阈值刀锋
"""
import pathlib
import pickle

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
df = pd.read_csv(HERE / "signals_2024plus.csv", dtype={"code": str})
df["year"] = df["year"].astype(str)
pd.set_option("display.width", 220)

# 交易日历: 从K线缓存里任取覆盖全期的股票拼出
kc = pickle.loads((HERE / "kline_cache_900.pkl").read_bytes())
cal = sorted({d for v in kc.values() if v is not None for d in v.d})
ord_map = {d: i for i, d in enumerate(cal)}
df["tday"] = df["T"].map(ord_map)

# 重算旋转门: 同票距上次过线的交易日间隔
df = df.sort_values(["code", "tday"]).reset_index(drop=True)
df["gap"] = df.groupby("code")["tday"].diff()
for w in (5, 10, 15, 20):
    df[f"rep{w}"] = df["gap"] <= w   # NaN(首次)→False=非旋转门


def seg(t: str) -> str:
    y, m = t[:4], int(t[5:7])
    return f"{y}H{1 if m <= 6 else 2}" if y != "2026" else "2026"


df["seg"] = df["T"].map(seg)


def stats(sub: pd.DataFrame) -> tuple:
    d = sub[sub["race20_5"].isin(["win", "stop", "timeout"])]
    n = len(d)
    if n < 15:
        return n, np.nan, np.nan, np.nan
    win = (d["race20_5"] == "win").mean() * 100
    stop = (d["race20_5"] == "stop").mean() * 100
    to = d[d["race20_5"] == "timeout"]["ret20"].mean()
    ev = (d["race20_5"] == "win").mean() * 5 + (d["race20_5"] == "stop").mean() * (-8) \
        + (d["race20_5"] == "timeout").mean() * (0 if np.isnan(to) else to)
    return n, round(win, 1), round(stop, 1), round(ev, 2)


SEGS = ["2024H1", "2024H2", "2025H1", "2025H2", "2026"]
print("# ①分段稳定性 (20日口径: n/胜/雷/EV)")
rows = []
for name, mask in [("基线", pd.Series(True, index=df.index)),
                   ("剔除旋转门(rep10)", ~df.rep10),
                   ("放量volx>=1.5", df.volx >= 1.5),
                   ("组合: 剔旋转门+放量", (~df.rep10) & (df.volx >= 1.5)),
                   ("组合: 剔旋转门∪放量", (~df.rep10) | (df.volx >= 1.5))]:
    r = {"filter": name}
    for s in SEGS:
        n, w_, st, ev = stats(df[mask & (df.seg == s)])
        r[s] = f"{n}|{w_}/{st}|{ev}"
    n, w_, st, ev = stats(df[mask])
    r["全期"] = f"{n}|{w_}/{st}|{ev}"
    rows.append(r)
print(pd.DataFrame(rows).to_string(index=False))

print("\n# ②阈值敏感性")
for w in (5, 10, 15, 20):
    n, w_, st, ev = stats(df[~df[f"rep{w}"]])
    print(f"  剔旋转门 窗{w}日: n={n} 胜{w_} 雷{st} EV{ev}")
for v in (1.2, 1.5, 2.0):
    n, w_, st, ev = stats(df[df.volx >= v])
    print(f"  放量>= {v}: n={n} 胜{w_} 雷{st} EV{ev}")
for v in (1.2, 1.5):
    n, w_, st, ev = stats(df[df.volx < v])
    print(f"  (对照)volx< {v}: n={n} 胜{w_} 雷{st} EV{ev}")

print("\n# ③信号量损耗(按日)")
days_all = df.groupby("T").size()
for name, mask in [("剔除旋转门", ~df.rep10), ("剔旋转门+放量gate", (~df.rep10) & (df.volx >= 1.5))]:
    days_f = df[mask].groupby("T").size()
    lost = len(days_all) - len(days_f)
    print(f"  {name}: 有信号日 {len(days_all)}→{len(days_f)} (清空{lost}日, {lost/len(days_all)*100:.0f}%), "
          f"日均票数 {days_all.mean():.1f}→{days_f.mean():.1f}")

print("\n# ④60日窗核对(防20日口径侥幸): 先到+10% / 先砸-8%")
for name, mask in [("基线", pd.Series(True, index=df.index)), ("剔除旋转门", ~df.rep10),
                   ("放量>=1.5", df.volx >= 1.5)]:
    d = df[mask & df["race60_10"].isin(["win", "stop", "timeout"])]
    print(f"  {name}: n={len(d)} 先到10%={(d['race60_10']=='win').mean()*100:.1f}% "
          f"雷={(d['race60_10']=='stop').mean()*100:.1f}% ret60均={d['ret60'].mean():.2f}%")

print("\n# ⑤dd250<=-50 描述性(仅供HTML标注, 不进打分)")
d = df[(df.dd250 <= -50) & df["race20_5"].isin(["win", "stop", "timeout"])]
print(f"  n={len(d)} 分布: {d.groupby('seg').size().to_dict()} 胜{(d['race20_5']=='win').mean()*100:.1f}% "
      f"雷{(d['race20_5']=='stop').mean()*100:.1f}%")
