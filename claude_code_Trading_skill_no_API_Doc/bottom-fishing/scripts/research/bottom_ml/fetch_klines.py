# -*- coding: utf-8 -*-
"""抓取 universe 历史日K(腾讯qfq, 641根≈2023-11起) → 落盘 klines.parquet。
只读研究, 复用 weekly 引擎的 get_spot 拿 universe, 不修改任何 skill 文件。"""
import json, time, sys, pathlib, urllib.request
import pandas as pd

WEEKLY = pathlib.Path(r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
sys.path.insert(0, str(WEEKLY))
import ashare_weekly_rank as WK  # noqa

OUT = pathlib.Path(r"C:\Trading_analysis\research\bottom_ml")
OUT.mkdir(parents=True, exist_ok=True)

_HOSTS = ["https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
          "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
          "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"]

def kline(sym, n=650):
    for base in _HOSTS:
        try:
            j = json.loads(urllib.request.urlopen(f"{base}?param={sym},day,,,{n},qfq", timeout=15).read())
            rows = j["data"][sym].get("qfqday") or j["data"][sym].get("day") or []
            df = pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                               for r in rows], columns=["d", "o", "c", "h", "l", "v"])
            if len(df) >= 90:
                _HOSTS.remove(base); _HOSTS.insert(0, base)
                return df
            return None
        except Exception:
            continue
    return None

# 指数(创业板)单独存
idx = kline("sz399006", 1300)
idx.to_parquet(OUT / "index_399006.parquet")
print(f"[fetch] index sz399006: {len(idx)}根 {idx.d.iloc[0]}→{idx.d.iloc[-1]}")

spot = WK.get_spot(600)
codes = []
for _, r in spot.iterrows():
    code, name = str(r.get("代码", "")), str(r.get("名称", ""))
    if len(code) != 6 or code.startswith(("68", "8", "4")):
        continue
    if "ST" in name.upper() or "退" in name:
        continue
    codes.append((code, name, str(r.get("行业", "") or "")))
print(f"[fetch] universe={len(codes)}")

frames, meta = [], []
for k, (code, name, ind) in enumerate(codes):
    sym = ("sh" if code[0] in "69" else "sz") + code
    df = kline(sym, 650)
    if df is None:
        continue
    df = df.copy(); df["code"] = code
    frames.append(df)
    meta.append((code, name, ind))
    if (k + 1) % 100 == 0:
        print(f"[fetch] {k+1}/{len(codes)} 已存{len(frames)}只")
    time.sleep(0.05)

allk = pd.concat(frames, ignore_index=True)
allk.to_parquet(OUT / "klines.parquet")
pd.DataFrame(meta, columns=["code", "name", "industry"]).to_parquet(OUT / "meta.parquet")
print(f"[fetch] DONE: {len(meta)}只 {len(allk)}行 → klines.parquet")
