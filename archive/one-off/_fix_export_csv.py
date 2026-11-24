"""Restructure exportGfCsv to group by (serial, cond, temp) with start/stop freq."""
import sys, py_compile
sys.stdout.reconfigure(encoding='utf-8')

pp = open(r'C:\apps\padb\tools\padb_plots.py', encoding='utf-8').read()

OLD = (
    "function exportGfCsv(){\n"
    "  try{\n"
    "    var raw=localStorage.getItem('padb_v2_excluded');\n"
    "    if(!raw){alert('No global filter entries to export.');return;}\n"
    "    var keys=(JSON.parse(raw).excluded||[]);\n"
    "    if(!keys.length){alert('No global filter entries to export.');return;}\n"
    "    function esc(v){v=String(v);return v.indexOf(',')>=0||v.indexOf('\"')>=0?'\"'+v.replace(/\"/g,'\"\"')+'\"':v;}\n"
    "    var rows=['Serial,Condition,Temperature,Frequency_MHz'];\n"
    "    keys.forEach(function(k){\n"
    "      var p=k.split('||');\n"
    "      rows.push([esc(p[0]||''),esc(p[1]||''),esc(p[2]||''),esc(p[3]||'')].join(','));\n"
    "    });\n"
    "    var blob=new Blob([rows.join('\\r\\n')],{type:'text/csv;charset=utf-8;'});\n"
    "    var url=URL.createObjectURL(blob);\n"
    "    var a=document.createElement('a');a.href=url;a.download='global_filter.csv';\n"
    "    document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);\n"
    "  }catch(e){alert('Export failed: '+e);}\n"
    "}"
)

NEW = (
    "function exportGfCsv(){\n"
    "  try{\n"
    "    var raw=localStorage.getItem('padb_v2_excluded');\n"
    "    if(!raw){alert('No global filter entries to export.');return;}\n"
    "    var keys=(JSON.parse(raw).excluded||[]);\n"
    "    if(!keys.length){alert('No global filter entries to export.');return;}\n"
    "    function esc(v){v=String(v);return v.indexOf(',')>=0||v.indexOf('\"')>=0?'\"'+v.replace(/\"/g,'\"\"')+'\"':v;}\n"
    "    /* Group by (serial, condKey, temp) — collect freqs into start/stop range */\n"
    "    var groups={};\n"
    "    keys.forEach(function(k){\n"
    "      var p=k.split('||');\n"
    "      var ser=p[0]||'',cond=p[1]||'',temp=p[2]||'',freq=parseFloat(p[3]);\n"
    "      var gk=ser+'\\x00'+cond+'\\x00'+temp;\n"
    "      if(!groups[gk]) groups[gk]={ser:ser,cond:cond,temp:temp,flo:Infinity,fhi:-Infinity,n:0};\n"
    "      if(!isNaN(freq)){\n"
    "        if(freq<groups[gk].flo) groups[gk].flo=freq;\n"
    "        if(freq>groups[gk].fhi) groups[gk].fhi=freq;\n"
    "        groups[gk].n++;\n"
    "      }\n"
    "    });\n"
    "    var rows=['Serial,Condition,Temperature,Start_Freq_MHz,Stop_Freq_MHz,N_Points'];\n"
    "    Object.keys(groups).sort().forEach(function(gk){\n"
    "      var g=groups[gk];\n"
    "      var flo=isFinite(g.flo)?g.flo.toFixed(6):'';\n"
    "      var fhi=isFinite(g.fhi)?g.fhi.toFixed(6):'';\n"
    "      rows.push([esc(g.ser),esc(g.cond),esc(g.temp),flo,fhi,g.n].join(','));\n"
    "    });\n"
    "    var blob=new Blob([rows.join('\\r\\n')],{type:'text/csv;charset=utf-8;'});\n"
    "    var url=URL.createObjectURL(blob);\n"
    "    var a=document.createElement('a');a.href=url;a.download='global_filter.csv';\n"
    "    document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(url);\n"
    "  }catch(e){alert('Export failed: '+e);}\n"
    "}"
)

count = pp.count(OLD)
print(f'matches: {count}')
assert count == 1, f'Expected 1, got {count}'
pp = pp.replace(OLD, NEW)
open(r'C:\apps\padb\tools\padb_plots.py', 'w', encoding='utf-8').write(pp)
py_compile.compile(r'C:\apps\padb\tools\padb_plots.py', doraise=True)
print('OK')
