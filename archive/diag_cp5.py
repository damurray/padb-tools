import pandas as pd
csv = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\Non_Harmonics_Close_In_CarrierPower_Dataset.csv'
df = pd.read_csv(csv, dtype=str)
val_col = [c for c in df.columns if 'Carrier' in c][0]
df['Val'] = pd.to_numeric(df[val_col], errors='coerce')
valid = df.dropna(subset=['Val'])
print(f"Carrier power range: {valid['Val'].min():.3f} to {valid['Val'].max():.3f} dBm")
print(f"Mean: {valid['Val'].mean():.3f}  P5: {valid['Val'].quantile(0.05):.3f}  P95: {valid['Val'].quantile(0.95):.3f}")
