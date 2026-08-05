import pandas as pd
base = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots'
files = ['Non_Harmonics_Close_In_Env_Dataset.csv', 'NonHarmonics_Close_in_Env_Dataset.csv']
for fn in files:
    path = base + '\\' + fn
    try:
        df = pd.read_csv(path, dtype=str)
        freq_col = [c for c in df.columns if 'Freq' in c][0]
        df['F'] = pd.to_numeric(df[freq_col], errors='coerce')
        temps = sorted(df['Test Step'].dropna().unique().tolist())
        print(fn)
        print(f'  rows={len(df)}  freq: {df["F"].min():.1f} to {df["F"].max():.1f} MHz')
        print(f'  temps={temps}')
    except Exception as e:
        print(fn + ': ERROR ' + str(e))
