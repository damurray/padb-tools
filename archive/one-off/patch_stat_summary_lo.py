# -*- coding: utf-8 -*-
"""Fix stat_summary for lower-limit datasets — 3 targeted patches."""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# --- Patch A: insert direction detection after nFail=0 in updateStatPanel ---
OLD_A = (
    "  var nFail=0,rows=[];\n"
    "  conds.forEach(function(cd){\n"
    "    var sorted=(cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;});\n"
    "    sorted.forEach(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
    "      var pass=r.pass_up&&r.pass_lo&&r.ssu_pass_up&&r.ssu_pass_lo;\n"
)
count_a = src.count(OLD_A)
assert count_a == 1, f"Patch A found {count_a} times"
NEW_A = (
    "  var nFail=0,rows=[];\n"
    "  var _hasLo=false,_hasHi=false;\n"
    "  (conds||[]).forEach(function(cd){(cd.freq_stats||[]).forEach(function(fs){\n"
    "    var _r=computeFreqResult(fs,params);\n"
    "    if(_r.tll_lo!==null) _hasLo=true;\n"
    "    if(_r.tll_up!==null) _hasHi=true;\n"
    "  });});\n"
    "  var ssuHdr=(_hasLo&&!_hasHi)?'Spec Spt&#8595;':(_hasHi?'Spec Spt&#8593;':'Spec Spt');\n"
    "  var marginHdr=(_hasLo&&!_hasHi)?'Margin&#8595;':(_hasHi?'Margin&#8593;':'Margin');\n"
    "  conds.forEach(function(cd){\n"
    "    var sorted=(cd.freq_stats||[]).slice().sort(function(a,b){return a.freq-b.freq;});\n"
    "    sorted.forEach(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
    "      var pass=r.pass_up&&r.pass_lo&&r.ssu_pass_up&&r.ssu_pass_lo;\n"
)
src = src.replace(OLD_A, NEW_A, 1)
changes.append("A: direction detection (_hasLo/_hasHi) + adaptive header vars")

# --- Patch B: tllStr one-sided, ssuStr/marginStr adaptive ---
# Actual file uses: ';font-weight:bold">'+  (note double-quote before >)
OLD_B = (
    "      var tllStr=(r.tll_lo!==null&&r.tll_up!==null)?\n"
    "        '['+r.tll_lo.toFixed(4)+',\xa0'+r.tll_up.toFixed(4)+']':'—';\n"
    "      var ssuStr=r.ssu_up.toFixed(4);\n"
    "      var marginStr=r.margin_up!==null?\n"
    "        '<span style=\"color:'+(r.margin_up>=0?'green':'red')+';font-weight:bold\">'+\n"
    "        (r.margin_up>=0?'+':'')+r.margin_up.toFixed(4)+'</span>':'—';\n"
)
count_b = src.count(OLD_B)
assert count_b == 1, f"Patch B found {count_b} times"
NEW_B = (
    "      var tllStr;\n"
    "      if(r.tll_lo!==null&&r.tll_up!==null)\n"
    "        tllStr='['+r.tll_lo.toFixed(4)+',\xa0'+r.tll_up.toFixed(4)+']';\n"
    "      else if(r.tll_lo!==null)\n"
    "        tllStr='Lo:\xa0'+r.tll_lo.toFixed(4);\n"
    "      else if(r.tll_up!==null)\n"
    "        tllStr='Hi:\xa0'+r.tll_up.toFixed(4);\n"
    "      else\n"
    "        tllStr='—';\n"
    "      var ssuVal=(_hasLo&&!_hasHi)?r.ssu_lo:r.ssu_up;\n"
    "      var ssuStr=ssuVal.toFixed(4);\n"
    "      var marginVal=(_hasLo&&!_hasHi)?r.margin_lo:r.margin_up;\n"
    "      var marginStr=marginVal!==null?\n"
    "        '<span style=\"color:'+(marginVal>=0?'green':'red')+';font-weight:bold\">'+\n"
    "        (marginVal>=0?'+':'')+marginVal.toFixed(4)+'</span>':'—';\n"
)
src = src.replace(OLD_B, NEW_B, 1)
changes.append("B: tllStr one-sided; ssuStr/marginStr adaptive to lower/upper")

# --- Patch C: dynamic column headers ---
OLD_C = "    '<th>Spec Spt↑</th><th>Margin↑</th>'+\n"
count_c = src.count(OLD_C)
assert count_c == 1, f"Patch C found {count_c} times"
NEW_C  = "    '<th>'+ssuHdr+'</th><th>'+marginHdr+'</th>'+\n"
src = src.replace(OLD_C, NEW_C, 1)
changes.append("C: table headers Spec Spt/Margin adaptive")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied:")
for c in changes:
    print(" ", c)
print("Done.")
