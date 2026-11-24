"""Diagnostic: verify env_coverage UDE/LDE for Far-offset 7.032MHz high."""
import pandas as pd, numpy as np, scipy.stats as st, sys
sys.path.insert(0, r"C:\apps\padb\tools")

CSV = r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\closein_env_results\padb\Non_Harmonics_Close_In_Env_Dataset.csv"
TARGET_SPUR = "Far-offset 7.032MHz high"
P, C = 0.95, 0.90

df = pd.read_csv(CSV, dtype=str)
df["SpurType"] = df["Group"].str.extract(r"SpurType:\s*(.+)")
df["Serial"]   = df["Group"].str.extract(r"Serial Number:\s*(\S+)")
df["Port"]     = df["Group"].str.extract(r"Port:\s*(\S+)")
df["Freq_MHz"] = pd.to_numeric(df["Frequency (MHz)"], errors="coerce")
df["Value"]    = pd.to_numeric(df["SpectralMiniMoabCloseInSpursPowerControl (dBc)"], errors="coerce")
df["Temp"]     = df["Test Step"].str.strip()

filt = df[(df["SpurType"] == TARGET_SPUR) & (df["Port"] == "RF1")]
print(f"Rows (RF1, {TARGET_SPUR}): {len(filt)}")
print(f"Temps: {sorted(filt['Temp'].unique())}")
print(f"Serials: {sorted(filt['Serial'].dropna().unique())}")
print(f"Freqs unique: {len(filt['Freq_MHz'].dropna().unique())}")
print()

room_df = filt[filt["Temp"] == "Room"]
env_df  = filt[filt["Temp"] != "Room"]

rp = room_df.pivot_table(index="Serial", columns="Freq_MHz", values="Value", aggfunc="first")
print(f"Room DUTs: {list(rp.index)}")
print(f"Room freq count: {len(rp.columns)}")
print()

def k_factor_two_sided(n, P, C):
    """Two-sided non-central t k-factor for tolerance interval."""
    nu = n - 1
    z_p = st.norm.ppf((1 + P) / 2)
    ncp = np.sqrt(n) * z_p
    t_c = st.t.ppf(C, nu, nc=ncp)
    return t_c / np.sqrt(n)

# Collect per-DUT per-freq deltas, pooled across all temps
all_deltas = {}  # freq -> list of delta values
per_temp_info = []

for temp in sorted(env_df["Temp"].unique()):
    tdf = env_df[env_df["Temp"] == temp]
    tp  = tdf.pivot_table(index="Serial", columns="Freq_MHz", values="Value", aggfunc="first")
    common_s = rp.index.intersection(tp.index)
    common_f = rp.columns.intersection(tp.columns)
    n_duts = len(common_s)
    delta_mat = tp.loc[common_s, common_f].values - rp.loc[common_s, common_f].values
    print(f"Temp={temp}: {n_duts} matching DUTs, {len(common_f)} freqs")
    if n_duts > 0:
        # Show sample deltas for first 3 freqs
        sample_f = list(common_f[:3])
        for i, sf in enumerate(sample_f):
            col_deltas = delta_mat[:, i]
            valid = col_deltas[~np.isnan(col_deltas)]
            print(f"  Freq {sf:.4f}: deltas={[f'{v:+.2f}' for v in valid]}")
    for i, f in enumerate(common_f):
        col_d = delta_mat[:, i]
        valid = col_d[~np.isnan(col_d)]
        if len(valid):
            all_deltas.setdefault(f, []).extend(valid.tolist())
    per_temp_info.append((temp, n_duts))

print()
print(f"=== Per-freq pooled delta stats (P={P}, C={C}) ===")
print(f"{'Freq':>12}  {'n':>3}  {'mean':>7}  {'std':>7}  {'k':>6}  {'UDE':>8}  {'LDE':>8}  {'half-width':>10}")
for f in sorted(all_deltas):
    vals = np.array(all_deltas[f])
    n = len(vals)
    mu = float(np.mean(vals))
    sd = float(np.std(vals, ddof=1)) if n > 1 else 0.0
    if n > 1:
        k = k_factor_two_sided(n, P, C)
        ude = mu + k * sd
        lde = mu - k * sd
    else:
        k = float("nan")
        ude = lde = mu
    print(f"{f:>12.4f}  {n:>3}  {mu:>+7.3f}  {sd:>7.3f}  {k:>6.3f}  {ude:>+8.3f}  {lde:>+8.3f}  {k*sd:>10.3f}")

print()
print("=== Absolute spur level summary (all temps inc room) ===")
abs_pivot = filt.pivot_table(index="Temp", columns="Freq_MHz", values="Value", aggfunc=["mean","std","count"])
for f in sorted(filt["Freq_MHz"].dropna().unique()):
    for temp in sorted(filt["Temp"].unique()):
        sub = filt[(filt["Temp"]==temp) & (filt["Freq_MHz"]==f)]["Value"].dropna()
        if len(sub):
            print(f"  Freq={f:.4f} Temp={temp:15s}  n={len(sub):2d}  mean={sub.mean():+.3f}  std={sub.std(ddof=1):.3f}")
