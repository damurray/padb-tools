"""Apply summary GF fix to padb_v2.py and padb_plots.py."""
import sys, py_compile, re
sys.stdout.reconfigure(encoding='utf-8')

# ── padb_v2.py ──────────────────────────────────────────────────────────────
v2 = open(r'C:\apps\padb\tools\padb_v2.py', encoding='utf-8').read()

# 1. Add _ser_col discovery before hi_spec_global
OLD_V2_A = '    hi_spec_global = float("nan")\n    lo_spec_global = float("nan")\n    records: list[dict] = []'
NEW_V2_A = (
    '    # Identify serial column so per-DUT means can be embedded for GF support\n'
    '    _ser_col = next(\n'
    '        (c for c in all_grp\n'
    '         if any(kw in c.removeprefix("_grp_").lower() for kw in _serial_kws)),\n'
    '        None\n'
    '    )\n'
    '\n'
    '    hi_spec_global = float("nan")\n'
    '    lo_spec_global = float("nan")\n'
    '    records: list[dict] = []'
)
assert v2.count(OLD_V2_A) == 1, f'anchor A not unique: {v2.count(OLD_V2_A)}'
v2 = v2.replace(OLD_V2_A, NEW_V2_A)

# 2. Add dut_info/dut_vals computation + embed into records
#    Insert BEFORE the records.append({...}) — specifically, we add the DUT block
#    right before the comment "# cond_keys"
OLD_V2_B = (
    '        # cond_keys: label -> value for each condition dimension\n'
    '        cond_keys_dict = {}\n'
    '        for col in cond_cols:\n'
    '            label = col.removeprefix("_grp_")\n'
    '            unique_in_cond = cdf[col].dropna().unique()\n'
    '            cond_keys_dict[label] = str(unique_in_cond[0]) if len(unique_in_cond) == 1 else ""\n'
    '\n'
    '        records.append({\n'
    '            "condition":        cond,\n'
    '            "cond_keys":        cond_keys_dict,\n'
)
assert v2.count(OLD_V2_B) == 1, f'anchor B not unique: {v2.count(OLD_V2_B)}'

NEW_V2_B = (
    '        # Per-DUT means per frequency — embedded so JS can recompute aggregates\n'
    '        # when the global filter is active (GF excludes specific DUTs).\n'
    '        dut_info: list[dict] = []\n'
    '        dut_vals: list[list] = []  # [freq_idx][dut_idx] = mean across all temps\n'
    '        if _ser_col:\n'
    '            dut_serials = sorted(str(s) for s in cdf[_ser_col].dropna().unique())\n'
    '            dut_info = [{"s": s} for s in dut_serials]\n'
    '            for freq in all_freqs:\n'
    '                freq_df = cdf[cdf["Frequency_MHz"] == freq]\n'
    '                row = []\n'
    '                for s in dut_serials:\n'
    '                    vs = freq_df[freq_df[_ser_col] == s]["Value"].dropna().values\n'
    '                    row.append(round(float(np.mean(vs)), 4) if len(vs) else None)\n'
    '                dut_vals.append(row)\n'
    '\n'
    '        # cond_keys: label -> value for each condition dimension\n'
    '        cond_keys_dict = {}\n'
    '        for col in cond_cols:\n'
    '            label = col.removeprefix("_grp_")\n'
    '            unique_in_cond = cdf[col].dropna().unique()\n'
    '            cond_keys_dict[label] = str(unique_in_cond[0]) if len(unique_in_cond) == 1 else ""\n'
    '\n'
    '        records.append({\n'
    '            "condition":        cond,\n'
    '            "cond_keys":        cond_keys_dict,\n'
)
v2 = v2.replace(OLD_V2_B, NEW_V2_B)

# 3. Add dut_info/dut_vals to records.append dict (after by_temp/temps)
OLD_V2_C = (
    '            "by_temp":          by_temp,\n'
    '            "temps":            temps_in_cond,\n'
    '        })'
)
NEW_V2_C = (
    '            "by_temp":          by_temp,\n'
    '            "temps":            temps_in_cond,\n'
    '            "dut_info":         dut_info,\n'
    '            "dut_vals":         dut_vals,\n'
    '        })'
)
assert v2.count(OLD_V2_C) == 1, f'anchor C not unique: {v2.count(OLD_V2_C)}'
v2 = v2.replace(OLD_V2_C, NEW_V2_C)

