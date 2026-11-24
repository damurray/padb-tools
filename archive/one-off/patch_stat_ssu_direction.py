# -*- coding: utf-8 -*-
"""Make stat_summary SSU trace and filter bar direction-adaptive."""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# ── Patch 1a: add ID span to "Upper limit" radio label (stat_summary only) ──
# Stat summary uses name="data_flt"; boxplot uses "box_flt"; summary uses "sum_flt"
# Match exact Python source lines 4301-4302 including single-quote delimiters and \n escape in string
OLD1a = (
    "        '  <label><input type=\"radio\" name=\"data_flt\" value=\"range\"'\n"
    "        ' onchange=\"toggleRangeInputs();update()\"> Upper&nbsp;limit</label>\\n'\n"
)
NEW1a = (
    "        '  <label><input type=\"radio\" name=\"data_flt\" value=\"range\"'\n"
    "        ' onchange=\"toggleRangeInputs();update()\"><span id=\"flt_range_lbl\">&nbsp;Upper&nbsp;limit</span></label>\\n'\n"
)
count1a = src.count(OLD1a)
assert count1a == 1, f"Patch 1a found {count1a} times"
src = src.replace(OLD1a, NEW1a, 1)
changes.append("1a: added id=flt_range_lbl span to Upper limit label")

# ── Patch 1b: neutral placeholder for flt_yhi input ─────────────────────────
OLD1b = "id=\"flt_yhi\" placeholder=\"dBc limit\""
NEW1b = "id=\"flt_yhi\" placeholder=\"limit\""
count1b = src.count(OLD1b)
assert count1b == 1, f"Patch 1b found {count1b} times"
src = src.replace(OLD1b, NEW1b, 1)
changes.append("1b: flt_yhi placeholder changed to 'limit'")

# ── Patch 2: range filter sets spec_lo_override when lower-only ─────────────
OLD2 = "  if(flt.mode==='range'&&isFinite(flt.yhi)){params.spec_hi_override=flt.yhi;}\n"
count2 = src.count(OLD2)
assert count2 == 1, f"Patch 2 found {count2} times"
NEW2 = (
    "  if(flt.mode==='range'&&isFinite(flt.yhi)){\n"
    "    var _spLoEl=document.getElementById('stat_spec_lo'),_spHiEl=document.getElementById('stat_spec_hi');\n"
    "    var _spLoV=_spLoEl&&_spLoEl.value!==''?parseFloat(_spLoEl.value):NaN;\n"
    "    var _spHiV=_spHiEl&&_spHiEl.value!==''?parseFloat(_spHiEl.value):NaN;\n"
    "    if(isFinite(_spLoV)&&!isFinite(_spHiV)) params.spec_lo_override=flt.yhi;\n"
    "    else params.spec_hi_override=flt.yhi;\n"
    "  }\n"
    "  updateFilterLabel();\n"
)
src = src.replace(OLD2, NEW2, 1)
changes.append("2: range filter direction-adaptive + calls updateFilterLabel()")

# ── Patch 3: add updateFilterLabel() before toggleRangeInputs() ─────────────
# There is ONE toggleRangeInputs() that belongs to stat_summary (no prefix).
# Boxplot has no toggleRangeInputs (it uses a different pattern).
OLD3 = "function toggleRangeInputs(){\n  var el=document.getElementById('flt_range_inputs');\n"
count3 = src.count(OLD3)
assert count3 == 1, f"Patch 3 found {count3} times"
NEW3 = (
    "function updateFilterLabel(){\n"
    "  var _spLo=document.getElementById('stat_spec_lo'),_spHi=document.getElementById('stat_spec_hi');\n"
    "  var _loOn=_spLo&&_spLo.value!==''&&isFinite(parseFloat(_spLo.value));\n"
    "  var _hiOn=_spHi&&_spHi.value!==''&&isFinite(parseFloat(_spHi.value));\n"
    "  var lbl=document.getElementById('flt_range_lbl');\n"
    "  var inp=document.getElementById('flt_yhi');\n"
    "  if(_loOn&&!_hiOn){\n"
    "    if(lbl) lbl.innerHTML='&nbsp;Lower&nbsp;limit';\n"
    "    if(inp) inp.placeholder='min (dBm)';\n"
    "  } else {\n"
    "    if(lbl) lbl.innerHTML='&nbsp;Upper&nbsp;limit';\n"
    "    if(inp) inp.placeholder='limit';\n"
    "  }\n"
    "}\n"
    "function toggleRangeInputs(){\n  var el=document.getElementById('flt_range_inputs');\n"
)
src = src.replace(OLD3, NEW3, 1)
changes.append("3: updateFilterLabel() function defined")

