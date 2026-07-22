import sys
sys.path.insert(0, r"C:\Trading_analysis\Vibe-Trading-VT\claude_code_Trading_skill_no_API_Doc\weekly-ashare-rank")
import ashare_weekly_rank as A
df = A.get_spot(50)
print("列:", [c for c in df.columns.tolist()])
have = [c for c in ("代码","名称","市盈率","行业") if c in df.columns]
print(df[have].head(6).to_string())
print("扣非:", A.fetch_fundamentals(["300347","300759","300236"]))
