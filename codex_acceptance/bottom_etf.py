#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bottom-fishing ETF 持仓与走势相似度增强层。

保持 hash 锁定的业务引擎与原 renderer 不变：
1. 从东方财富公开机构持仓库读取最近一个“完整”基金报告期；
2. 识别其中的场内 ETF，并保留完整披露名单；
3. 对持仓占净值最高的一组 ETF 拉取腾讯前复权日线；
4. 以信号日 T 截止的最近 60 个共同日收益 Pearson 相关系数排序；
5. 把只读信息区块插在每张候选卡片 F10 行之后。

ETF 信息不参与推荐、分数、裁定、仓位或熔断。公开基金持仓是定期披露数据，
不是盘中实时仓位；一、三季报也可能只披露主要持仓，HTML 必须显示这一限制。
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable


VERSION = "bottom-etf-holdings/v1"
REPORT_API = "https://datacenter.eastmoney.com/securities/api/data/get"
REPORT_WEB_APIS = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
    "https://datacenter.eastmoney.com/api/data/v1/get",
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
)
REPORT_PAGE = "https://data.eastmoney.com/zlsj/detail/{date}-0-{code}.html"
KLINE_HOSTS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
)
EASTMONEY_KLINE_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SIMILARITY_DAYS = 60
MIN_RETURN_DAYS = 40
DEFAULT_SIMILARITY_MAX = 80
DEFAULT_HTML_TOP = 5
MAX_HOLDING_PAGES = 20
HOLDING_PAGE_SIZE = 500
KLINE_BARS = 110
_CN_TZ = dt.timezone(dt.timedelta(hours=8))


def _now() -> dt.datetime:
    return dt.datetime.now(_CN_TZ)


def _json_request(url: str, params: dict[str, Any], timeout: float = 18.0) -> dict[str, Any]:
    # 过滤表达式中的括号和引号也必须百分号编码；直接把双引号放进 URL
    # 会被部分代理/服务器以 HTTP 400 拒绝。
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://data.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _request_with_retry(
    request_json: Callable[..., dict[str, Any]],
    url: str,
    params: dict[str, Any],
    *,
    attempts: int = 3,
) -> dict[str, Any]:
    """短退避重试单个公开端点；最终异常交给上层切换端点/提供方。"""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return request_json(url, params)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (2 ** attempt))
    assert last_error is not None
    raise last_error


def _web_report_params(params: dict[str, Any]) -> dict[str, Any]:
    """把 securities/WAP 参数翻译成 DataCenter v1 参数。"""
    translated: dict[str, Any] = {
        "reportName": params.get("type"),
        "columns": params.get("sty") or "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageNumber": params.get("p") or 1,
        "pageSize": params.get("ps") or 100,
    }
    if params.get("st"):
        translated["sortColumns"] = params["st"]
    if params.get("sr") not in (None, ""):
        translated["sortTypes"] = params["sr"]
    if params.get("filter"):
        translated["filter"] = params["filter"]
    return translated


