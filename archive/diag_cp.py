import pandas as pd
csv = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\Non_Harmonics_Close_In_CarrierPower_Dataset.csv'
df = pd.read_csv(csv, dtype=str, nrows=5)
print('Columns:', df.columns.tolist())
print()
for i, row in df.iterrows():
    print(f'Row {i}: Group={repr(row["Group"])}  Freq={row["Frequency (MHz)"]}  Step={row["Test Step"]}')
print()
df2 = pd.read_csv(csv, dtype=str)
print('Total rows:', len(df2))
print('Unique groups (first 5):')
for g in df2['Group'].dropna().unique()[:5]:
    print(' ', repr(g))
print('Test Steps:', sorted(df2['Test Step'].dropna().unique()))
print('Freq range:', df2['Frequency (MHz)'].dropna().astype(float).min(), 'to', df2['Frequency (MHz)'].dropna().astype(float).max())
