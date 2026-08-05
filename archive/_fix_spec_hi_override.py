"""Fix stat_summary 'Upper limit' data filter to actually override spec_up for TLL/margin."""
import py_compile, sys
sys.stdout.reconfigure(encoding='utf-8')

path = r'C:\apps\padb\tools\padb_plots.py'
pp = open(path, encoding='utf-8').read()

# 1. computeFreqResult: give spec_hi_override priority over data spec
OLD1 = "  var spec_up=(fs.spec_up!=null)?fs.spec_up:params.spec_hi_override;\n"
NEW1 = "  var spec_up=(params.spec_hi_override!==null)?params.spec_hi_override:((fs.spec_up!=null)?fs.spec_up:null);\n"
assert pp.count(OLD1) == 1, f'anchor 1 not unique: {pp.count(OLD1)}'
pp = pp.replace(OLD1, NEW1)
print('Fix 1 applied: computeFreqResult spec_hi_override priority')

# 2. update(): inject flt.yhi into params.spec_hi_override when mode='range'
OLD2 = (
    "  var flt=getDataFilter();\n"
    "  conds=applyDataFilter(conds,params,flt);\n"
    "  Plotly.purge('plot');Plotly.newPlot('plot',buildTraces(conds,params),buildLayout(conds,params),{responsive:true});\n"
)
NEW2 = (
    "  var flt=getDataFilter();\n"
    "  if(flt.mode==='range'&&isFinite(flt.yhi)){params.spec_hi_override=flt.yhi;}\n"
    "  conds=applyDataFilter(conds,params,flt);\n"
    "  Plotly.purge('plot');Plotly.newPlot('plot',buildTraces(conds,params),buildLayout(conds,params),{responsive:true});\n"
)
assert pp.count(OLD2) == 1, f'anchor 2 not unique: {pp.count(OLD2)}'
pp = pp.replace(OLD2, NEW2)
print('Fix 2 applied: update() injects flt.yhi into params')

# 3. applyDataFilter: range mode no longer hides frequencies (spec override does the work)
OLD3 = (
    "function applyDataFilter(conds,params,flt){\n"
    "  if(flt.mode==='all') return conds;\n"
    "  return conds.map(function(cd){\n"
    "    var fs2=(cd.freq_stats||[]).filter(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
    "      if(flt.mode==='passing') return r.pass_up&&r.pass_lo;\n"
    "      if(flt.mode==='range') return r.ti_up<=flt.yhi;\n"
    "      return true;\n"
    "    });\n"
    "    return Object.assign({},cd,{freq_stats:fs2});\n"
    "  });\n"
    "}"
)
NEW3 = (
    "function applyDataFilter(conds,params,flt){\n"
    "  if(flt.mode==='all'||flt.mode==='range') return conds;\n"
    "  return conds.map(function(cd){\n"
    "    var fs2=(cd.freq_stats||[]).filter(function(fs){\n"
    "      var r=computeFreqResult(fs,params);\n"
    "      if(flt.mode==='passing') return r.pass_up&&r.pass_lo;\n"
    "      return true;\n"
    "    });\n"
    "    return Object.assign({},cd,{freq_stats:fs2});\n"
    "  });\n"
    "}"
)
assert pp.count(OLD3) == 1, f'anchor 3 not unique: {pp.count(OLD3)}'
pp = pp.replace(OLD3, NEW3)
print('Fix 3 applied: applyDataFilter range mode no longer hides frequencies')

# 4. Update label text to reflect new behaviour
OLD4 = "    <small style=\"color:#666\">(hides freqs where TI upper bound exceeds this limit; Y scale unchanged)</small>\n"
NEW4 = "    <small style=\"color:#666\">(overrides test data spec; TLL and margin recalculated relative to this limit)</small>\n"
assert pp.count(OLD4) == 1, f'anchor 4 not unique: {pp.count(OLD4)}'
pp = pp.replace(OLD4, NEW4)
print('Fix 4 applied: label updated')

open(path, 'w', encoding='utf-8').write(pp)
py_compile.compile(path, doraise=True)
print('OK — padb_plots.py compiles cleanly')