def _report_request(
    request_json: Callable[..., dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """在线读取最新披露，主端点失败时依次切换三个公开备用端点。"""
    endpoints = [(REPORT_API, params)] + [
        (url, _web_report_params(params)) for url in REPORT_WEB_APIS
    ]
    errors: list[str] = []
    for url, endpoint_params in endpoints:
        try:
            obj = _request_with_retry(request_json, url, endpoint_params)
            if obj.get("success") is False or not isinstance(obj.get("result"), dict):
                raise RuntimeError(f"接口返回失败: code={obj.get('code')} message={obj.get('message')}")
            return obj
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {str(exc)[:120]}")
    raise RuntimeError("ETF公开披露端点全部不可用: " + " | ".join(errors))


def _read_cache(path: pathlib.Path, max_age_seconds: float | None = None) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if max_age_seconds is not None:
            fetched = dt.datetime.fromisoformat(str(obj.get("fetched_at")))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=_CN_TZ)
            if (_now() - fetched.astimezone(_CN_TZ)).total_seconds() > max_age_seconds:
                return None
        return obj
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(path: pathlib.Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _report_periods(cache_root: pathlib.Path, request_json: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    cache = cache_root / "report_periods.json"
    # 每次裁定在线确认最近完整报告期；缓存只留审计副本，不作为失败兜底。
    obj = _report_request(
        request_json,
        {
            "type": "RPT_MAIN_REPORTDATE",
            "sty": "ALL",
            "source": "DataCenter",
            "client": "WAP",
            "p": 1,
            "ps": 100,
            "sr": "",
            "st": "",
        },
    )
    rows = list(((obj.get("result") or {}).get("data") or []))
    _write_cache(cache, {"fetched_at": _now().isoformat(timespec="seconds"), "rows": rows})
    return rows


def _select_report_period(rows: list[dict[str, Any]]) -> dict[str, Any]:
    visible = [row for row in rows if str(row.get("IS_FUND_SHOW", "1")) == "1"]
    visible.sort(key=lambda row: str(row.get("REPORT_DATE", "")), reverse=True)
    complete = [row for row in visible if str(row.get("IS_COMPLETE", "0")) == "1"]
    selected = complete[0] if complete else (visible[0] if visible else None)
    if not selected:
        raise RuntimeError("公开持仓库未返回可用基金报告期")
    selected_date = str(selected.get("REPORT_DATE", ""))[:10]
    newer = next(
        (
            row
            for row in visible
            if str(row.get("REPORT_DATE", ""))[:10] > selected_date
            and str(row.get("IS_COMPLETE", "0")) != "1"
        ),
        None,
    )
    return {
        "report_date": selected_date,
        "report_name": str(selected.get("REPORT_DATE_NAME") or selected_date),
        "complete": str(selected.get("IS_COMPLETE", "0")) == "1",
        "newer_incomplete_report_date": str((newer or {}).get("REPORT_DATE", ""))[:10] or None,
        "newer_incomplete_report_name": str((newer or {}).get("REPORT_DATE_NAME") or "") or None,
    }


def _is_etf_row(row: dict[str, Any]) -> bool:
    code = str(row.get("FUND_CODE") or row.get("HOLDER_CODE") or "")
    derive = str(row.get("FUND_DERIVECODE") or "")
    short_name = str(row.get("HOLDER_NAME") or "")
    full_name = str(row.get("F9_HOLDER_NAME") or "")
    combined = f"{short_name} {full_name}"
    return bool(
        re.fullmatch(r"\d{6}", code)
        and re.fullmatch(r"\d{6}\.(?:SH|SZ)", derive)
        and "联接" not in combined
        and ("交易型开放式" in full_name or "ETF" in combined.upper())
    )


def _holding_row(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row.get("FUND_CODE") or row.get("HOLDER_CODE") or "")
    return {
        "code": code,
        "secucode": str(row.get("FUND_DERIVECODE") or ""),
        "name": str(row.get("HOLDER_NAME") or row.get("F9_HOLDER_NAME") or code),
        "full_name": str(row.get("F9_HOLDER_NAME") or row.get("HOLDER_NAME") or code),
        "fund_type": str(row.get("FUND_TYPE") or ""),
        "manager": str(row.get("ORG_NAME") or ""),
        "holding_weight_pct": _float(row.get("NETVALUE_RATIO")),
        "holding_value_yuan": _float(row.get("HOLD_VALUE")),
        "shares": _float(row.get("TOTAL_SHARES")),
        "change_type": str(row.get("CHANGE_TYPE_NEW") or row.get("CHANGE_TYPE") or ""),
    }


def _stock_etfs(
    code: str,
    report_date: str,
    cache_root: pathlib.Path,
    request_json: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    cache = cache_root / "holdings" / f"{code}_{report_date}.json"
    # 完整报告期也可能补充/更正；每次裁定在线刷新，缓存只作审计副本。
    rows: list[dict[str, Any]] = []
    pages = 1
    for page in range(1, MAX_HOLDING_PAGES + 1):
        obj = _report_request(
            request_json,
            {
                "type": "RPT_MAIN_ORGHOLDDETAIL",
                "sty": "ALL",
                "source": "DataCenter",
                "client": "WAP",
                "p": page,
                "ps": HOLDING_PAGE_SIZE,
                "sr": -1,
                "st": "TOTAL_SHARES",
                "filter": (
                    f'(SECURITY_CODE="{code}")'
                    f"(REPORT_DATE='{report_date}')"
                    '(ORG_TYPE="01")'
                ),
            },
        )
        result = obj.get("result") or {}
        if page == 1:
            pages = min(int(result.get("pages") or 1), MAX_HOLDING_PAGES)
        page_rows = list(result.get("data") or [])
        rows.extend(page_rows)
        if page >= pages or not page_rows:
            break
    etfs: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_etf_row(row):
            continue
        clean = _holding_row(row)
        previous = etfs.get(clean["code"])
        if previous is None or (clean.get("holding_value_yuan") or 0) > (
            previous.get("holding_value_yuan") or 0
        ):
            etfs[clean["code"]] = clean
    result_rows = sorted(
        etfs.values(),
        key=lambda row: (
            -(row.get("holding_weight_pct") or -1),
            -(row.get("holding_value_yuan") or -1),
            row["code"],
        ),
    )
    _write_cache(
        cache,
        {
            "fetched_at": _now().isoformat(timespec="seconds"),
            "code": code,
            "report_date": report_date,
            "rows": result_rows,
        },
    )
    return result_rows


def _symbol(code: str) -> str:
    return ("sh" if str(code).startswith(("5", "6", "9")) else "sz") + str(code)


def _fetch_kline(code: str, cache_root: pathlib.Path, target_t: str) -> list[list[Any]]:
    symbol = _symbol(code)
    cache = cache_root / "klines" / f"{symbol}.json"
    last_error: Exception | None = None
    for host in KLINE_HOSTS:
        try:
            obj = _request_with_retry(
                _json_request,
                host,
                {"param": f"{symbol},day,,,{KLINE_BARS},qfq"},
                attempts=2,
            )
            node = (obj.get("data") or {}).get(symbol) or {}
            raw = node.get("qfqday") or node.get("day") or []
            rows = [
                [str(row[0]), float(row[2])]
                for row in raw
                if len(row) >= 3 and _float(row[2]) is not None
            ]
            if len(rows) >= MIN_RETURN_DAYS + 1:
                _write_cache(
                    cache,
                    {"fetched_at": _now().isoformat(timespec="seconds"), "rows": rows},
                )
                return rows
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    # 腾讯三端点不可用时切到东方财富，避免同提供方单点失败。
    try:
        secid = ("1." if str(code).startswith(("5", "6", "9")) else "0.") + str(code)
        obj = _request_with_retry(
            _json_request,
            EASTMONEY_KLINE_API,
            {
                "secid": secid,
                "klt": 101,
                "fqt": 1,
                "lmt": KLINE_BARS,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            attempts=3,
        )
        raw = ((obj.get("data") or {}).get("klines") or [])
        rows = []
        for item in raw:
            parts = str(item).split(",")
            close = _float(parts[2] if len(parts) >= 3 else None)
            if close is not None:
                rows.append([parts[0], close])
        if len(rows) >= MIN_RETURN_DAYS + 1:
            _write_cache(cache, {"fetched_at": _now().isoformat(timespec="seconds"), "rows": rows})
            return rows
        raise RuntimeError("东方财富K线共同日不足")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{symbol} 腾讯/东方财富K线均不可用: 腾讯={last_error}; 东财={exc}") from exc


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left, mean_right = statistics.fmean(left), statistics.fmean(right)
    dx = [value - mean_left for value in left]
    dy = [value - mean_right for value in right]
    denominator = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(dx, dy)) / denominator))


def _similarity(stock_rows: list[list[Any]], etf_rows: list[list[Any]], target_t: str) -> dict[str, Any] | None:
    stock = {str(row[0]): float(row[1]) for row in stock_rows if str(row[0]) <= target_t}
    etf = {str(row[0]): float(row[1]) for row in etf_rows if str(row[0]) <= target_t}
    dates = sorted(set(stock).intersection(etf))[-(SIMILARITY_DAYS + 1) :]
    if len(dates) - 1 < MIN_RETURN_DAYS:
        return None
    stock_returns = [math.log(stock[right] / stock[left]) for left, right in zip(dates, dates[1:])]
    etf_returns = [math.log(etf[right] / etf[left]) for left, right in zip(dates, dates[1:])]
    correlation = _pearson(stock_returns, etf_returns)
    if correlation is None:
        return None
    stock_base, etf_base = stock[dates[0]], etf[dates[0]]
    stock_path = [stock[date] / stock_base * 100 for date in dates]
    etf_path = [etf[date] / etf_base * 100 for date in dates]
    rmse = math.sqrt(statistics.fmean((a - b) ** 2 for a, b in zip(stock_path, etf_path)))
    same_direction = statistics.fmean(
        1.0 if (a == 0 and b == 0) or a * b > 0 else 0.0
        for a, b in zip(stock_returns, etf_returns)
    )
    return {
        "correlation": round(correlation, 4),
        "path_rmse": round(rmse, 3),
        "common_return_days": len(dates) - 1,
        "window_start": dates[0],
        "window_end": dates[-1],
        "stock_return_pct": round((stock[dates[-1]] / stock_base - 1) * 100, 2),
        "etf_return_pct": round((etf[dates[-1]] / etf_base - 1) * 100, 2),
        "same_direction_pct": round(same_direction * 100, 1),
    }


def _blocked_payload(message: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": "blocked",
        "used_in_recommendation": False,
        "error": str(message)[:300],
        "all_etfs": [],
        "ranked": [],
    }


def enrich_bottom_result(
    result: dict[str, Any],
    cache_root: pathlib.Path,
    *,
    request_json: Callable[..., dict[str, Any]] = _json_request,
    similarity_max: int | None = None,
    html_top: int = DEFAULT_HTML_TOP,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """给候选写入结构化 ETF 字段；所有异常降级为信息不可用，不影响推荐。"""
    logger = log or (lambda _: None)
    similarity_max = int(
        similarity_max
        if similarity_max is not None
        else os.environ.get("BOTTOM_ETF_SIM_MAX", DEFAULT_SIMILARITY_MAX)
    )
    similarity_max = max(1, min(similarity_max, 300))
    top_meta = {
        "version": VERSION,
        "generated_at": _now().isoformat(timespec="seconds"),
        "used_in_recommendation": False,
        "similarity_metric": "截至T最近60个共同交易日的前复权日对数收益 Pearson 相关系数（降序）",
        "similarity_tie_break": "归一化收盘路径RMSE（升序）",
        "similarity_max_etfs_by_holding_weight": similarity_max,
        "html_top": html_top,
        "disclosure_note": "基金定期披露≠实时仓位；一/三季报通常非全部持仓，披露中报告可能不完整",
        "online_refresh": True,
        "recovery_policy": "每次裁定在线刷新；持仓四端点自动切换；行情腾讯三端点失败后切东方财富；不使用过期缓存兜底",
    }
    result["etf_holdings_meta"] = top_meta
    candidates = list(result.get("candidates") or [])
    if not candidates:
        top_meta["status"] = "no_candidates"
        return result
    try:
        period = _select_report_period(_report_periods(cache_root, request_json))
        top_meta.update(period)
        top_meta["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        top_meta["status"] = "blocked"
        top_meta["error"] = str(exc)[:300]
        for candidate in candidates:
            candidate["etf_holdings"] = _blocked_payload(exc)
        return result

    memory: dict[str, list[list[Any]]] = {}
    memory_lock = threading.Lock()

    def kline(code: str) -> list[list[Any]]:
        with memory_lock:
            cached = memory.get(code)
        if cached is not None:
            return cached
        rows = _fetch_kline(code, cache_root, str(result.get("T", "")))
        with memory_lock:
            memory[code] = rows
        return rows

    for candidate in candidates:
        code = str(candidate.get("code") or "")
        try:
            all_etfs = _stock_etfs(code, period["report_date"], cache_root, request_json)
            source_url = REPORT_PAGE.format(date=period["report_date"], code=code)
            payload: dict[str, Any] = {
                "version": VERSION,
                "status": "no_etf" if not all_etfs else "ok",
                "used_in_recommendation": False,
                **period,
                "source_name": "东方财富机构持仓公开披露",
                "source_url": source_url,
                "fetched_at": _now().isoformat(timespec="seconds"),
                "holding_etf_count": len(all_etfs),
                "similarity_universe_count": min(len(all_etfs), similarity_max),
                "similarity_success_count": 0,
                "similarity_window": {
                    "as_of_T": str(result.get("T", "")),
                    "target_return_days": SIMILARITY_DAYS,
                    "minimum_return_days": MIN_RETURN_DAYS,
                    "price_adjustment": "qfq",
                    "metric": top_meta["similarity_metric"],
                },
                "all_etfs": all_etfs,
                "ranked": [],
            }
            if not all_etfs:
                candidate["etf_holdings"] = payload
                continue
            stock_rows = kline(code)
            universe = all_etfs[:similarity_max]
            failures = 0

            def score(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
                try:
                    return row["code"], _similarity(stock_rows, kline(row["code"]), str(result.get("T", ""))), None
                except Exception as exc:  # noqa: BLE001
                    return row["code"], None, str(exc)[:160]

            workers = min(8, max(1, len(universe)))
            scored: dict[str, dict[str, Any]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                for etf_code, similarity, error in executor.map(score, universe):
                    if similarity is not None:
                        scored[etf_code] = similarity
                    elif error:
                        failures += 1
            for row in all_etfs:
                if row["code"] in scored:
                    row.update(scored[row["code"]])
                    row["similarity_status"] = "ok"
                elif row in universe:
                    row["similarity_status"] = "unavailable"
                else:
                    row["similarity_status"] = "not_computed_weight_cap"
            ranked = sorted(
                [row for row in all_etfs if row.get("similarity_status") == "ok"],
                key=lambda row: (
                    -float(row["correlation"]),
                    float(row["path_rmse"]),
                    -(row.get("holding_weight_pct") or -1),
                    row["code"],
                ),
            )
            for rank, row in enumerate(ranked, 1):
                row["similarity_rank"] = rank
            payload["ranked"] = ranked
            payload["similarity_success_count"] = len(ranked)
            payload["similarity_failure_count"] = failures
            payload["uncomputed_due_to_cap"] = max(0, len(all_etfs) - len(universe))
            if not ranked:
                payload["status"] = "partial"
            candidate["etf_holdings"] = payload
            logger(
                f"[bottom-etf] {code} 披露ETF{len(all_etfs)}只 · "
                f"相似度成功{len(ranked)}/{len(universe)}只"
            )
        except Exception as exc:  # noqa: BLE001
            candidate["etf_holdings"] = _blocked_payload(exc)
            logger(f"[bottom-etf] {code} 获取失败（不影响推荐）: {str(exc)[:100]}")
    return result


def _percent(value: Any) -> str:
    number = _float(value)
    return "—" if number is None else f"{number:.2f}%"


def _quote_url(code: str) -> str:
    return f"https://quote.eastmoney.com/{_symbol(code)}.html"


def _etf_section(code: str, payload: dict[str, Any], html_top: int) -> str:
    start = f"<!-- codex-bottom-etf:{code}:start -->"
    end = f"<!-- codex-bottom-etf:{code}:end -->"
    status = str(payload.get("status") or "blocked")
    if status == "blocked":
        message = html.escape(str(payload.get("error") or "公开持仓源暂不可用"))
        return (
            f"{start}<div class=etf-box data-bottom-etf-code='{code}'>"
            "<b>ETF持仓与走势相似度</b><div class=etf-note>"
            f"数据获取失败：{message}。仅信息区块降级，不影响股票推荐、裁定或仓位。</div></div>{end}"
        )
    report_date = html.escape(str(payload.get("report_date") or "—"))
    report_name = html.escape(str(payload.get("report_name") or report_date))
    newer = str(payload.get("newer_incomplete_report_name") or "")
    incomplete_note = (
        f"；较新的{html.escape(newer)}仍在披露中，为避免把不完整名单当全量，本区使用最近完整期"
        if newer
        else ""
    )
    source_url = html.escape(str(payload.get("source_url") or ""), quote=True)
    all_etfs = list(payload.get("all_etfs") or [])
    ranked = list(payload.get("ranked") or [])
    if not all_etfs:
        return (
            f"{start}<div class=etf-box data-bottom-etf-code='{code}'>"
            "<b>ETF持仓与走势相似度</b>"
            f"<div class=etf-note>{report_name}（{report_date}）未识别到场内ETF公开持仓；"
            "这不等于实时零持仓。基金定期披露，不参与股票推荐。</div></div>"
            f"{end}"
        )

    def row_html(row: dict[str, Any]) -> str:
        etf_code = str(row.get("code") or "")
        name = html.escape(str(row.get("name") or etf_code))
        link = html.escape(_quote_url(etf_code), quote=True)
        corr = _float(row.get("correlation"))
        corr_text = "—" if corr is None else f"{corr:.3f}"
        rank = row.get("similarity_rank") or "—"
        days = row.get("common_return_days") or "—"
        etf_ret = _percent(row.get("etf_return_pct"))
        return (
            f"<tr><td>{rank}</td><td><a href='{link}'>{etf_code} {name}</a></td>"
            f"<td><b>{corr_text}</b></td><td>{days}</td><td>{etf_ret}</td>"
            f"<td>{_percent(row.get('holding_weight_pct'))}</td></tr>"
        )

    visible = ranked[:html_top]
    visible_rows = "".join(row_html(row) for row in visible)
    cap_note = (
        f"按该股占ETF净值比例先取{payload.get('similarity_universe_count', 0)}只计算；"
        f"成功{payload.get('similarity_success_count', 0)}只"
    )
    source_link = f"<a href='{source_url}'>持仓来源</a>" if source_url else "持仓来源不可链接"
    return (
        f"{start}<div class=etf-box data-bottom-etf-code='{code}'>"
        "<div class=etf-title>ETF持仓与走势相似度 <span>仅信息展示·不改推荐</span></div>"
        f"<div class=etf-note>最新完整公开披露：{report_name}（{report_date}）{incomplete_note}。"
        f"共识别{payload.get('holding_etf_count', len(all_etfs))}只场内ETF；{cap_note}。"
        f"排序=截至股票T日最近60个共同交易日的前复权日收益Pearson相关（高→低），"
        f"同分再按归一化路径误差；{source_link}。定期披露≠实时仓位，一/三季报通常非全部持仓。</div>"
        "<div class=etf-scroll><table class=etf-table><thead><tr>"
        "<th>走势排名</th><th>ETF</th><th>相关</th><th>共同日</th><th>区间涨跌</th><th>持仓占净值</th>"
        f"</tr></thead><tbody>{visible_rows or '<tr><td colspan=6>相似度行情不足</td></tr>'}</tbody></table></div>"
        f"</div>{end}"
    )


_ETF_CSS = """
.etf-box{background:#eef7f4;border:1px solid #8fc7b7;border-radius:8px;padding:8px 10px;margin:7px 0}
.etf-title{font-weight:700;color:#176b58;margin-bottom:4px}.etf-title span{font-size:11px;font-weight:400;color:#687}
.etf-note{font-size:11.5px;line-height:1.55;color:#567;margin:3px 0 6px}.etf-note a{color:#1565c0}
.etf-scroll{overflow:auto;max-height:420px}.etf-table{border-collapse:collapse;width:100%;font-size:11.5px;background:#fff}
.etf-table th,.etf-table td{border:1px solid #d7e7e2;padding:4px 6px;text-align:right;white-space:nowrap}
.etf-table th:nth-child(2),.etf-table td:nth-child(2){text-align:left}.etf-table th{background:#dff0ea;color:#365;position:sticky;top:0}
.etf-table a{color:#1565c0;text-decoration:none}.etf-box details{margin-top:6px}.etf-box summary{font-size:11.5px;color:#176b58;cursor:pointer}
"""


def inject_etf_sections(raw: str, result: dict[str, Any]) -> str:
    """幂等地把 ETF 区块插入每张候选卡片的 F10 行之后。"""
    if VERSION not in str((result.get("etf_holdings_meta") or {}).get("version")):
        return raw
    if "codex-bottom-etf:" in raw:
        return raw
    if "</style>" in raw and ".etf-box{" not in raw:
        raw = raw.replace("</style>", _ETF_CSS + "</style>", 1)
    html_top = int((result.get("etf_holdings_meta") or {}).get("html_top") or DEFAULT_HTML_TOP)
    search_from = 0
    for candidate in result.get("candidates") or []:
        code = str(candidate.get("code") or "")
        start = raw.find("<div class=card", search_from)
        if start < 0:
            break
        next_card = raw.find("<div class=card", start + 1)
        observation = raw.find("<h3>观察池", start + 1)
        ends = [position for position in (next_card, observation) if position >= 0]
        end = min(ends) if ends else len(raw)
        block = raw[start:end]
        if code not in block:
            located = raw.find(code, start)
            if located < 0:
                continue
            start = raw.rfind("<div class=card", start, located + 1)
            next_card = raw.find("<div class=card", located)
            observation = raw.find("<h3>观察池", located)
            ends = [position for position in (next_card, observation) if position >= 0]
            end = min(ends) if ends else len(raw)
            block = raw[start:end]
        section = _etf_section(code, candidate.get("etf_holdings") or _blocked_payload("未生成ETF数据"), html_top)
        f10 = block.find("<div class=row>F10:")
        if f10 >= 0:
            insertion = block.find("</div>", f10)
            insertion = insertion + len("</div>") if insertion >= 0 else len(block)
        else:
            plan = block.find("<div class=plan>")
            insertion = plan if plan >= 0 else block.rfind("</div>")
            if insertion < 0:
                insertion = len(block)
        absolute = start + insertion
        raw = raw[:absolute] + section + raw[absolute:]
        search_from = absolute + len(section)
    return raw
