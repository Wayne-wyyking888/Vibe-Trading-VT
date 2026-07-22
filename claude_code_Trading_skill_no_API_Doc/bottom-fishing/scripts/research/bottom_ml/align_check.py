# -*- coding: utf-8 -*-
"""对齐校验: 用面板管线复算 600415/600048 在 2026-07-15 的因子, 比对引擎JSON。"""
import sys, pathlib
import numpy as np, pandas as pd
ROOT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
sys.path.insert(0, str(ROOT))
import panel_build as PB

idx = PB.index_panel()
allk = pd.read_parquet(ROOT / "klines.parquet")
# 复算(去掉底部区/标签过滤, 保留全部日) —— 临时改: 直接调 stock_rows 前的因子段不方便, 这里用引擎口径手算末日
from importlib import reload

def check(code, ref):
    g = allk[allk.code == code].sort_values("d").reset_index(drop=True)
    if PB.DROP_TODAY and g.d.iloc[-1] == PB.TODAY:
        g = g.iloc[:-1].reset_index(drop=True)
    # 末行=2026-07-15
    r = PB.stock_rows.__wrapped__ if hasattr(PB.stock_rows, "__wrapped__") else None
    # 直接内联算末日因子(照抄stock_rows关键量)
    c, h, l, o, v = g.c, g.h, g.l, g.o, g.v
    hi60 = h.rolling(60).max(); lo60 = l.rolling(60).min()
    dd60 = (c / hi60 - 1) * 100
    pos60 = (c - lo60) / (hi60 - lo60 + 1e-9) * 100
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / c * 100
    lo14 = l.rolling(14).min(); hi14 = h.rolling(14).max()
    rsv = (c - lo14) / (hi14 - lo14 + 1e-9) * 100
    dd250 = (c / h.rolling(250).max() - 1) * 100
    print(f"\n{code}  末日={g.d.iloc[-1]}")
    print(f"  dd60  我={dd60.iloc[-1]:.1f}   引擎={ref['dd60']}")
    print(f"  pos60 我={pos60.iloc[-1]:.1f}   引擎={ref['pos60']}")
    print(f"  atr   我={atr.iloc[-1]:.2f}  引擎={ref['atr']}")
    print(f"  rsv   我={rsv.iloc[-1]:.1f}   引擎={ref['rsv']}")
    print(f"  ret5  我={(c.pct_change(5).iloc[-1]*100):.2f}  引擎={ref['ret5']}")
    print(f"  dd250 我={dd250.iloc[-1]:.1f}   引擎={ref['dd250']}")
    print(f"  close 我={c.iloc[-1]:.2f}  引擎={ref['close']}")

check("600415", dict(dd60=-24.7, pos60=22.0, atr=3.99, rsv=79.5, ret5=4.2, dd250=-54.5, close=10.43))
check("600048", dict(dd60=-28.1, pos60=17.3, atr=3.36, rsv=90.9, ret5=6.05, dd250=-42.3, close=4.91))
