#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由结果 JSON 渲染 HTML 报告（文件名后缀=中国当地时间）。

用途：选股引擎已自动出一份量化报告；做完 Agent②(催化剂)/Agent③(裁决) 后，
把结论写进 JSON 的 catalysts_md / final_md 字段，用本脚本生成**完整版**报告。

用法:
  python make_report.py --in C:\\Trading_analysis\\data\\rank_latest.json
  python make_report.py --in result_enriched.json --report-dir <目录>

可选：JSON 里加 "catalysts_md"(字符串) 和 "final_md"(字符串) → 报告会附消息面/裁决章节。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("eng", HERE / "ashare_weekly_rank.py")
eng = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eng)


def main() -> None:
    ap = argparse.ArgumentParser(description="由结果JSON渲染HTML报告(中国时间命名)")
    ap.add_argument("--in", dest="inp",
                    default=r"C:\Trading_analysis\data\rank_latest.json", help="结果JSON路径")
    ap.add_argument("--report-dir", default=None, help="报告输出目录(默认 reports/)")
    args = ap.parse_args()

    p = pathlib.Path(args.inp)
    if not p.exists():
        print(f"找不到 {p}")
        return
    result = json.loads(p.read_text(encoding="utf-8"))
    out = eng.render_html(result, args.report_dir)  # preview=False → 完整版+清掉预览中间文件
    print(f"📄 完整版HTML报告已保存: {out}（已清除引擎预览中间文件）")


if __name__ == "__main__":
    main()
