import sys, json, re
sys.path.insert(0, r"C:\apps\padb\tools")
import padb_plots as pp
from pathlib import Path

csv = Path(r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\v2_probe_results\padb\Harmonics_Env_Dataset.csv")
df = pp._load_scatter_for_stats(csv)
df = pp._parse_group_fields(df)
cfg = {"proportion": 0.90, "confidence": 0.90}
stat_data = pp._aggregate_stat_data(df, cfg)

# Show all unique spec_up values per harmonic condition
spec_by_harm = {}
for cd in stat_data:
    m = re.search(r'HarmonicNumber[:\s]+([\S]+)', cd["condition"])
    harm = m.group(1) if m else "?"
    specs = set()
    for fs in cd.get("freq_stats", []):
        if fs.get("spec_up") is not None:
            specs.add(round(fs["spec_up"], 2))
    if specs:
        spec_by_harm.setdefault(harm, set()).update(specs)

print("Spec values per HarmonicNumber in STAT_DATA:")
for h in sorted(spec_by_harm.keys(), key=lambda x: float(x) if x != "?" else 999):
    print(f"  Harmonic {h}: {sorted(spec_by_harm[h])}")
