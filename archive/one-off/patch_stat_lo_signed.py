"""Fix stat_summary lower limit to use signed spec_lo instead of magnitude.

Changes to padb_plots.py:
  1. Remove Math.abs() in computeFreqResult — treat spec_lo as signed
  2. Update tll_lo, margin_lo, spec_lo, ssu_pass_lo to use signed spec_lo_raw
  3. Update HTML label: |Spec↓| → Spec↓, remove min="0", update tooltip

Change to padb_v2.py:
  4. _write_index: suppress per-job description when merging multiple job outputs
"""
import sys

# ── padb_plots.py changes ───────────────────────────────────────────────────

plots_path = r'C:\apps\padb\tools\padb_plots.py'
with open(plots_path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# Change 1: remove spec_lo_mag, update all 5 uses of it in computeFreqResult
OLD1 = (
    "  // spec_lo stored/entered as either signed (−0.15) or magnitude (0.15) — always apply as lower limit\n"
    "  var spec_lo_raw=(fs.spec_lo!=null)?fs.spec_lo:params.spec_lo_override;\n"
    "  var spec_lo_mag=(spec_lo_raw!=null)?Math.abs(spec_lo_raw):null;\n"
    "  var g_up=params.mu+dev_up+params.gb+params.drift;\n"
    "  var g_lo=params.mu+dev_lo+params.gb+params.drift;\n"
    "  var tll_up=(params.tll_hi_override!==null)?params.tll_hi_override:((spec_up!=null)?spec_up-g_up:null);\n"
    "  var tll_lo=(params.tll_lo_override!==null)?params.tll_lo_override:((spec_lo_mag!=null)?-spec_lo_mag+g_lo:null);\n"
    "  var ssu_up=ti_up+g_up;   // spec supportable from data (upper): TI_up + full budget\n"
    "  var ssu_lo=ti_lo-g_lo;   // spec supportable (lower): TI_lo - full budget\n"
    "  var margin_up=(spec_up!==null)?spec_up-ssu_up:null;   // positive = passing with margin\n"
    "  var margin_lo=(spec_lo_mag!==null)?ssu_lo-(-spec_lo_mag):null;\n"
    "  return {n_use:n_use,k:k,ti_up:ti_up,ti_lo:ti_lo,np_active:useNp,\n"
    "          tll_up:tll_up,tll_lo:tll_lo,\n"
    "          ssu_up:ssu_up,ssu_lo:ssu_lo,\n"
    "          margin_up:margin_up,margin_lo:margin_lo,\n"
    "          denv_up:dev_up,denv_lo:dev_lo,\n"
    "          spec_lo:-spec_lo_mag,spec_up:spec_up,\n"
    "          pass_up:tll_up===null||ti_up<=tll_up,\n"
    "          pass_lo:tll_lo===null||ti_lo>=tll_lo,\n"
    "          ssu_pass_up:spec_up===null||ssu_up<=spec_up,\n"
    "          ssu_pass_lo:spec_lo_mag===null||ssu_lo>=-spec_lo_mag};"
)
NEW1 = (
    "  // spec_lo entered as signed value: negative for upper-limit specs (e.g. −0.15 dBc), positive for lower-limit specs (e.g. +14 dBm)\n"
    "  var spec_lo_raw=(fs.spec_lo!=null)?fs.spec_lo:params.spec_lo_override;\n"
    "  var g_up=params.mu+dev_up+params.gb+params.drift;\n"
    "  var g_lo=params.mu+dev_lo+params.gb+params.drift;\n"
    "  var tll_up=(params.tll_hi_override!==null)?params.tll_hi_override:((spec_up!=null)?spec_up-g_up:null);\n"
    "  var tll_lo=(params.tll_lo_override!==null)?params.tll_lo_override:((spec_lo_raw!=null)?spec_lo_raw+g_lo:null);\n"
    "  var ssu_up=ti_up+g_up;   // spec supportable from data (upper): TI_up + full budget\n"
    "  var ssu_lo=ti_lo-g_lo;   // spec supportable (lower): TI_lo - full budget\n"
    "  var margin_up=(spec_up!==null)?spec_up-ssu_up:null;   // positive = passing with margin\n"
    "  var margin_lo=(spec_lo_raw!==null)?ssu_lo-spec_lo_raw:null;\n"
    "  return {n_use:n_use,k:k,ti_up:ti_up,ti_lo:ti_lo,np_active:useNp,\n"
    "          tll_up:tll_up,tll_lo:tll_lo,\n"
    "          ssu_up:ssu_up,ssu_lo:ssu_lo,\n"
    "          margin_up:margin_up,margin_lo:margin_lo,\n"
    "          denv_up:dev_up,denv_lo:dev_lo,\n"
    "          spec_lo:spec_lo_raw,spec_up:spec_up,\n"
    "          pass_up:tll_up===null||ti_up<=tll_up,\n"
    "          pass_lo:tll_lo===null||ti_lo>=tll_lo,\n"
    "          ssu_pass_up:spec_up===null||ssu_up<=spec_up,\n"
    "          ssu_pass_lo:spec_lo_raw===null||ssu_lo>=spec_lo_raw};"
)
assert src.count(OLD1) == 1, f"Change 1 anchor found {src.count(OLD1)} times"
src = src.replace(OLD1, NEW1, 1)
changes.append("1: removed Math.abs(), spec_lo_raw used directly throughout computeFreqResult")

# Change 2: update HTML label — |Spec↓| → Spec↓, remove min="0", update tooltip
OLD2 = (
    '        f\'  <label title="Lower spec magnitude, e.g. 0.15 for a &#177;0.15 spec (sign applied automatically)">\'\n'
    '        f\'&#124;Spec&#8595;&#124;:<input type="number" id="stat_spec_lo" value="{spec_lo_val}" min="0" step="0.001"\'\n'
    '        f\' style="width:74px" placeholder="0.15" oninput="update()"></label>\\n\''
)
NEW2 = (
    '        f\'  <label title="Lower spec limit as signed value, e.g. 14 for a ≥14 dBm spec or −0.15 for a ≤−0.15 dBc spec">\'\n'
    '        f\'Spec&#8595;:<input type="number" id="stat_spec_lo" value="{spec_lo_val}" step="0.001"\'\n'
    '        f\' style="width:74px" placeholder="e.g. 14" oninput="update()"></label>\\n\''
)
assert src.count(OLD2) == 1, f"Change 2 anchor found {src.count(OLD2)} times"
src = src.replace(OLD2, NEW2, 1)
changes.append("2: HTML label |Spec↓| → Spec↓, removed min=0, updated tooltip and placeholder")

out = src.replace('\n', '\r\n') if crlf else src
with open(plots_path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied padb_plots.py changes:")
for c in changes:
    print(" ", c)

# ── padb_v2.py change ───────────────────────────────────────────────────────

v2_path = r'C:\apps\padb\tools\padb_v2.py'
with open(v2_path, 'r', encoding='utf-8') as f:
    v2src = f.read()

# Change 3: suppress per-job description in index when multiple job outputs merged
OLD3 = (
    '    title = cfg.get("index_title", prefix)\n'
    '    desc = cfg.get("description", "")\n'
)
NEW3 = (
    '    title = cfg.get("index_title", prefix)\n'
    '    # Suppress per-job description when multiple jobs share the same output dir\n'
    '    desc = cfg.get("description", "") if not existing else ""\n'
)
assert v2src.count(OLD3) == 1, f"Change 3 anchor found {v2src.count(OLD3)} times"
v2src = v2src.replace(OLD3, NEW3, 1)

with open(v2_path, 'w', encoding='utf-8') as f:
    f.write(v2src)

print("\nApplied padb_v2.py changes:")
print("  3: index description suppressed when merging multiple job outputs")
print("\nDone.")
