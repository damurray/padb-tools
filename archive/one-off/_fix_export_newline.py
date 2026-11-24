"""Fix literal CRLF inside exportGfCsv rows.join string in padb_plots.py."""
import sys, py_compile, re
sys.stdout.reconfigure(encoding='utf-8')

path = r'C:\apps\padb\tools\padb_plots.py'
# Read in binary so CRLF is visible
raw = open(path, 'rb').read()

BAD  = b"rows.join('\r\n')],{type:'text/csv'});"
GOOD = b"rows.join('\\r\\n')],{type:'text/csv;charset=utf-8;'});"

count = raw.count(BAD)
print(f'Bad pattern occurrences: {count}')
if count != 1:
    # Check for just LF version too
    BAD2 = b"rows.join('\n')],{type:'text/csv'});"
    count2 = raw.count(BAD2)
    print(f'LF-only version occurrences: {count2}')
    if count2 == 1:
        raw = raw.replace(BAD2, GOOD)
        open(path, 'wb').write(raw)
        py_compile.compile(path, doraise=True)
        print('Fixed (LF-only version) OK')
    else:
        # Show all rows.join occurrences
        for m in re.finditer(rb"rows\.join\('([^']*?)'\)", raw):
            print(f'  at {m.start()}: {repr(m.group(0)[:60])}')
else:
    raw = raw.replace(BAD, GOOD)
    open(path, 'wb').write(raw)
    py_compile.compile(path, doraise=True)
    print('Fixed (CRLF version) OK')
