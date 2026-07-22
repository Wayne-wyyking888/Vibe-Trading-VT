# -*- coding: utf-8 -*-
"""探针: 确认建模依赖 + 历史K线可得深度。不碰任何skill文件。"""
import json, urllib.request, sys

# 1) 依赖
mods = {}
for m in ["catboost", "lightgbm", "sklearn", "numpy", "pandas"]:
    try:
        mod = __import__(m)
        mods[m] = getattr(mod, "__version__", "?")
    except Exception as e:
        mods[m] = f"MISSING ({e.__class__.__name__})"
print("=== deps ===")
for k, v in mods.items():
    print(f"  {k}: {v}")

# 2) 历史K线深度 (腾讯qfq, 请求1200根看能到多早)
_HOSTS = ["https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
          "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
          "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"]
def kline(sym, n=1200):
    for base in _HOSTS:
        try:
            j = json.loads(urllib.request.urlopen(f"{base}?param={sym},day,,,{n},qfq", timeout=15).read())
            rows = j["data"][sym].get("qfqday") or j["data"][sym].get("day") or []
            return rows
        except Exception:
            continue
    return None

print("\n=== kline depth ===")
for sym in ["sz399006", "sh600519", "sh601601"]:
    rows = kline(sym)
    if rows:
        print(f"  {sym}: {len(rows)}根  {rows[0][0]} → {rows[-1][0]}")
    else:
        print(f"  {sym}: FAIL")
