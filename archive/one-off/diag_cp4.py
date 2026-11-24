"""Trace _aggregate_env_coverage_data output for carrier power CSV."""
import sys
sys.path.insert(0, r"C:\apps\padb\tools")
import padb_plots as _pp
from pathlib import Path
import pandas as pd

csv = Path(r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\Non_Harmonics_Close_In_CarrierPower_Dataset.csv")
cfg = {"room_values": ["Room"], "freq_scale": 0.000001}

df = _pp._load_scatter_for_stats(csv)
df = _pp._parse_group_fields(df)

# Apply freq scale
df["Frequency_MHz"] = df["Frequency_MHz"] * 0.000001

print(f"Loaded {len(df)} rows")
print(f"Freq range: {df['Frequency_MHz'].min():.4f} to {df['Frequency_MHz'].max():.4f} MHz")
print(f"Temps: {sorted(df['Temperature'].unique())}")
print(f"Groups: {sorted(df['Group'].unique())[:3]} ... ({df['Group'].nunique()} total)")

grp_cols = [c for c in df.columns if c.startswith("_grp_")]
print(f"_grp_ cols: {grp_cols}")
print()

env_data, cond_dims, non_room_temps, all_serials, all_ports = _pp._aggregate_env_coverage_data(df, cfg)
print(f"Conditions: {len(env_data)}")
for cd in env_data:
    freqs = cd["freqs"]
    duts = cd["duts"]
    print(f"  Condition: {cd['condition']}")
    print(f"    Freqs: {len(freqs)}  Min={min(freqs):.4f} Max={max(freqs):.4f}")
    print(f"    DUTs: {len(duts)}")
    # Check how many deltas are non-null per DUT
    for dut_key, dut in list(duts.items())[:2]:
        print(f"    DUT {dut_key}: temps with deltas={list(dut['deltas'].keys())}")
        for temp, dvals in dut['deltas'].items():
            non_null = sum(1 for v in dvals if v is not None)
            print(f"      {temp}: {non_null}/{len(dvals)} non-null delta freqs")
