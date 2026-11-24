"""
Verify clock leakage stat_summary statistics against raw CSV.
Replicates _aggregate_stat_data logic exactly: Room temp, group by condition x freq,
compute mean/std/TLL using k-factor (one-sided normal tolerance interval).
"""
import math
import re
import sys
import numpy as np
import pandas as pd
from scipy.stats import nct, norm

sys.path.insert(0, r"C:\apps\padb\tools")

P = 0.95   # default proportion
C = 0.90   # default confidence

def k_factor(n, p=P, c=C):
    """One-sided normal tolerance interval k-factor (same formula as _build_k_table)."""
    try:
        return float(nct.ppf(c, df=n-1, nc=norm.ppf(p)*math.sqrt(n)) / math.sqrt(n))
    except Exception:
        return float(norm.ppf(p) + norm.ppf(c)*math.sqrt(1/n + 0.5*norm.ppf(p)**2/(n-1)))


# ---------------------------------------------------------------------------
# Load CSV
# ---------------------------------------------------------------------------
CSV = r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\NonHarmonics_Clock_leakage_Env_Dataset2.csv"
df = pd.read_csv(CSV)
df.columns = [c.strip() for c in df.columns]

val_col  = "SpectralMiniMoabClkSpursPowerControl (dBc)"
freq_col = "Frequency (MHz)"
grp_col  = "Group"
temp_col = "Test Step"
spec_col = "Upper Limit (<=)"

print(f"Total rows: {len(df):,}")

# Parse _grp_ columns from Group field
for g in df[grp_col].dropna().unique():
    for part in g.split("  "):
        kv = part.strip().split(": ", 1)
        if len(kv) == 2:
            key = f"_grp_{kv[0].strip()}"
            if key not in df.columns:
                df[key] = None

grp_keys = [c for c in df.columns if c.startswith("_grp_")]
for gc in grp_keys:
    name = gc.removeprefix("_grp_")
    df[gc] = df[grp_col].str.extract(rf"(?:^|  ){re.escape(name)}: ([^  ]+)", expand=False)

# Filter to Room temp
room = df[df[temp_col].str.strip().str.lower() == "room"].copy()
print(f"Room rows : {len(room):,}")

# Identify condition columns (same logic as padb_plots.py render_summary / _aggregate_stat_data)
serial_kws = ("serial", "unit id", "dut id", "s/n")
temp_kws   = ("temp", "temperature", "deg c", "deg f")
path_pat   = re.compile(r"^(rf|path|port|ch|channel)\s*\d*$", re.IGNORECASE)
serial_pat = re.compile(r"^[A-Z]{2,4}\d{4,}$")

all_grp = [c for c in grp_keys if room[c].nunique(dropna=True) >= 2]

def is_exclude(col):
    name = col.removeprefix("_grp_").lower()
    if any(kw in name for kw in serial_kws + temp_kws):
        return True
    vals = room[col].dropna().unique()
    if not len(vals): return False
    if all(serial_pat.match(str(v)) for v in vals): return True
    return False

cond_cols = [c for c in all_grp if not is_exclude(c)]
# Include port/path cols
for col in all_grp:
    if col in cond_cols: continue
    vals = room[col].dropna().unique()
    if 1 < len(vals) <= 20 and all(path_pat.match(str(v)) for v in vals):
        cond_cols.append(col)

_ser_col = next((c for c in all_grp if any(kw in c.removeprefix("_grp_").lower() for kw in serial_kws)), None)

print(f"Cond cols : {[c.removeprefix('_grp_') for c in cond_cols]}")
print(f"Serial col: {_ser_col}")

def cond_label(row):
    parts = [f"{c.removeprefix('_grp_')}: {row[c]}" for c in cond_cols if pd.notna(row.get(c))]
    return "  ".join(parts) if parts else "All"

room["_cond"] = room.apply(cond_label, axis=1)

print(f"\nConditions: {sorted(room['_cond'].unique())}")
print(f"Freq count: {room[freq_col].nunique()}")

# ---------------------------------------------------------------------------
# Per-condition per-freq stats — one value per DUT (same as padb_plots.py)
# ---------------------------------------------------------------------------
rows = []
for cond, cdf in room.groupby("_cond", sort=True):
    spec_modal = cdf[spec_col].dropna().mode()
    spec_hi = float(spec_modal.iloc[0]) if len(spec_modal) else None

    for freq in sorted(cdf[freq_col].dropna().unique()):
        fdf = cdf[cdf[freq_col] == freq]
        if _ser_col:
            dut_vals = fdf.groupby(_ser_col)[val_col].mean()
        else:
            dut_vals = fdf[val_col].dropna()
        vals = dut_vals.dropna().values
        n = len(vals)
        if n < 2:
            continue
        mean_v = float(np.mean(vals))
        std_v  = float(np.std(vals, ddof=1))
        k = k_factor(n, P, C)
        tll_hi = mean_v + k * std_v

        freq_spec = fdf[spec_col].dropna().mode()
        freq_spec_hi = float(freq_spec.iloc[0]) if len(freq_spec) else spec_hi

        margin = (freq_spec_hi - tll_hi) if freq_spec_hi is not None else None
        rows.append(dict(
            cond=cond, freq=float(freq), n=n,
            mean=mean_v, std=std_v, k=k,
            tll_hi=tll_hi, spec=freq_spec_hi, margin=margin,
            dut_list=list(dut_vals.items()) if _ser_col else []
        ))

out = pd.DataFrame(rows)
failures = out[out["margin"] < 0] if "margin" in out.columns else pd.DataFrame()

print(f"\n{'='*70}")
print(f"Total freq-cond combinations : {len(out)}")
print(f"Failures (TLL_hi > Spec)     : {len(failures)}")
print(f"{'='*70}")

if not failures.empty:
    print("\nFailing combinations:")
    print(f"  {'Condition':<55} {'Freq':>8} {'n':>3} {'Mean':>8} {'Std':>5} {'k':>5} {'TLL_hi':>8} {'Spec':>8} {'Margin':>7}")
    for _, r in failures.iterrows():
        print(f"  {r['cond']:<55} {r['freq']:>8.3f} {r['n']:>3d} "
              f"{r['mean']:>8.2f} {r['std']:>5.2f} {r['k']:>5.3f} "
              f"{r['tll_hi']:>8.2f} {r['spec']:>8.1f} {r['margin']:>+7.2f}")

# Show worst margin per condition
print("\n--- Worst margin per condition ---")
worst = out.groupby("cond").apply(
    lambda g: g.loc[g["margin"].idxmin()] if g["margin"].notna().any() else g.iloc[0],
    include_groups=False
).reset_index()
worst = worst.sort_values("margin")
for _, r in worst.iterrows():
    flag = " *** FAIL" if pd.notna(r.get("margin")) and r["margin"] < 0 else ""
    print(f"  {r['cond']:<55}  margin={r['margin']:>+7.2f}  "
          f"@{r['freq']:.3f}MHz  n={r['n']:.0f}  std={r['std']:.3f}{flag}")

# Show n per condition (summary)
print("\n--- DUT count per condition ---")
for cond, cdf in out.groupby("cond"):
    ns = cdf["n"].unique()
    print(f"  {cond:<55}  n={sorted(ns)}")
