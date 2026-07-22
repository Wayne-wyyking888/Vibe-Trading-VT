# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import market_gate as M
idx = {"sh000001": {"above_ma20": True, "chg": 1.16, "heavy_sell": False, "down_streak": 0, "vol_ratio": 0.96},
       "sz399006": {"above_ma20": True, "chg": 0.54, "heavy_sell": False, "down_streak": 0, "vol_ratio": 0.98}}
# 06-29 真实画像：涨停107 vs 前日60(1.78倍激增)、炸板26.2%
senti = {"zt_count": 107, "zt_prev": 60, "zb_rate": 26.2, "dt_count": 0, "max_streak": 3, "zt_shrink": False}
r = M.assess(idx, senti)
print(f"[06-29高潮] 环境分={r['score']} regime={r['regime']}")
print("  M5命中:", [x for x in r["reasons"] if "激增" in x])
# 对照：温和普涨(涨停70 vs 前日65，不激增)
r2 = M.assess(idx, dict(senti, zt_count=70, zt_prev=65))
print(f"[温和对照] 环境分={r2['score']} regime={r2['regime']} (应无激增惩罚、分更高)")
