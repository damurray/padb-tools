# -*- coding: utf-8 -*-
"""
Replace single SSU trace in buildTraces() with dual lo/hi traces.
When no spec is defined: both ▼Spec and ▲Spec shown (envelope around mean).
When lower spec entered: only ▼Spec shown.
When upper spec entered: only ▲Spec shown.
When both specs entered: both shown.
"""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

# The full SSU section inserted by patch_stat_ssu_direction.py (Patches 4a/4b/4c)
# em-dash U+2014, delta U+0394, checkmark U+2714, cross U+2718, tri-dn U+25BC, tri-up U+25B2
OLD = (
    "    // Trace 5: Spec supportable — direction-adaptive (upper or lower spec)\n"
    "    // Green markers where ssu is within spec (passing with margin); red otherwise.\n"
    "    var _ssuFirstR=sorted.length?computeFreqResult(sorted[0],params):null;\n"
    "    var _ssuLo=_ssuFirstR&&(_ssuFirstR.tll_lo!==null&&_ssuFirstR.tll_up===null);\n"
    "    var ssu_x=[],ssu_y=[],ssu_cols=[],ssu_hover=[];\n"
    "    var any_ssu_fail=false;\n"
    "    sorted.forEach(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
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
    "    });\n"
    "    traces.push({\n"
    "      type:'scatter',x:ssu_x,y:ssu_y,mode:'lines+markers',\n"
    "      line:{color:color,width:1,dash:'dot'},\n"
    "      marker:{size:6,color:ssu_cols,line:{color:'white',width:0.5}},\n"
    "      name:cd.condition+(_ssuLo?' ▼Spec':' ▲Spec'),legendgroup:cd.condition,\n"
    "      showlegend:any_ssu_fail,\n"
    "      text:ssu_hover,\n"
    "      hovertemplate:'<b>'+cd.condition+'</b><br>%{text}<extra></extra>'\n"
    "    });\n"
)
count = src.count(OLD)
assert count == 1, f"Dual-SSU patch found {count} times (expected 1)"

# Dual-trace replacement:
#   _showSsuLo = _hasLoSpec || !_hasHiSpec  (lo-only, both, or no-spec → show lo)
#   _showSsuHi = _hasHiSpec || !_hasLoSpec  (hi-only, both, or no-spec → show hi)
# Legend only shown when spec is active and a failure exists.
NEW = (
    "    // Trace 5/5b: Spec Supportable lower (▼) and/or upper (▲).\n"
    "    // When no spec defined both are shown — they bracket the mean as an envelope.\n"
    "    // When a spec is entered only the matching direction is shown.\n"
    "    var _ssuR0=sorted.length?computeFreqResult(sorted[0],params):null;\n"
    "    var _hasLoSpec=!!(_ssuR0&&_ssuR0.tll_lo!==null);\n"
    "    var _hasHiSpec=!!(_ssuR0&&_ssuR0.tll_up!==null);\n"
    "    var _showSsuLo=_hasLoSpec||!_hasHiSpec;\n"
    "    var _showSsuHi=_hasHiSpec||!_hasLoSpec;\n"
    "    var ssuLo_x=[],ssuLo_y=[],ssuLo_cols=[],ssuLo_hover=[],any_ssuLo_fail=false;\n"
    "    var ssuHi_x=[],ssuHi_y=[],ssuHi_cols=[],ssuHi_hover=[],any_ssuHi_fail=false;\n"
    "    sorted.forEach(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
    "      var budLo='MU='+params.mu.toFixed(2)+' ΔEnv='+r.denv_lo.toFixed(2)+' GB='+params.gb.toFixed(2);\n"
    "      var budHi='MU='+params.mu.toFixed(2)+' ΔEnv='+r.denv_up.toFixed(2)+' GB='+params.gb.toFixed(2);\n"
    "      if(_showSsuLo){\n"
    "        ssuLo_x.push(fs.freq);ssuLo_y.push(r.ssu_lo);\n"
    "        var clo=r.spec_lo!==null?(r.ssu_pass_lo?'#2ca02c':'#d62728'):'#9467bd';\n"
    "        if(r.spec_lo!==null&&!r.ssu_pass_lo) any_ssuLo_fail=true;\n"
    "        ssuLo_cols.push(clo);\n"
    "        var mlo=r.margin_lo;\n"
    "        var mStrLo=mlo!==null?'Margin: '+(mlo>=0?'+':'')+mlo.toFixed(3)+' dB '+(mlo>=0?'✔':'✘'):'No spec defined';\n"
    "        ssuLo_hover.push('Spec spt↓: '+r.ssu_lo.toFixed(3)+'<br>'+budLo+'<br>'+mStrLo);\n"
    "      }\n"
    "      if(_showSsuHi){\n"
    "        ssuHi_x.push(fs.freq);ssuHi_y.push(r.ssu_up);\n"
    "        var chi=r.spec_up!==null?(r.ssu_pass_up?'#2ca02c':'#d62728'):'#9467bd';\n"
    "        if(r.spec_up!==null&&!r.ssu_pass_up) any_ssuHi_fail=true;\n"
    "        ssuHi_cols.push(chi);\n"
    "        var mhi=r.margin_up;\n"
    "        var mStrHi=mhi!==null?'Margin: '+(mhi>=0?'+':'')+mhi.toFixed(3)+' dB '+(mhi>=0?'✔':'✘'):'No spec defined';\n"
    "        ssuHi_hover.push('Spec spt↑: '+r.ssu_up.toFixed(3)+'<br>'+budHi+'<br>'+mStrHi);\n"
    "      }\n"
    "    });\n"
    "    if(_showSsuLo){\n"
    "      traces.push({\n"
    "        type:'scatter',x:ssuLo_x,y:ssuLo_y,mode:'lines+markers',\n"
    "        line:{color:color,width:1,dash:'dot'},\n"
    "        marker:{size:6,color:ssuLo_cols,line:{color:'white',width:0.5}},\n"
    "        name:cd.condition+' ▼Spec',legendgroup:cd.condition,\n"
    "        showlegend:_hasLoSpec?any_ssuLo_fail:false,\n"
    "        text:ssuLo_hover,\n"
    "        hovertemplate:'<b>'+cd.condition+'</b><br>%{text}<extra></extra>'\n"
    "      });\n"
    "    }\n"
    "    if(_showSsuHi){\n"
    "      traces.push({\n"
    "        type:'scatter',x:ssuHi_x,y:ssuHi_y,mode:'lines+markers',\n"
    "        line:{color:color,width:1,dash:'dot'},\n"
    "        marker:{size:6,color:ssuHi_cols,line:{color:'white',width:0.5}},\n"
    "        name:cd.condition+' ▲Spec',legendgroup:cd.condition,\n"
    "        showlegend:_hasHiSpec?any_ssuHi_fail:false,\n"
    "        text:ssuHi_hover,\n"
    "        hovertemplate:'<b>'+cd.condition+'</b><br>%{text}<extra></extra>'\n"
    "      });\n"
    "    }\n"
)

src = src.replace(OLD, NEW, 1)

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Dual-SSU patch applied.")
