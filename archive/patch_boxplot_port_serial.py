# -*- coding: utf-8 -*-
"""Add Port and Serial Number to boxplot Group By; rename Temperature->Temperature Step."""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# ── Patch A: Python grp_opts — add Port and Serial, rename Temperature Step ──
OLD_A = (
    "    # Group By dropdown — options: Condition (default), Temperature (if >1), each cond_dim\n"
    "    grp_opts = '<option value=\"\">Condition</option>\\n'\n"
    "    if len(all_temps) > 1:\n"
    "        grp_opts += '<option value=\"__temp__\">Temperature</option>\\n'\n"
    "    grp_opts += ''.join(\n"
    "        f'<option value=\"{d[\"col_id\"]}\">{d[\"label\"]}</option>\\n' for d in cond_dims\n"
    "    )\n"
    "    group_by_html = (\n"
    "        sep_div\n"
    "        + '<label style=\"font-weight:600\">Group&thinsp;by:</label>'\n"
    "        + f'<select id=\"box_group_by\" style=\"font-size:12px;padding:1px 4px;'\n"
    "          f'border:1px solid #bbb;border-radius:3px\" onchange=\"update()\">\\n'\n"
    "        + grp_opts + '</select>'\n"
    "    ) if (cond_dims or len(all_temps) > 1) else ''\n"
)
count_a = src.count(OLD_A)
assert count_a == 1, f"Patch A found {count_a} times"
NEW_A = (
    "    # Group By: Condition + Temperature Step + Port + Serial + cond_dims\n"
    "    grp_opts = '<option value=\"\">Condition</option>\\n'\n"
    "    if len(all_temps) > 1:\n"
    "        grp_opts += '<option value=\"__temp__\">Temperature Step</option>\\n'\n"
    "    if all_box_ports and len(all_box_ports) > 1:\n"
    "        grp_opts += '<option value=\"__port__\">Port</option>\\n'\n"
    "    if all_box_serials and len(all_box_serials) > 1:\n"
    "        grp_opts += '<option value=\"__serial__\">Serial Number</option>\\n'\n"
    "    grp_opts += ''.join(\n"
    "        f'<option value=\"{d[\"col_id\"]}\">{d[\"label\"]}</option>\\n' for d in cond_dims\n"
    "    )\n"
    "    _has_grp_opts = (cond_dims or len(all_temps) > 1\n"
    "                     or (all_box_ports and len(all_box_ports) > 1)\n"
    "                     or (all_box_serials and len(all_box_serials) > 1))\n"
    "    group_by_html = (\n"
    "        sep_div\n"
    "        + '<label style=\"font-weight:600\">Group&thinsp;by:</label>'\n"
    "        + f'<select id=\"box_group_by\" style=\"font-size:12px;padding:1px 4px;'\n"
    "          f'border:1px solid #bbb;border-radius:3px\" onchange=\"update()\">\\n'\n"
    "        + grp_opts + '</select>'\n"
    "    ) if _has_grp_opts else ''\n"
)
src = src.replace(OLD_A, NEW_A, 1)
changes.append("A: grp_opts adds Port/Serial/TemperatureStep; dropdown condition updated")