# ── Patch 4: SSU trace (Trace 5) direction-adaptive ─────────────────────────
# Use unique sub-anchors since the full block is hard to match
# 4a: insert _ssuLo detection before ssu_x=[]
OLD4a = (
    "    // Trace 5: Spec supportable = TI_up + MU + DEnv + GB (bottom-up view)\n"
    "    // Shows what spec this population can commit to at each frequency.\n"
    "    // Green markers where ssu <= spec (passing with margin); red where ssu > spec.\n"
    "    var ssu_x=[],ssu_y=[],ssu_cols=[],ssu_hover=[];\n"
)
count4a = src.count(OLD4a)
assert count4a == 1, f"Patch 4a found {count4a} times"
NEW4a = (
    "    // Trace 5: Spec supportable — direction-adaptive (upper or lower spec)\n"
    "    // Green markers where ssu is within spec (passing with margin); red otherwise.\n"
    "    var _ssuFirstR=sorted.length?computeFreqResult(sorted[0],params):null;\n"
    "    var _ssuLo=_ssuFirstR&&(_ssuFirstR.tll_lo!==null&&_ssuFirstR.tll_up===null);\n"
    "    var ssu_x=[],ssu_y=[],ssu_cols=[],ssu_hover=[];\n"
)
src = src.replace(OLD4a, NEW4a, 1)
changes.append("4a: _ssuLo direction flag before SSU loop")

# 4b: replace ssu_y.push and col logic inside the loop
OLD4b = (
    "      ssu_x.push(fs.freq);\n"
    "      ssu_y.push(r.ssu_up);\n"
    "      var col=r.spec_up!==null?(r.ssu_pass_up?'#2ca02c':'#d62728'):'#9467bd';\n"
    "      if(!r.ssu_pass_up) any_ssu_fail=true;\n"
    "      ssu_cols.push(col);\n"
    "      var budgetStr='MU='+params.mu.toFixed(2)+' ΔEnv='+r.denv_up.toFixed(2)+' GB='+params.gb.toFixed(2);\n"
    "      var mStr=r.margin_up!==null?\n"
    "        'Margin: '+(r.margin_up>=0?'+':'')+r.margin_up.toFixed(3)+' dB '+(r.margin_up>=0?'✔':'✘'):\n"
    "        'No spec defined';\n"
    "      ssu_hover.push('Spec supportable: '+r.ssu_up.toFixed(3)+'<br>'+budgetStr+'<br>'+mStr);\n"
)
count4b = src.count(OLD4b)
assert count4b == 1, f"Patch 4b found {count4b} times"
NEW4b = (
    "      ssu_x.push(fs.freq);\n"
    "      var ssuVal=_ssuLo?r.ssu_lo:r.ssu_up;\n"
    "      ssu_y.push(ssuVal);\n"
    "      var col=_ssuLo?\n"
    "        (r.spec_lo!==null?(r.ssu_pass_lo?'#2ca02c':'#d62728'):'#9467bd'):\n"
    "        (r.spec_up!==null?(r.ssu_pass_up?'#2ca02c':'#d62728'):'#9467bd');\n"
    "      if(_ssuLo?!r.ssu_pass_lo:!r.ssu_pass_up) any_ssu_fail=true;\n"
    "      ssu_cols.push(col);\n"
    "      var denv=_ssuLo?r.denv_lo:r.denv_up;\n"
    "      var budgetStr='MU='+params.mu.toFixed(2)+' ΔEnv='+denv.toFixed(2)+' GB='+params.gb.toFixed(2);\n"
    "      var margin=_ssuLo?r.margin_lo:r.margin_up;\n"
    "      var mStr=margin!==null?\n"
    "        'Margin: '+(margin>=0?'+':'')+margin.toFixed(3)+' dB '+(margin>=0?'✔':'✘'):\n"
    "        'No spec defined';\n"
    "      ssu_hover.push('Spec supportable: '+ssuVal.toFixed(3)+'<br>'+budgetStr+'<br>'+mStr);\n"
)
src = src.replace(OLD4b, NEW4b, 1)
changes.append("4b: SSU loop body uses ssuVal, ssuLo-adaptive col/margin/hover")

# 4c: rename trace label ▲Spec → ▼Spec when lower-only
OLD4c = "      name:cd.condition+' ▲Spec',legendgroup:cd.condition,\n"
count4c = src.count(OLD4c)
assert count4c == 1, f"Patch 4c found {count4c} times"
NEW4c = "      name:cd.condition+(_ssuLo?' ▼Spec':' ▲Spec'),legendgroup:cd.condition,\n"
src = src.replace(OLD4c, NEW4c, 1)
changes.append("4c: SSU trace name ▲/▼Spec adaptive")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied:")
for c in changes:
    print(" ", c)
print("Done.")
