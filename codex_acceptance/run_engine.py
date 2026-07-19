#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 薄启动器：不改业务引擎，只做工作区适配。

用法：
  python run_engine.py bottom --
  python run_engine.py weekly -- --hold-days 3
  python run_engine.py weekly-gate --
  python run_engine.py stock -- --code 600519 --cost 1500

`--` 之后的参数原样交给原引擎。所有量化、阈值、数据源顺序和 renderer
仍由 hash 锁定的原文件执行；启动层只重定向缓存，并把 bottom 报告的历史
“T日+北京时间时分秒”混合文件名归一为完整 UTC+8 生成时间戳。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import re
import sys
from types import ModuleType


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SKILLS = REPO / "claude_code_Trading_skill_no_API_Doc"
CACHE = pathlib.Path(r"C:\Trading_analysis\data\cache\ashare_weekly")

ENTRIES = {
    "bottom": SKILLS / "bottom-fishing" / "bottom_fishing.py",
    "bottom-smoke": SKILLS / "bottom-fishing" / "bottom_fishing.py",
    "stock": SKILLS / "stock-diagnostic" / "stock_diagnostic.py",
    "stock-report": SKILLS / "stock-diagnostic" / "make_diag_report.py",
    "stock-recheck": SKILLS / "stock-diagnostic" / "recheck_diag.py",
    "weekly": SKILLS / "weekly-ashare-rank" / "ashare_weekly_rank.py",
    "weekly-gate": SKILLS / "weekly-ashare-rank" / "market_gate.py",
    "weekly-report": SKILLS / "weekly-ashare-rank" / "make_report.py",
    "weekly-recheck": SKILLS / "weekly-ashare-rank" / "recheck.py",
    "weekly-review": SKILLS / "weekly-ashare-rank" / "review.py",
}


def _load(path: pathlib.Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载引擎：{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _redirect_cache(mod: ModuleType, seen: set[int] | None = None) -> None:
    """递归找到引擎模块中的 WK/eng/diag，并只覆盖缓存目录常量。"""
    seen = seen or set()
    if id(mod) in seen:
        return
    seen.add(id(mod))
    if hasattr(mod, "_CACHE_DIR"):
        setattr(mod, "_CACHE_DIR", CACHE)
    for attr in ("WK", "eng", "diag"):
        child = getattr(mod, attr, None)
        if isinstance(child, ModuleType):
            _redirect_cache(child, seen)


def _bottom_report_t(path: pathlib.Path) -> str | None:
    """从 bottom HTML 读取业务截止日 T；文件名只承载生成时间，不再承载 T。"""
    try:
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"\bT=(\d{4}-\d{2}-\d{2})\b", html)
    return match.group(1) if match else None


def _install_bottom_cn_report_naming(mod: ModuleType) -> None:
    """强制 bottom 报告使用同一 UTC+8 时点中的完整日期和时分秒。

    原引擎文件名为 ``bottom_<T>_<北京时分秒>[_裁定版].html``，日期与时钟
    不是同一口径。启动层以结果中的 ``generated_at`` / ``adjudicated_at`` 为
    单一事实源，统一输出 ``bottom_cn_YYYY-MM-DD_HH-MM-SS[_裁定版].html``。
    """
    original = mod.render_html
    cn_tz = dt.timezone(dt.timedelta(hours=8))

    def render_html_cn(result: dict, *args, **kwargs):
        legacy_path = pathlib.Path(original(result, *args, **kwargs))
        field = "adjudicated_at" if result.get("adjudicated") else "generated_at"
        raw = result.get(field)
        if not raw:
            raise ValueError(f"bottom 报告缺少 {field}，拒绝生成非严格北京时间文件名")
        stamp = dt.datetime.fromisoformat(str(raw))
        if stamp.tzinfo is None:
            raise ValueError(f"bottom {field} 不含时区偏移：{raw}")
        stamp = stamp.astimezone(cn_tz)
        tag = "_裁定版" if result.get("adjudicated") else ""
        target = legacy_path.with_name(f"bottom_cn_{stamp.strftime('%Y-%m-%d_%H-%M-%S')}{tag}.html")
        if legacy_path != target:
            legacy_path.replace(target)

        # 裁定完成后仍保持原合同：同一 T 只留最新裁定版。T 从 HTML 正文读取，
        # 不再污染严格的生成时间戳文件名。
        if result.get("adjudicated"):
            for old in target.parent.glob("bottom_cn_*.html"):
                if old != target and _bottom_report_t(old) == result.get("T"):
                    old.unlink()
        return target

    mod.render_html = render_html_cn


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ENTRIES:
        names = ", ".join(ENTRIES)
        print(f"用法：python run_engine.py <entry> -- [原引擎参数]\nentry: {names}", file=sys.stderr)
        return 2
    entry = sys.argv[1]
    passthrough = sys.argv[2:]
    if passthrough[:1] == ["--"]:
        passthrough = passthrough[1:]
    forbidden = {"--no-verify", "--no-notices"}.intersection(passthrough)
    if forbidden:
        print(f"Codex workflow 禁止跳过硬校验参数：{', '.join(sorted(forbidden))}", file=sys.stderr)
        return 2
    path = ENTRIES[entry]
    if not path.is_file():
        raise FileNotFoundError(path)

    CACHE.mkdir(parents=True, exist_ok=True)
    mod = _load(path, f"codex_entry_{entry.replace('-', '_')}")
    _redirect_cache(mod)
    if entry in {"bottom", "bottom-smoke"}:
        _install_bottom_cn_report_naming(mod)
    sys.argv = [str(path), *passthrough]

    if entry == "bottom-smoke":
        smoke = pathlib.Path(r"C:\Trading_analysis\data\codex_smoke\bottom")
        smoke.mkdir(parents=True, exist_ok=True)
        mod.DATA = smoke
        mod.SHADOW = smoke / "bottom_shadow_log.jsonl"
        mod.OUT_JSON = smoke / "bottom_latest.json"
        mod.ADJUD = smoke / "bottom_adjudication.json"
        mod.REPORTS = smoke / "reports"
        if passthrough:
            print("bottom-smoke 不接受原引擎参数", file=sys.stderr)
            return 2
        mod.scan()
    elif entry == "bottom":
        if not passthrough:
            mod.scan()
        elif passthrough in (["--help"], ["-h"]):
            print("usage: bottom_fishing.py [-h] [--review] [--adjudicate]\n\n"
                  "options:\n  -h, --help     show this help message and exit\n"
                  "  --review        对账影子日志\n  --adjudicate   合并裁定并重排重出HTML")
        elif passthrough == ["--review"]:
            mod.review()
        elif passthrough == ["--adjudicate"]:
            mod.adjudicate()
        else:
            print("bottom 仅接受 --review 或 --adjudicate", file=sys.stderr)
            return 2
    else:
        mod.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