open(r'C:\apps\padb\tools\padb_v2.py', 'w', encoding='utf-8').write(v2)
py_compile.compile(r'C:\apps\padb\tools\padb_v2.py', doraise=True)
print('padb_v2.py OK')

# ── padb_plots.py ────────────────────────────────────────────────────────────
pp = open(r'C:\apps\padb\tools\padb_plots.py', encoding='utf-8').read()

# 1. Fix _loadSumGlobalFilter to SORT the coarse condition parts
#    so they match _sumCoarseCondKey's sorted output
OLD_PP_A = (
    '          var coarseCond=parts[1].split(\'|\').filter(function(p){\n'
    '            var lo=p.toLowerCase();\n'
    '            return !stripKws.some(function(kw){return lo.indexOf(kw)===0;});\n'
    '          }).join(\'|\');\n'
    '          _sumGfCoarseExcluded.add(parts[0]+\'||\'+coarseCond);'
)
assert pp.count(OLD_PP_A) == 1, f'anchor PP_A not unique: {pp.count(OLD_PP_A)}'
NEW_PP_A = (
    '          var coarseCond=parts[1].split(\'|\').filter(function(p){\n'
    '            var lo=p.toLowerCase();\n'
    '            return !stripKws.some(function(kw){return lo.indexOf(kw)===0;});\n'
    '          }).sort().join(\'|\');\n'
    '          _sumGfCoarseExcluded.add(parts[0]+\'||\'+coarseCond);'
)
pp = pp.replace(OLD_PP_A, NEW_PP_A)

