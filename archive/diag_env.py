"""Diagnostic: check why 220KHz spur type shows ΔEnv n=0 in env_coverage."""
import sys
sys.path.insert(0, r"C:\apps\padb\tools")
import padb_plots as _pp

csv_path = r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\closein_env_results\padb\Non_Harmonics_Close_In_Env_Dataset.csv"
from pathlib import Path
df = _pp._load_scatter_for_stats(Path(csv_path))
df = _pp._parse_group_fields(df)

print(f"Total rows: {len(df)}")
print(f"Temperatures: {sorted(df['Temperature'].dropna().unique().tolist())}")
grp_cols = [c for c in df.columns if c.startswith("_grp_")]
print(f"Group cols: {grp_cols}")

# Find SpurType column
spur_col = next((c for c in grp_cols if "SpurType" in c or "spur" in c.lower()), None)
print(f"SpurType col: {spur_col}")
if spur_col:
    print(f"SpurType values: {sorted(str(v) for v in df[spur_col].dropna().unique())}")

# Build super_cond as _aggregate_env_coverage_data does
_serial_kws = ("serial", "unit id", "dut id", "s/n")
_port_kws = ("port",)
serial_cols = {c for c in grp_cols if any(kw in c.removeprefix("_grp_").lower() for kw in _serial_kws)}
port_cols = {c for c in grp_cols if c not in serial_cols and any(kw in c.removeprefix("_grp_").lower() for kw in _port_kws)}
cond_cols = [c for c in grp_cols if c not in serial_cols and c not in port_cols and 1 < df[c].nunique(dropna=True) <= 50]
print(f"\nSerial cols: {serial_cols}")
print(f"Port cols: {port_cols}")
print(f"Cond cols: {cond_cols}")

def _super_label(row):
    parts = [f"{c.removeprefix('_grp_')}: {row[c]}" for c in cond_cols if __import__('pandas').notna(row[c])]
    return "  ".join(parts) if parts else "All"

import pandas as pd
df["_super_cond"] = df.apply(_super_label, axis=1)
conds_220 = sorted(c for c in df["_super_cond"].unique() if "220" in str(c).lower() or "220" in str(c))
print(f"\nConditions with '220': {conds_220[:10]}")

# Check each 220 condition
room_values = ["Room"]
for sc in conds_220[:3]:
    sc_df = df[df["_super_cond"] == sc]
    temps = sorted(sc_df["Temperature"].dropna().unique().tolist())
    print(f"\n--- {sc} ---")
    print(f"  Temps: {temps}")

    room_df = sc_df[sc_df["Temperature"].isin(room_values)]
    non_room_temps = [t for t in temps if t not in room_values]
    print(f"  Room rows: {len(room_df)}, Non-room temps: {non_room_temps}")

    if len(room_df) == 0:
        print("  >> NO ROOM DATA!")
        continue

    # Build pivots
    room_pivot = room_df.pivot_table(index="Group", columns="Frequency_MHz", values="Value", aggfunc="first")
    print(f"  Room pivot: {len(room_pivot)} groups x {len(room_pivot.columns)} freqs")
    print(f"  Room groups sample: {list(room_pivot.index)[:3]}")

    for temp in non_room_temps[:3]:
        tdf = sc_df[sc_df["Temperature"] == temp]
        if not len(tdf):
            print(f"  [{temp}] No data")
            continue
        tp = tdf.pivot_table(index="Group", columns="Frequency_MHz", values="Value", aggfunc="first")
        print(f"  [{temp}] pivot: {len(tp)} groups x {len(tp.columns)} freqs")

        # Check overlap
        room_grps = set(room_pivot.index)
        tp_grps = set(tp.index)
        overlap = room_grps & tp_grps
        print(f"  [{temp}] Group overlap: {len(overlap)}/{len(room_grps)} room groups also in non-room")

        if not overlap:
            print(f"  [{temp}] >> ZERO GROUP OVERLAP - this is the bug!")
            print(f"    Room sample: {list(room_grps)[:3]}")
            print(f"    Non-room sample: {list(tp_grps)[:3]}")
        else:
            # Check freq overlap for overlapping groups
            grp = list(overlap)[0]
            room_freqs = set(room_pivot.columns)
            tp_freqs = set(tp.columns)
            freq_overlap = room_freqs & tp_freqs
            print(f"  [{temp}] Freq overlap: {len(freq_overlap)}/{len(room_freqs)} freqs")

            # Count deltas
            n_deltas = 0
            for g in overlap:
                for f in freq_overlap:
                    rv = room_pivot.at[g, f] if g in room_pivot.index and f in room_pivot.columns else None
                    tv = tp.at[g, f] if g in tp.index and f in tp.columns else None
                    if rv is not None and tv is not None and str(rv) not in ('nan','') and str(tv) not in ('nan',''):
                        n_deltas += 1
            print(f"  [{temp}] Computable deltas: {n_deltas}")
