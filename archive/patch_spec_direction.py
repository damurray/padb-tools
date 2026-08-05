# -*- coding: utf-8 -*-
"""
Add SPEC_DIRECTION JS constant (from cfg.spec_direction or auto-detected from data).
Patches 1-2: Python emits the constant.
Patch  3:    updateFilterLabel() defaults to Lower/Upper based on SPEC_DIRECTION.
Patch  5:    updateStatPanel() header fallback uses SPEC_DIRECTION.
(SSU dual-trace is in patch_dual_ssu.py)
"""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# ── Patch 1: Python computes spec_dir_js ──────────────────────────────────────
OLD1 = (
    "    lo_js = \"null\"\n"
    "    hi_js = \"null\"\n"
)
count1 = src.count(OLD1)
assert count1 == 1, f"Patch 1 found {count1} times"
NEW1 = (
    "    lo_js = \"null\"\n"
    "    hi_js = \"null\"\n"
    "    # Determine spec direction from data and job config\n"
    "    _any_lo = any(fs.get(\"spec_lo\") is not None\n"
    "                  for cd in stat_data for fs in cd.get(\"freq_stats\", []))\n"
    "    _any_hi = any(fs.get(\"spec_up\") is not None\n"
    "                  for cd in stat_data for fs in cd.get(\"freq_stats\", []))\n"
    "    _cfg_dir = cfg.get(\"spec_direction\", \"auto\")\n"
    "    if _cfg_dir in (\"lo\", \"hi\", \"both\", \"none\"):\n"
    "        spec_dir_js = _cfg_dir\n"
    "    elif _any_lo and _any_hi:\n"
    "        spec_dir_js = \"both\"\n"
    "    elif _any_lo:\n"
    "        spec_dir_js = \"lo\"\n"
    "    elif _any_hi:\n"
    "        spec_dir_js = \"hi\"\n"
    "    else:\n"
    "        spec_dir_js = \"none\"\n"
)
src = src.replace(OLD1, NEW1, 1)
changes.append("1: Python computes spec_dir_js")

# ── Patch 2: add SPEC_DIRECTION to JS constants list ─────────────────────────
# Anchor: the SS_ALL_PORTS line (unique in this constants block)
# File line: f"var SS_ALL_PORTS={json.dumps(all_ports_ss)};",
OLD2 = "        f\"var SS_ALL_PORTS={json.dumps(all_ports_ss)};\","
count2 = src.count(OLD2)
assert count2 == 1, f"Patch 2 found {count2} times"
NEW2 = (
    "        f\"var SS_ALL_PORTS={json.dumps(all_ports_ss)};\",\n"
    "        f\"var SPEC_DIRECTION={json.dumps(spec_dir_js)};\","
)
src = src.replace(OLD2, NEW2, 1)
changes.append("2: SPEC_DIRECTION added to JS constants")

# ── Patch 3: updateFilterLabel() — SPEC_DIRECTION as default ─────────────────
OLD3 = (
    "function updateFilterLabel(){\n"
    "  var _spLo=document.getElementById('stat_spec_lo'),_spHi=document.getElementById('stat_spec_hi');\n"
    "  var _loOn=_spLo&&_spLo.value!==''&&isFinite(parseFloat(_spLo.value));\n"
    "  var _hiOn=_spHi&&_spHi.value!==''&&isFinite(parseFloat(_spHi.value));\n"
)
count3 = src.count(OLD3)
assert count3 == 1, f"Patch 3 found {count3} times"
NEW3 = (
    "function updateFilterLabel(){\n"
    "  var _spLo=document.getElementById('stat_spec_lo'),_spHi=document.getElementById('stat_spec_hi');\n"
    "  var _loOn=_spLo&&_spLo.value!==''&&isFinite(parseFloat(_spLo.value));\n"
    "  var _hiOn=_spHi&&_spHi.value!==''&&isFinite(parseFloat(_spHi.value));\n"
    "  if(!_loOn&&!_hiOn){\n"
    "    var _sd=typeof SPEC_DIRECTION!=='undefined'?SPEC_DIRECTION:'none';\n"
    "    _loOn=_sd==='lo'||_sd==='both';\n"
    "    _hiOn=_sd==='hi'||_sd==='both';\n"
    "  }\n"
)
src = src.replace(OLD3, NEW3, 1)
changes.append("3: updateFilterLabel() uses SPEC_DIRECTION for default")

# ── Patch 5: updateStatPanel — SPEC_DIRECTION fallback for headers ────────────
OLD5 = (
    "  var ssuHdr=(_hasLo&&!_hasHi)?'Spec Spt&#8595;':(_hasHi?'Spec Spt&#8593;':'Spec Spt');\n"
    "  var marginHdr=(_hasLo&&!_hasHi)?'Margin&#8595;':(_hasHi?'Margin&#8593;':'Margin');\n"
)
count5 = src.count(OLD5)
assert count5 == 1, f"Patch 5 found {count5} times"
NEW5 = (
    "  if(!_hasLo&&!_hasHi){\n"
    "    var _spsd=typeof SPEC_DIRECTION!=='undefined'?SPEC_DIRECTION:'none';\n"
    "    if(_spsd==='lo'||_spsd==='both') _hasLo=true;\n"
    "    if(_spsd==='hi'||_spsd==='both') _hasHi=true;\n"
    "  }\n"
    "  var ssuHdr=(_hasLo&&!_hasHi)?'Spec Spt&#8595;':(_hasHi?'Spec Spt&#8593;':'Spec Spt');\n"
    "  var marginHdr=(_hasLo&&!_hasHi)?'Margin&#8595;':(_hasHi?'Margin&#8593;':'Margin');\n"
)
src = src.replace(OLD5, NEW5, 1)
changes.append("5: updateStatPanel SPEC_DIRECTION fallback for headers")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied:")
for c in changes:
    print(" ", c)
print("Done.")
