import pandas as pd, numpy as np
csv = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\Non_Harmonics_Close_In_CarrierPower_Dataset.csv'
df = pd.read_csv(csv, dtype=str)
df['Freq'] = pd.to_numeric(df['Frequency (MHz)'], errors='coerce')
df['Freq_MHz'] = df['Freq'] / 1e6

print(f'Total rows: {len(df)}')
print(f'Freq range (raw): {df["Freq"].min():.0f} to {df["Freq"].max():.0f}')
print(f'Freq range (MHz after /1e6): {df["Freq_MHz"].min():.4f} to {df["Freq_MHz"].max():.4f}')
print(f'Unique freqs: {df["Freq_MHz"].nunique()}')
print()
print('All unique frequencies (MHz):')
for f in sorted(df['Freq_MHz'].dropna().unique()):
    print(f'  {f:.4f}')