# ── Patch B: insert buildPortSerialTraces() before buildBoxTraces() ──────────
OLD_B = (
    "  return {hi:hi,lo:lo};\n"
    "}\n"
    "function buildBoxTraces(selConds,selTemps,yFlt,selBoxSers){\n"
    "  var k=getIqrK();\n"
)
count_b = src.count(OLD_B)
assert count_b == 1, f"Patch B found {count_b} times"
NEW_B = (
    "  return {hi:hi,lo:lo};\n"
    "}\n"
    "function buildPortSerialTraces(colId,selBoxSers,selTemps,yFlt,fr,k,\n"
    "    serActive,portActive,selPorts,gfActive,boxGfFocus,passActive,passLo,passHi,rhi){\n"
    "  var freqVals={},freqSet={},freqLabels={};\n"
    "  BOX_DATA.forEach(function(cd){\n"
    "    if(selTemps.indexOf(cd.temp)<0) return;\n"
    "    (cd.freq_stats||[]).forEach(function(f){\n"
    "      if(f.freq<fr.lo||f.freq>fr.hi) return;\n"
    "      freqSet[f.freq]=true;\n"
    "      if(f.freq_label) freqLabels[f.freq]=f.freq_label;\n"
    "      (f.vals_detail||[]).forEach(function(d){\n"
    "        if(serActive&&selBoxSers.indexOf(d.s)<0) return;\n"
    "        if(portActive&&selPorts.indexOf(d.p||'')<0) return;\n"
    "        if(d.v>rhi) return;\n"
    "        if(passActive&&((passLo!==null&&d.v<passLo)||(passHi!==null&&d.v>passHi))) return;\n"
    "        if(gfActive){var _ig=_boxIsInGf(_boxBaseSerial(d.s)+'||'+_boxFullCondKey(cd.condition,d.p)+'|Temp='+cd.temp);if(boxGfFocus?!_ig:_ig) return;}\n"
    "        var gk=colId==='__port__'?(d.p||''):d.s;\n"
    "        if(!freqVals[gk]) freqVals[gk]={};\n"
    "        if(!freqVals[gk][f.freq]) freqVals[gk][f.freq]=[];\n"
    "        freqVals[gk][f.freq].push(d);\n"
    "      });\n"
    "    });\n"
    "  });\n"
    "  var sortedFreqs=Object.keys(freqSet).map(Number).sort(function(a,b){return a-b;});\n"
    "  var groups=Object.keys(freqVals).sort();\n"
    "  var condIdxMap={};groups.forEach(function(g,i){condIdxMap[g]=i;});\n"
    "  var traces=[];\n"
    "  groups.forEach(function(gk){\n"
    "    var fs_arr=sortedFreqs.map(function(freq){\n"
    "      var items=freqVals[gk][freq]||[];\n"
    "      if(!items.length) return null;\n"
    "      var vals=items.map(function(d){return d.v;});\n"
    "      var bs=computeBoxStats(vals,k); if(!bs) return null;\n"
    "      var outDet=items.filter(function(d){return d.v<bs.lo_w||d.v>bs.hi_w;});\n"
    "      return {freq:freq,freq_label:freqLabels[freq]||String(freq),\n"
    "        n:bs.n,mean:bs.mean,q1:bs.q1,q2:bs.q2,q3:bs.q3,\n"
    "        lo_w:Math.min.apply(null,vals),hi_w:Math.max.apply(null,vals),\n"
    "        outlier_detail:outDet,outliers:outDet.map(function(d){return d.v;}),vals_detail:items};\n"
    "    }).filter(Boolean);\n"
    "    if(!fs_arr.length) return;\n"
    "    var color=PALETTE[(condIdxMap[gk]||0)%PALETTE.length];\n"
    "    traces.push({\n"
    "      type:'box',\n"
    "      x:fs_arr.map(function(f){return f.freq_label;}),\n"
    "      q1:fs_arr.map(function(f){return f.q1;}),\n"
    "      median:fs_arr.map(function(f){return f.q2;}),\n"
    "      q3:fs_arr.map(function(f){return f.q3;}),\n"
    "      lowerfence:fs_arr.map(function(f){return f.lo_w;}),\n"
    "      upperfence:fs_arr.map(function(f){return f.hi_w;}),\n"
    "      mean:fs_arr.map(function(f){return f.mean;}),\n"
    "      boxpoints:false,name:gk,legendgroup:gk,showlegend:true,\n"
    "      marker:{color:color,opacity:0.7},line:{color:color,width:2},whiskerwidth:0.6,\n"
    "      hovertemplate:'<b>'+gk+'</b><br>Freq: %{x}<br>Q1: %{q1:.4f}<br>Median: %{median:.4f}<br>'+\n"
    "        'Q3: %{q3:.4f}<br>Whiskers: [%{lowerfence:.4f}, %{upperfence:.4f}]<extra></extra>',\n"
    "    });\n"
    "    if(isShowPoints()){\n"
    "      var pxP=[],pyP=[],ptP=[];\n"
    "      fs_arr.forEach(function(f){(f.vals_detail||[]).forEach(function(d){\n"
    "        pxP.push(f.freq_label);pyP.push(d.v);\n"
    "        ptP.push((d.s&&d.s!=='unknown'?d.s+': ':'')+d.v.toFixed(4));});});\n"
    "      if(pxP.length){traces.push({type:'scatter',x:pxP,y:pyP,mode:'markers',\n"
    "        marker:{size:5,color:color,opacity:0.55},name:gk+' pts',showlegend:false,\n"
    "        text:ptP,hovertemplate:'%{text}<extra></extra>'});}\n"
    "    }\n"
    "    var oxArr=[],oyArr=[],oText=[];\n"
    "    fs_arr.forEach(function(f){(f.outlier_detail||[]).forEach(function(d){\n"
    "      oxArr.push(f.freq_label);oyArr.push(d.v);\n"
    "      oText.push(gk+' outlier: '+d.v.toFixed(4)+(d.s&&d.s!=='unknown'?' ('+d.s+')':''));});});\n"
    "    if(oxArr.length){traces.push({type:'scatter',x:oxArr,y:oyArr,mode:'markers',text:oText,\n"
    "      marker:{symbol:'circle-open',size:7,color:color,opacity:0.9,line:{width:2,color:color}},\n"
    "      name:gk+' outliers',showlegend:false,\n"
    "      hovertemplate:'%{text}<extra></extra>'});}\n"
    "  });\n"
    "  return traces;\n"
    "}\n"
    "function buildBoxTraces(selConds,selTemps,yFlt,selBoxSers){\n"
    "  var k=getIqrK();\n"
)
src = src.replace(OLD_B, NEW_B, 1)
changes.append("B: buildPortSerialTraces() added before buildBoxTraces()")

