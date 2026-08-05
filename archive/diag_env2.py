"""Trace env_coverage UDE/LDE computation exactly as the JS does, for verification."""
import pandas as pd, numpy as np, sys
from scipy.stats import nct, norm
sys.path.insert(0, r"C:\apps\padb\tools")

CSV = r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\closein_env_results\padb\Non_Harmonics_Close_In_Env_Dataset.csv"
TARGET = "Far-offset 7.032MHz high"
P_env, C_env = 0.95, 0.90

def k_one_sided(n, P, C):
    """Same as padb_plots._k_one_sided — used by env_coverage kLookup."""
    import math
    k = nct.ppf(C, df=n - 1, nc=norm.ppf(P) * math.sqrt(n)) / math.sqrt(n)
    return float(k)

# k-factor values for reference
for n in [4, 5, 6, 9, 11]:
    k = k_one_sided(n, P_env, C_env)
    print(f"k-factor: n={n}, P={P_env}, C={C_env} -> k = {k:.4f}")
print()

df = pd.read_csv(CSV, dtype=str)
df["SpurType"] = df["Group"].str.extract(r"SpurType:\s*(.+)")
df["Serial"]   = df["Group"].str.extract(r"Serial Number:\s*(\S+)")
df["Port"]     = df["Group"].str.extract(r"Port:\s*(\S+)")
df["Freq_MHz"] = pd.to_numeric(df["Frequency (MHz)"], errors="coerce")
df["Value"]    = pd.to_numeric(df["SpectralMiniMoabCloseInSpursPowerControl (dBc)"], errors="coerce")
df["Temp"]     = df["Test Step"].str.strip()
df["DUT_key"]  = df["Serial"] + "_" + df["Port"]

filt = df[df["SpurType"] == TARGET].copy()
room_df = filt[filt["Temp"] == "Room"]
env_df  = filt[filt["Temp"] != "Room"]

# Pivot using DUT_key (serial+port) as index — same as how JS dut_key works
rp = room_df.pivot_table(index="DUT_key", columns="Freq_MHz", values="Value", aggfunc="first")
print(f"Room DUTs: {list(rp.index)}")
print()

# Pick a sample frequency near 7.032 MHz for close inspection
sample_freqs = sorted(filt["Freq_MHz"].dropna().unique())
# take 3 representative ones
check_freqs = [sample_freqs[0], sample_freqs[len(sample_freqs)//2], sample_freqs[-1]]

print("=== Per-temperature delta stats and TI for sample frequencies ===")
print(f"{'Freq':>12}  {'Temp':>15}  {'n':>3}  {'mean_delta':>10}  {'std_delta':>10}  {'k':>6}  {'u=mu+k*s':>10}  {'l=mu-k*s':>10}  {'LDE=max(0,-l)':>14}")

for f in check_freqs:
    for temp in sorted(env_df["Temp"].unique()):
        tdf = env_df[env_df["Temp"] == temp]
        tp  = tdf.pivot_table(index="DUT_key", columns="Freq_MHz", values="Value", aggfunc="first")
        common = rp.index.intersection(tp.index)
        if f not in rp.columns or f not in tp.columns or len(common) < 2:
            continue
        dv = tp.loc[common, f].values - rp.loc[common, f].values
        dv = dv[~np.isnan(dv)]
        n = len(dv)
        mu = float(np.mean(dv))
        sd = float(np.std(dv, ddof=1))
        k  = k_one_sided(n, P_env, C_env)
        u  = mu + k * sd
        l  = mu - k * sd
        lde = max(0.0, -l)
        print(f"{f:>12.4f}  {temp:>15}  {n:>3}  {mu:>+10.4f}  {sd:>10.4f}  {k:>6.4f}  {u:>+10.4f}  {l:>+10.4f}  {lde:>14.4f}")
    print()

print("=== Final UDE/LDE (worst-case across all temps) for all freqs ===")
print(f"{'Freq':>12}  {'UDE':>8}  {'LDE':>8}  (UDE+LDE = total range)")
for f in sorted(filt["Freq_MHz"].dropna().unique()):
    u_j = None
    l_j = None
    for temp in sorted(env_df["Temp"].unique()):
        tdf = env_df[env_df["Temp"] == temp]
        tp  = tdf.pivot_table(index="DUT_key", columns="Freq_MHz", values="Value", aggfunc="first")
        common = rp.index.intersection(tp.index)
        if f not in rp.columns or f not in tp.columns or len(common) < 2:
            continue
        dv = tp.loc[common, f].values - rp.loc[common, f].values
        dv = dv[~np.isnan(dv)]
        n = len(dv)
        if n < 2:
            continue
        mu = float(np.mean(dv))
        sd = float(np.std(dv, ddof=1))
        k  = k_one_sided(n, P_env, C_env)
        u  = mu + k * sd
        l  = mu - k * sd
        lde = max(0.0, -l)
        if u_j is None or u > u_j: u_j = u
        if l_j is None or lde > l_j: l_j = lde
    if u_j is not None and l_j is not None:
        print(f"{f:>12.4f}  {u_j:>+8.4f}  {l_j:>8.4f}  ({u_j + l_j:.2f} dB total)")
