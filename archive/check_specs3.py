import sys
sys.path.insert(0, r"C:\apps\padb\tools")
import padb_plots as pp
from pathlib import Path

csv = Path(r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\v2_probe_results\padb\Harmonics_Env_Dataset.csv")
df = pp._load_scatter_for_stats(csv)
df = pp._parse_group_fields(df)

bad = df[df["Upper_Limit"] == -60.0]
print(f"Rows with Upper_Limit=-60: {len(bad)}")
if "_grp_HarmonicNumber" in bad.columns:
    print("\nBy Harmonic:")
    print(bad.groupby("_grp_HarmonicNumber")["Upper_Limit"].count())
    print("\nUnique freq+harmonic combos (sample):")
    sample = bad[["_grp_HarmonicNumber","Frequency_MHz"]].drop_duplicates().sort_values(["_grp_HarmonicNumber","Frequency_MHz"])
    print(sample.to_string(index=False))
