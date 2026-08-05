import sys
sys.path.insert(0, r"C:\apps\padb\tools")
import padb_plots as pp
from pathlib import Path

csv = Path(r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\v2_probe_results\padb\Harmonics_Env_Dataset.csv")
df = pp._load_scatter_for_stats(csv)
df = pp._parse_group_fields(df)

print("Unique Upper_Limit values:", sorted(df["Upper_Limit"].dropna().unique()))
print("First Upper_Limit:", df["Upper_Limit"].dropna().iloc[0])
print("First few rows Upper_Limit:", df["Upper_Limit"].head(10).tolist())
