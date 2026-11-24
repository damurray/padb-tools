"""Add Temperature to boxplot Group By dropdown.

Changes:
  1. getGroupKey(condition) -> getGroupKey(cd): accepts full data obj, handles __temp__
  2. condIdxMap forEach: getGroupKey(cd.condition) -> getGroupKey(cd)
  3. Trace builder: getGroupKey(cd.condition) -> getGroupKey(cd)
  4. Python grp_opts: add Temperature option; show dropdown when all_temps > 1 too
"""
path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# Change 1: getGroupKey — accept cd object, handle __temp__
OLD1 = (
    "function getGroupKey(condition){\n"
    "  var sel=document.getElementById('box_group_by');\n"
    "  if(!sel||!sel.value) return condition;\n"
    "  var colId=sel.value;\n"
    "  var dim=null;\n"
    "  for(var i=0;i<COND_DIMS.length;i++){if(COND_DIMS[i].col_id===colId){dim=COND_DIMS[i];break;}}\n"
    "  if(!dim) return condition;\n"
    "  var safe=dim.col.replace(/[-\\/\\\\^$*+?.()|[\\]{}]/g,'\\\\$&');\n"
    "  var m=condition.match(new RegExp(safe+':\\\\s*(.+?)(?=\\\\s{2,}|$)'));\n"
    "  return m?m[1].trim():condition;\n"
    "}"
)
NEW1 = (
    "function getGroupKey(cd){\n"
    "  var sel=document.getElementById('box_group_by');\n"
    "  if(!sel||!sel.value) return cd.condition;\n"
    "  var colId=sel.value;\n"
    "  if(colId==='__temp__') return cd.temp||'';\n"
    "  var condition=cd.condition;\n"
    "  var dim=null;\n"
    "  for(var i=0;i<COND_DIMS.length;i++){if(COND_DIMS[i].col_id===colId){dim=COND_DIMS[i];break;}}\n"
    "  if(!dim) return condition;\n"
    "  var safe=dim.col.replace(/[-\\/\\\\^$*+?.()|[\\]{}]/g,'\\\\$&');\n"
    "  var m=condition.match(new RegExp(safe+':\\\\s*(.+?)(?=\\\\s{2,}|$)'));\n"
    "  return m?m[1].trim():condition;\n"
    "}"
)
assert src.count(OLD1) == 1, f"Change 1 found {src.count(OLD1)} times"
src = src.replace(OLD1, NEW1, 1)
changes.append("1: getGroupKey(cd) — accepts full data obj, handles __temp__")

# Change 2: condIdxMap forEach
OLD2 = "  BOX_DATA.forEach(function(cd){var _gk=getGroupKey(cd.condition);if(condIdxMap[_gk]===undefined) condIdxMap[_gk]=ci++;});"
NEW2 = "  BOX_DATA.forEach(function(cd){var _gk=getGroupKey(cd);if(condIdxMap[_gk]===undefined) condIdxMap[_gk]=ci++;});"
assert src.count(OLD2) == 1, f"Change 2 found {src.count(OLD2)} times"
src = src.replace(OLD2, NEW2, 1)
changes.append("2: condIdxMap forEach uses getGroupKey(cd)")

# Change 3: trace gkey
OLD3 = "    var _gkey=getGroupKey(cd.condition);"
NEW3 = "    var _gkey=getGroupKey(cd);"
assert src.count(OLD3) == 1, f"Change 3 found {src.count(OLD3)} times"
src = src.replace(OLD3, NEW3, 1)
changes.append("3: trace builder uses getGroupKey(cd)")

# Change 4: Python group_by_html — add Temperature option
OLD4 = (
    "    # Group By dropdown — options: Condition (default) + each cond_dim\n"
    "    grp_opts = '<option value=\"\">Condition</option>\\n'\n"
    "    grp_opts += ''.join(\n"
    "        f'<option value=\"{d[\"col_id\"]}\">{d[\"label\"]}</option>\\n' for d in cond_dims\n"
    "    )\n"
    "    group_by_html = (\n"
    "        sep_div\n"
    "        + '<label style=\"font-weight:600\">Group&thinsp;by:</label>'\n"
    "        + f'<select id=\"box_group_by\" style=\"font-size:12px;padding:1px 4px;'\n"
    "          f'border:1px solid #bbb;border-radius:3px\" onchange=\"update()\">\\n'\n"
    "        + grp_opts + '</select>'\n"
    "    ) if cond_dims else ''"
)
NEW4 = (
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
    "    ) if (cond_dims or len(all_temps) > 1) else ''"
)
assert src.count(OLD4) == 1, f"Change 4 found {src.count(OLD4)} times"
src = src.replace(OLD4, NEW4, 1)
changes.append("4: Python grp_opts adds Temperature; dropdown shown when all_temps > 1 too")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied changes:")
for c in changes:
    print(" ", c)
print("Done.")
