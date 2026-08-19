#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立跨源收盘价留痕器（免费公开源，不参与业务打分）。

从结果 JSON 读取代码与 T，调用原 weekly 引擎已有的东财/腾讯数据客户端，
把来源、源内日期、价格、最大偏差和状态写成独立 JSON。任何单源、日期不符或
偏差>1%都不会得到 verified，供 codex_audit.price_verification_by_code 引用。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
from typing import Any


HERE = pathlib.Path(__file__).resolve().parent
ENGINE = HERE.parent / "claude_code_Trading_skill_no_API_Doc" / "weekly-ashare-rank" / "ashare_weekly_rank.py"
CACHE = pathlib.Path(r"C:\Trading_analysis\data\cache\ashare_weekly")


def _load_engine():
    spec = importlib.util.spec_from_file_location("codex_price_engine", ENGINE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {ENGINE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._CACHE_DIR = CACHE
    return mod


def _extract(result: dict[str, Any], skill: str) -> tuple[str, list[str]]:
    if skill == "stock-diagnostic":
        return str(result.get("as_of", "")), [str(result.get("code", ""))]
    if skill == "bottom-fishing":
        rows = list(result.get("candidates") or [])
        if result.get("adjudicated"):
            rows = [x for x in rows if x.get("judge") == "✓"]
        return str(result.get("T", "")), [str(x.get("code", "")) for x in rows]
    return str(result.get("as_of", "")), [str(x.get("code", "")) for x in result.get("candidates") or []]


def _last(df: Any) -> tuple[str | None, float | None]:
    if df is None or not len(df):
        return None, None
    try:
        return str(df["日期"].iloc[-1])[:10], round(float(df["收盘"].iloc[-1]), 2)
    except (KeyError, TypeError, ValueError):
        return None, None


def _sina_last(eng: Any, code: str) -> tuple[str | None, float | None]:
    try:
        response = eng._SESSION.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData",
            params={"symbol": eng._tx_secid(code), "scale": 240, "datalen": 3},
            headers={"Referer": "https://finance.sina.com.cn/"}, timeout=15,
        )
        rows = response.json()
        if rows:
            row = rows[-1]
            return str(row.get("day", ""))[:10], round(float(row["close"]), 2)
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _yahoo_last(eng: Any, code: str) -> tuple[str | None, float | None]:
    suffix = ".SS" if code.startswith(("60", "68", "9", "5")) else ".SZ"
    try:
        response = eng._SESSION.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}",
            params={"range": "5d", "interval": "1d"}, timeout=12,
        )
        result = response.json()["chart"]["result"][0]
        pairs = [(ts, close) for ts, close in zip(
            result.get("timestamp") or [], result["indicators"]["quote"][0].get("close") or []
        ) if close is not None]
        if pairs:
            ts, close = pairs[-1]
            date = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(
                dt.timezone(dt.timedelta(hours=8))).date().isoformat()
            return date, round(float(close), 2)
    except Exception:  # noqa: BLE001
        pass
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Codex 独立跨源收盘价验真（不改业务结果）")
    ap.add_argument("--skill", required=True,
                    choices=("bottom-fishing", "stock-diagnostic", "weekly-ashare-rank"))
    ap.add_argument("--result", required=True, help="引擎结果 JSON")
    ap.add_argument("--out", help="输出 JSON；默认写到结果 JSON 同目录")
    args = ap.parse_args()

    result_path = pathlib.Path(args.result)
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    as_of, codes = _extract(result, args.skill)
    codes = [x for x in dict.fromkeys(codes) if len(x) == 6 and x.isdigit()]
    if not as_of or (not codes and args.skill != "bottom-fishing"):
        raise SystemExit("结果 JSON 缺 T/as_of 或候选代码")

    CACHE.mkdir(parents=True, exist_ok=True)
    eng = _load_engine()
    rows: dict[str, Any] = {}
    for code in codes:
        em_date, em_price = _last(eng._em_kline(code, bars=5))
        tx_date, tx_price = _last(eng._tx_kline(code, bars=5))
        all_sources = {
            "东方财富": {"date": em_date, "price": em_price},
            "腾讯": {"date": tx_date, "price": tx_price},
        }
        if sum(x["date"] == as_of and x["price"] is not None for x in all_sources.values()) < 2:
            sina_date, sina_price = _sina_last(eng, code)
            all_sources["新浪"] = {"date": sina_date, "price": sina_price}
        if sum(x["date"] == as_of and x["price"] is not None for x in all_sources.values()) < 2:
            yahoo_date, yahoo_price = _yahoo_last(eng, code)
            all_sources["雅虎"] = {"date": yahoo_date, "price": yahoo_price}
        usable = {name: x["price"] for name, x in all_sources.items()
                  if x["date"] == as_of and x["price"] is not None}
        dev = None
        if len(usable) >= 2:
            vals = list(usable.values())
            dev = round((max(vals) - min(vals)) / max(vals) * 100, 3)
            status = "verified" if dev <= 1.0 else "conflict"
        elif len(usable) == 1:
            status = "single"
        else:
            status = "stale_or_missing"
        rows[code] = {
            "as_of": as_of,
            "sources": all_sources,
            "usable_sources": usable,
            "max_dev_pct": dev,
            "status": status,
        }

    payload = {
        "version": "codex-price-verification/v1",
        "skill": args.skill,
        "as_of": as_of,
        "retrieved_at_beijing": eng._cn_now().isoformat(timespec="seconds"),
        "price_verification_by_code": rows,
    }
    out = pathlib.Path(args.out) if args.out else result_path.with_name("codex_price_verification.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [code for code, row in rows.items() if row["status"] != "verified"]
    print(f"已写出 {out}")
    for code, row in rows.items():
        print(f"{code}: {row['status']} sources={row['usable_sources']} dev={row['max_dev_pct']}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
