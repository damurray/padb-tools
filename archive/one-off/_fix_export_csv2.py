"""Fix exportGfCsv: skip sentinel freq=0 for manual entries, show empty start/stop."""
import sys, py_compile
sys.stdout.reconfigure(encoding='utf-8')

pp = open(r'C:\apps\padb\tools\padb_plots.py', encoding='utf-8').read()

OLD = (
    "      var gk=ser+'\\x00'+cond+'\\x00'+temp;\n"
    "      if(!groups[gk]) groups[gk]={ser:ser,cond:cond,temp:temp,flo:Infinity,fhi:-Infinity,n:0};\n"
    "      if(!isNaN(freq)){\n"
    "        if(freq<groups[gk].flo) groups[gk].flo=freq;\n"
    "        if(freq>groups[gk].fhi) groups[gk].fhi=freq;\n"
    "        groups[gk].n++;\n"
    "      }"
)

NEW = (
    "      var gk=ser+'\\x00'+cond+'\\x00'+temp;\n"
    "      if(!groups[gk]) groups[gk]={ser:ser,cond:cond,temp:temp,flo:Infinity,fhi:-Infinity,n:0};\n"
    "      groups[gk].n++;\n"
    "      /* freq=0 with temp='manual' is a sentinel (no specific freq) — exclude from range */\n"
    "      if(!isNaN(freq)&&!(temp==='manual'&&freq===0)){\n"
    "        if(freq<groups[gk].flo) groups[gk].flo=freq;\n"
    "        if(freq>groups[gk].fhi) groups[gk].fhi=freq;\n"
    "      }"
)

count = pp.count(OLD)
print(f'matches: {count}')
assert count == 1, f'Expected 1, got {count}'
pp = pp.replace(OLD, NEW)
open(r'C:\apps\padb\tools\padb_plots.py', 'w', encoding='utf-8').write(pp)
py_compile.compile(r'C:\apps\padb\tools\padb_plots.py', doraise=True)
print('OK')