# 2. Add GF per-DUT block at the START of getSumCondData
OLD_PP_B = (
    'function getSumCondData(cd,selTemps,params){\n'
    '  var allTemps=TEMPS_ALL||[];\n'
    '  var filtering=selTemps&&allTemps.length>0&&selTemps.length<allTemps.length;\n'
    '  /* No by_temp data (old record format)'
)
assert pp.count(OLD_PP_B) == 1, f'anchor PP_B not unique: {pp.count(OLD_PP_B)}'
NEW_PP_B = (
    'function getSumCondData(cd,selTemps,params){\n'
    '  var allTemps=TEMPS_ALL||[];\n'
    '  var filtering=selTemps&&allTemps.length>0&&selTemps.length<allTemps.length;\n'
    '  /* GF per-DUT filtering: recompute means from active DUTs when GF is on */\n'
    '  var _gfEl=document.getElementById(\'sum_gf_chk\');\n'
    '  var _gfOn=!_gfEl||_gfEl.checked;\n'
    '  if(_gfOn&&_sumGfCoarseExcluded&&_sumGfCoarseExcluded.size&&\n'
    '     cd.dut_info&&cd.dut_info.length&&cd.dut_vals&&cd.dut_vals.length){\n'
    '    var _coarseKey=_sumCoarseCondKey(cd.condition);\n'
    '    var _nAll=cd.dut_info.length;\n'
    '    var _gfMode=localStorage.getItem(\'padb_v2_gf_mode\')||\'exclude\';\n'
    '    var _inclIdxs=[];\n'
    '    cd.dut_info.forEach(function(di,idx){\n'
    '      var inGf=_sumGfCoarseExcluded.has(di.s+\'||\'+_coarseKey);\n'
    '      if(_gfMode===\'focus\'?inGf:!inGf) _inclIdxs.push(idx);\n'
    '    });\n'
    '    if(_inclIdxs.length>0&&_inclIdxs.length<_nAll){\n'
    '      var _nGf=_inclIdxs.length;\n'
    '      var _scale=_nGf/_nAll;\n'
    '      /* Per-freq mean from included DUTs only */\n'
    '      var _gfMeans=cd.dut_vals.map(function(row){\n'
    '        var vs=_inclIdxs.map(function(i){return row[i];}).filter(function(v){return v!==null;});\n'
    '        return vs.length?Math.round(vs.reduce(function(a,b){return a+b;},0)/vs.length*1e6)/1e6:null;\n'
    '      });\n'
    '      if(!cd.by_temp){\n'
    '        return {mean:_gfMeans,min_data:cd.min_data,max_data:cd.max_data,\n'
    '                uttl:cd.uttl,lttl:cd.lttl,uttl_is_estimate:cd.uttl_is_estimate||false,gf_n:_nGf,total_duts:_nAll};\n'
    '      }\n'
    '      /* Recompute TI using scaled-n approximation */\n'
    '      var _tps=filtering?selTemps:allTemps;\n'
    '      var _nF=cd.freqs.length;\n'
    '      var _om=[],_omin=[],_omax=[],_ou=[],_ol=[];\n'
    '      for(var _fi=0;_fi<_nF;_fi++){\n'
    '        var _tn=0,_pss=0,_mn=null,_mx=null;\n'
    '        _tps.forEach(function(t){\n'
    '          var bt=cd.by_temp[t];if(!bt)return;\n'
    '          var n=bt.n[_fi],m=bt.mean[_fi],s=bt.std[_fi];\n'
    '          if(!n||m===null||m===undefined)return;\n'
    '          var ns=Math.max(1,Math.round(n*_scale));\n'
    '          _pss+=(ns>1?(ns-1)*s*s:0);_tn+=ns;\n'
    '          if(_mn===null||bt.min_data[_fi]<_mn)_mn=bt.min_data[_fi];\n'
    '          if(_mx===null||bt.max_data[_fi]>_mx)_mx=bt.max_data[_fi];\n'
    '        });\n'
    '        var _mu=_gfMeans[_fi];\n'
    '        if(!_tn||_mu===null){\n'
    '          _om.push(_mu);_omin.push(null);_omax.push(null);_ou.push(null);_ol.push(null);continue;\n'
    '        }\n'
    '        var _sig=_tn>1?Math.sqrt(_pss/Math.max(_tn-1,1)):0;\n'
    '        var _nu=params.n_override>0?params.n_override:_tn;\n'
    '        var _k=kLookup(_nu,params.P,params.C);\n'
    '        _om.push(Math.round(_mu*1e6)/1e6);\n'
    '        _omin.push(_mn);_omax.push(_mx);\n'
    '        _ou.push(Math.round((_mu+_k*_sig+params.mu+params.denv)*1e4)/1e4);\n'
    '        _ol.push(Math.round((_mu-_k*_sig-params.mu-params.denv)*1e4)/1e4);\n'
    '      }\n'
    '      return {mean:_om,min_data:_omin,max_data:_omax,\n'
    '              uttl:_ou,lttl:_ol,uttl_is_estimate:true,gf_n:_nGf,total_duts:_nAll};\n'
    '    }\n'
    '  }\n'
    '  /* No by_temp data (old record format)'
)
pp = pp.replace(OLD_PP_B, NEW_PP_B)

# 3. Update _buildCondRows to use GF-scaled n when GF is active
OLD_PP_C = (
    '      var tot_n=0;\n'
    '      selTemps.forEach(function(t){\n'
    '        var bt=cd.by_temp&&cd.by_temp[t];\n'
    '        if(bt&&bt.n&&bt.n[fi]) tot_n+=bt.n[fi];\n'
    '      });\n'
    '      var sHi='
)
assert pp.count(OLD_PP_C) == 1, f'anchor PP_C not unique: {pp.count(OLD_PP_C)}'
NEW_PP_C = (
    '      var tot_n=0;\n'
    '      selTemps.forEach(function(t){\n'
    '        var bt=cd.by_temp&&cd.by_temp[t];\n'
    '        if(bt&&bt.n&&bt.n[fi]) tot_n+=bt.n[fi];\n'
    '      });\n'
    '      if(stats.gf_n!==undefined&&stats.total_duts&&stats.total_duts>0){\n'
    '        tot_n=Math.round(tot_n*stats.gf_n/stats.total_duts);\n'
    '      }\n'
    '      var sHi='
)
pp = pp.replace(OLD_PP_C, NEW_PP_C)

open(r'C:\apps\padb\tools\padb_plots.py', 'w', encoding='utf-8').write(pp)
py_compile.compile(r'C:\apps\padb\tools\padb_plots.py', doraise=True)
print('padb_plots.py OK')
