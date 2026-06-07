#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由诊断结果 JSON 渲染完整版 HTML 报告（文件名后缀=中国当地时间）。

用途：引擎已自动出一份量化预览报告；做完 Agent②~⑤ 后，把结论回填进 JSON
（final_action / final_confidence / agent_scores / agents_md / final_md），
用本脚本生成**完整版**报告（含五方辩论与裁决章节）。

用法:
  python make_diag_report.py --in C:\\Trading_analysis\\data\\diag_latest.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("diag", HERE / "stock_diagnostic.py")
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)


def main() -> None:
    ap = argparse.ArgumentParser(description="由诊断JSON渲染HTML报告(中国时间命名)")
    ap.add_argument("--in", dest="inp",
                    default=r"C:\Trading_analysis\data\diag_latest.json", help="诊断结果JSON路径")
    ap.add_argument("--report-dir", default=None, help="报告输出目录(默认 reports/)")
    args = ap.parse_args()

    p = pathlib.Path(args.inp)
    if not p.exists():
        print(f"找不到 {p}")
        return
    result = json.loads(p.read_text(encoding="utf-8"))
    out = diag.render_html(result, args.report_dir, is_final=True)  # 完整版：永久保留、不覆盖旧的完整报告
    print(f"📄 HTML完整诊断报告已保存(每次另存,不覆盖): {out}")


if __name__ == "__main__":
    main()
