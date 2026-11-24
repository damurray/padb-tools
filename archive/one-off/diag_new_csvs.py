import pandas as pd
base = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots'
files = [
    'NonHarmonics_line_related_Env_Dataset.csv',
    'Non_Harmonics_Line_Related_Env_Dataset.csv',
    'Non_Harmonics_Line_Related_CarrierPower_Env_Dataset.csv',
    'NonHarmonics_Clock_leakage_Env_Dataset.csv',
    'NonHarmonics_Clock_leakage_carrierPower_Env_Dataset.csv',
]
for fn in files:
    path = base + '\\' + fn
    try:
        df = pd.read_csv(path, dtype=str)
        freq_cols = [c for c in df.columns if 'Freq' in c]
        if not freq_cols:
            print(fn + ': NO FREQ COLUMN')
            print('  cols=' + str(list(df.columns[:6])))
            continue
        freq_col = freq_cols[0]
        df['F'] = pd.to_numeric(df[freq_col], errors='coerce')
        temps = sorted(df['Test Step'].dropna().unique().tolist()) if 'Test Step' in df.columns else ['N/A']
        groups = df['Group'].dropna().unique() if 'Group' in df.columns else []
        print(fn)
        print(f'  rows={len(df)}  freq({freq_col}): {df["F"].min():.1f} to {df["F"].max():.1f}')
        print(f'  temps={temps}')
        print(f'  groups({len(groups)} unique): {list(groups[:3])}')
    except Exception as e:
        print(fn + ': ERROR ' + str(e))
    print()
