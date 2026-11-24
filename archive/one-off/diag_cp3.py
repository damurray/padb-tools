import pandas as pd
csv = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots\Non_Harmonics_Close_In_CarrierPower_Dataset.csv'
df = pd.read_csv(csv, dtype=str)
df['Freq_MHz'] = pd.to_numeric(df['Frequency (MHz)'], errors='coerce') / 1e6
df['Val'] = pd.to_numeric(df['SpectralMiniMoabClkSpursPowerControl:Carrier Power (dBm) [N]'], errors='coerce')
df['Temp'] = df['Test Step'].str.strip()

room = df[df['Temp'] == 'Room']
nonroom = df[df['Temp'] != 'Room']

room_freqs = set(room['Freq_MHz'].dropna().unique())
nonroom_freqs = set(nonroom['Freq_MHz'].dropna().unique())
both = room_freqs & nonroom_freqs

print(f'Room rows: {len(room)} | Non-room rows: {len(nonroom)}')
print(f'Room freqs: {len(room_freqs)} | Non-room freqs: {len(nonroom_freqs)}')
print(f'Freqs with BOTH room and non-room: {len(both)}')
print(f'  Min: {min(both):.4f} MHz  Max: {max(both):.4f} MHz')
print()
print(f'Room-only freqs (no non-room data): {len(room_freqs - nonroom_freqs)}')
print(f'Non-room-only freqs (no room data): {len(nonroom_freqs - room_freqs)}')
nr_only = sorted(nonroom_freqs - room_freqs)
if nr_only:
    print(f'  Range: {nr_only[0]:.1f} to {nr_only[-1]:.1f} MHz  (first 5: {nr_only[:5]})')
print()
# Check if room has null values at these frequencies
room_by_freq = room.groupby('Freq_MHz')['Val'].count()
print('Room data count per freq (freqs with <3 room measurements):')
low_room = room_by_freq[room_by_freq < 3]
print(f'  {len(low_room)} freqs have <3 room measurements')
if len(low_room):
    print(f'  Max freq with <3 room: {low_room.index.max():.4f} MHz')
