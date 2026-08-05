import csv

path = r"C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\v2_probe_results\padb\Harmonics_Env_Dataset.csv"
bad = set()
with open(path, newline="", encoding="utf-8-sig") as f:
    rdr = csv.DictReader(f)
    for row in rdr:
        ul = row.get("Upper_Limit", "")
        if not ul:
            continue
        try:
            uv = float(ul)
        except ValueError:
            continue
        if round(uv, 4) not in (-30.0, -55.0):
            h = row.get("_grp_HarmonicNumber", "?")
            freq = row.get("Frequency_MHz", "?")
            bad.add((h, round(float(freq), 2), round(uv, 4)))

if bad:
    print(f"Found {len(bad)} unexpected spec values:")
    for item in sorted(bad):
        print(f"  Harmonic={item[0]}  Freq={item[1]}  Spec={item[2]}")
else:
    print("All Upper_Limit values are -30.0 or -55.0 (or null).")
