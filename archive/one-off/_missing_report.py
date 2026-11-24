import pandas as pd, os, re

RPLOTS = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\R-Plots'

DATASETS = [
    ('Close-In Spurs',            'NonHarmonics_Close_In_Env_Dataset2.csv'),
    ('Clock Leakage Spurs',       'NonHarmonics_Clock_leakage_Env_Dataset2.csv'),
    ('Line-Related Spurs',        'NonHarmonics_Line_Related_Env_Dataset2.csv'),
    ('Harmonics & Sub-Harmonics', 'Harmonics_and_Subharmonics_Env_Dataset2.csv'),
]

def parse_group(g):
    ser = re.search(r'Serial Number:\s*(\S+)', str(g))
    port = re.search(r'Port:\s*(\S+)', str(g))
    return (ser.group(1) if ser else '?', port.group(1) if port else '?')

def find_meas_col(df):
    fixed = {'Analysis Type', 'Model(s)', 'Algorithm -> Result', 'Units',
             'Group', 'Frequency (MHz)', 'Test Step', 'Upper Limit (<=)',
             'Lower Limit (>=)'}
    for c in df.columns:
        if c not in fixed:
            return c
    return None

lines = []
lines.append('=' * 72)
lines.append('  PADB V2 -- Missing Room-Temperature Data Report')
lines.append('  SG6311A Characterization Datasets  |  2026-07-11')
lines.append('=' * 72)
lines.append('')
lines.append('Expected DUT pool: 14 (11 serials; 401, 501, 502 each contribute RF1+RF2)')
lines.append('US65080410 excluded -- not a spec-setting instrument')
lines.append('Row counts show non-NaN measured values only.')
lines.append('MISSING = 0 valid Room rows.  SPARSE = < 30% of dataset median Room count.')
lines.append('Room-only = has Room data but no environmental (0C/20C/30C/55C) data.')
lines.append('')

for name, fname in DATASETS:
    path = os.path.join(RPLOTS, fname)
    df = pd.read_csv(path, low_memory=False)

    meas_col = find_meas_col(df)

    parsed = df['Group'].apply(parse_group)
    df['_ser'] = parsed.apply(lambda x: x[0])
    df['_port'] = parsed.apply(lambda x: x[1])
    df['_dut'] = df['_ser'].str[-3:] + '_' + df['_port']
    df['_temp'] = df['Test Step']

    valid = df[df[meas_col].notna()]
    all_duts = sorted(df['_dut'].unique())

    room_valid = valid[valid['_temp'] == 'Room']
    room_counts = {d: len(room_valid[room_valid['_dut'] == d]) for d in all_duts}

    # Median from DUTs that have any Room data at all
    nonzero_counts = [v for v in room_counts.values() if v > 0]
    median_room = sorted(nonzero_counts)[len(nonzero_counts)//2] if nonzero_counts else 1
    sparse_thresh = median_room * 0.30

    n_ok = sum(1 for v in room_counts.values() if v > 0)

    lines.append('-' * 72)
    lines.append('  ' + name)
    lines.append('  Measurement column   : ' + str(meas_col))
    lines.append('-' * 72)
    temps = sorted(df['_temp'].unique())
    lines.append('  Temperatures present : ' + str(temps))
    lines.append('  Total DUTs found     : ' + str(len(all_duts)))
    lines.append('  Median Room count    : ' + str(median_room) +
                 '  (SPARSE threshold: ' + str(int(sparse_thresh)) + ')')
    lines.append('  DUTs with Room data  : ' + str(n_ok) +
                 '  (effective n = ' + str(n_ok) + ' for TI calculation)')
    lines.append('')

    hdr = ('  ' + 'DUT'.ljust(16) + '  ' + 'Room'.rjust(7) +
           '  ' + '0C'.rjust(7) + '  ' + '20C'.rjust(7) +
           '  ' + '30C'.rjust(7) + '  ' + '55C'.rjust(7) + '  Status')
    sep = ('  ' + '-'*16 + '  ' + '-'*7 +
           '  ' + '-'*7 + '  ' + '-'*7 +
           '  ' + '-'*7 + '  ' + '-'*7 + '  ------')
    lines.append(hdr)
    lines.append(sep)

    for dut in all_duts:
        sub = valid[valid['_dut'] == dut]
        r   = room_counts[dut]
        c0  = len(sub[sub['_temp'] == '0.0 Deg C'])
        c20 = len(sub[sub['_temp'] == '20.0 Deg C'])
        c30 = len(sub[sub['_temp'] == '30.0 Deg C'])
        c55 = len(sub[sub['_temp'] == '55.0 Deg C'])
        env_total = c0 + c20 + c30 + c55
        if r == 0:
            status = 'MISSING'
        elif r < sparse_thresh:
            status = 'SPARSE'
        elif env_total == 0:
            status = 'Room-only'
        else:
            status = 'OK'
        lines.append('  ' + dut.ljust(16) +
                     '  ' + str(r).rjust(7) +
                     '  ' + str(c0).rjust(7) +
                     '  ' + str(c20).rjust(7) +
                     '  ' + str(c30).rjust(7) +
                     '  ' + str(c55).rjust(7) +
                     '  ' + status)
    lines.append('')

lines.append('=' * 72)
lines.append('  SUMMARY OF ISSUES')
lines.append('=' * 72)
lines.append('')
lines.append('  Close-In (n=11):')
lines.append('    MISSING Room:  401_RF2, 432_RF1, 501_RF2')
lines.append('    Note: 401_RF2 and 501_RF2 have full environmental data; only Room is NaN.')
lines.append('    Note: 432_RF1 environmental data also absent (all-NaN at Room).')
lines.append('')
lines.append('  Clock Leakage / Line-Related / Harmonics (n=13):')
lines.append('    MISSING Room:  501_RF2 only')
lines.append('    SPARSE Room:   401_RF2 (~170 rows, 8% of median)')
lines.append('                   432_RF1 (~170 rows, 8% of median)')
lines.append('    Note: SPARSE DUTs DO contribute to n but with fewer frequency points.')
lines.append('    Note: 416_RF1 and 426_RF1 are Room-only (no environmental runs).')
lines.append('')
lines.append('  Recommended actions:')
lines.append('    1. Re-check 501_RF2 Room-temp measurement files for all datasets.')
lines.append('    2. Re-check 401_RF2 Room-temp measurement files for Close-In only.')
lines.append('    3. Accept SPARSE entries for 401_RF2 and 432_RF1 in clock/line/harmonics')
lines.append('       if no additional Room data is available (these are Proc-20 results).')
lines.append('    4. 416_RF1 and 426_RF1 Room-only status is expected (limited test plan).')
lines.append('')
lines.append('=' * 72)
lines.append('  END OF REPORT')
lines.append('=' * 72)

report = '\n'.join(lines)
print(report)

out = r'C:\Users\damurray\OneDrive - Keysight Technologies\Documents\Padb\Data\missing_data_report.txt'
with open(out, 'w') as f:
    f.write(report + '\n')
print('\nReport written to: ' + out)