# ── Patch C: early return in buildBoxTraces for __port__ / __serial__ ─────────
OLD_C = (
    "  var kChanged=Math.abs(k-1.5)>0.001;\n"
    "  var gfActive=_boxGfCoarseExcluded&&_boxGfCoarseExcluded.size>0;\n"
    "  var boxGfFocus=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';\n"
    "  var passHi=passActive?(yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined?yFlt.tll_hi:HI_SPEC):null;\n"
    "  var passLo=passActive?LO_SPEC:null;\n"
    "  var rhi=yActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;\n"
    "  var fr=getBoxFreqRange();\n"
    "  var traces=[];\n"
)
count_c = src.count(OLD_C)
assert count_c == 1, f"Patch C found {count_c} times"
NEW_C = (
    "  var kChanged=Math.abs(k-1.5)>0.001;\n"
    "  var gfActive=_boxGfCoarseExcluded&&_boxGfCoarseExcluded.size>0;\n"
    "  var boxGfFocus=(localStorage.getItem('padb_v2_gf_mode')||'exclude')==='focus';\n"
    "  var passHi=passActive?(yFlt.tll_hi!==null&&yFlt.tll_hi!==undefined?yFlt.tll_hi:HI_SPEC):null;\n"
    "  var passLo=passActive?LO_SPEC:null;\n"
    "  var rhi=yActive&&isFinite(yFlt.yhi)?yFlt.yhi:Infinity;\n"
    "  var fr=getBoxFreqRange();\n"
    "  var _bxGrpEl=document.getElementById('box_group_by');\n"
    "  var _bxGrpId=_bxGrpEl?_bxGrpEl.value:'';\n"
    "  if(_bxGrpId==='__port__'||_bxGrpId==='__serial__'){\n"
    "    return buildPortSerialTraces(_bxGrpId,selBoxSers,selTemps,yFlt,fr,k,\n"
    "      serActive,portActive,selPorts,gfActive,boxGfFocus,passActive,passLo,passHi,rhi);\n"
    "  }\n"
    "  var traces=[];\n"
)
src = src.replace(OLD_C, NEW_C, 1)
changes.append("C: buildBoxTraces early return for __port__/__serial__")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied:")
for c in changes:
    print(" ", c)
print("Done.")
