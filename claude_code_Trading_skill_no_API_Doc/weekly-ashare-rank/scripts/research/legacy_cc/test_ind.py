import sys
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as A
rows = A._clist_top(A._FS_ALL_A, "f12,f14,f100,f102,f103", fid="f6", pz=5, retries=2)
for r in rows:
    print(r.get("f12"), r.get("f14"), "| f100=",r.get("f100"), "| f102=",r.get("f102"), "| f103=",r.get("f103"))
