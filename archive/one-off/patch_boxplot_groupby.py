"""Apply Group By + fix harmonic bar to padb_plots.py boxplot."""
import sys

path = r'C:\apps\padb\tools\padb_plots.py'
with open(path, 'rb') as f:
    raw = f.read()
crlf = b'\r\n' in raw
src = raw.decode('utf-8').replace('\r\n', '\n')

changes = []

# ── Change 1: add getGroupKey() JS function after getBoxFreqRange() ──────────
OLD1 = (
    "function isBoxNpTI(){var c=document.getElementById('box_np_ti_chk');return c?c.checked:false;}\n"
    "function isShowPoints(){var c=document.getElementById('box_show_pts_chk');return c?c.checked:false;}"
)
NEW1 = (
    "function isBoxNpTI(){var c=document.getElementById('box_np_ti_chk');return c?c.checked:false;}\n"
    "function isShowPoints(){var c=document.getElementById('box_show_pts_chk');return c?c.checked:false;}\n"
    "/* Group By: extract the value for the selected dimension from a condition string.\n"
    "   Returns the full condition string when 'Condition' (empty) is selected. */\n"
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
assert src.count(OLD1) == 1, f"Change 1 anchor found {src.count(OLD1)} times"
src = src.replace(OLD1, NEW1, 1)
changes.append("1: added getGroupKey()")

# ── Change 2: condIdxMap — key by group, not full condition ──────────────────
OLD2 = (
    "  var condIdxMap={};var ci=0;\n"
    "  BOX_DATA.forEach(function(cd){if(condIdxMap[cd.condition]===undefined) condIdxMap[cd.condition]=ci++;});"
)
NEW2 = (
    "  var condIdxMap={};var ci=0;\n"
    "  BOX_DATA.forEach(function(cd){var _gk=getGroupKey(cd.condition);if(condIdxMap[_gk]===undefined) condIdxMap[_gk]=ci++;});\n"
    "  var _legendShown={};"
)
assert src.count(OLD2) == 1, f"Change 2 anchor found {src.count(OLD2)} times"
src = src.replace(OLD2, NEW2, 1)
changes.append("2: condIdxMap uses getGroupKey")

# ── Change 3: trace color + name + legendgroup/showlegend ────────────────────
OLD3 = (
    "    var color=PALETTE[(condIdxMap[cd.condition]||0)%PALETTE.length];\n"
    "    var showTemp=selTemps.length>1;\n"
    "    var name=showTemp?cd.condition+' ('+cd.temp+')':cd.condition;\n"
    "    traces.push({\n"
    "      type:'box',\n"
    "      x:fs.map(function(f){return f.freq_label;}),\n"
    "      q1:fs.map(function(f){return f.q1;}),\n"
    "      median:fs.map(function(f){return f.q2;}),\n"
    "      q3:fs.map(function(f){return f.q3;}),\n"
    "      lowerfence:fs.map(function(f){return f.lo_w;}),\n"
    "      upperfence:fs.map(function(f){return f.hi_w;}),\n"
    "      mean:fs.map(function(f){return f.mean;}),\n"
    "      boxpoints:false,\n"
    "      name:name,\n"
    "      marker:{color:color,opacity:0.7},\n"
    "      line:{color:color,width:2},\n"
    "      whiskerwidth:0.6,\n"
    "      opacity:cd.temp==='Room'?0.85:0.65,\n"
    "      hovertemplate:'<b>'+name+'</b><br>Freq: %{x}<br>Q1: %{q1:.4f}<br>Median: %{median:.4f}<br>'+\n"
    "        'Q3: %{q3:.4f}<br>Whiskers: [%{lowerfence:.4f}, %{upperfence:.4f}]<extra></extra>',\n"
    "    });"
)
NEW3 = (
    "    var _gkey=getGroupKey(cd.condition);\n"
    "    var color=PALETTE[(condIdxMap[_gkey]||0)%PALETTE.length];\n"
    "    var showTemp=selTemps.length>1;\n"
    "    var _lgKey=_gkey+'|'+cd.temp;\n"
    "    var name=showTemp?_gkey+' ('+cd.temp+')':_gkey;\n"
    "    var _showLegend=!_legendShown[_lgKey];_legendShown[_lgKey]=true;\n"
    "    traces.push({\n"
    "      type:'box',\n"
    "      x:fs.map(function(f){return f.freq_label;}),\n"
    "      q1:fs.map(function(f){return f.q1;}),\n"
    "      median:fs.map(function(f){return f.q2;}),\n"
    "      q3:fs.map(function(f){return f.q3;}),\n"
    "      lowerfence:fs.map(function(f){return f.lo_w;}),\n"
    "      upperfence:fs.map(function(f){return f.hi_w;}),\n"
    "      mean:fs.map(function(f){return f.mean;}),\n"
    "      boxpoints:false,\n"
    "      name:name,\n"
    "      legendgroup:_lgKey,\n"
    "      showlegend:_showLegend,\n"
    "      marker:{color:color,opacity:0.7},\n"
    "      line:{color:color,width:2},\n"
    "      whiskerwidth:0.6,\n"
    "      opacity:cd.temp==='Room'?0.85:0.65,\n"
    "      hovertemplate:'<b>'+name+'</b><br>Freq: %{x}<br>Q1: %{q1:.4f}<br>Median: %{median:.4f}<br>'+\n"
    "        'Q3: %{q3:.4f}<br>Whiskers: [%{lowerfence:.4f}, %{upperfence:.4f}]<extra></extra>',\n"
    "    });"
)
assert src.count(OLD3) == 1, f"Change 3 anchor found {src.count(OLD3)} times"
src = src.replace(OLD3, NEW3, 1)
changes.append("3: trace uses group key, legendgroup, showlegend")

# ── Change 4: Group By dropdown in ctrl_bar ───────────────────────────────────
OLD4 = (
    "    sep_div = '<div class=\"sep\"></div>'\n"
    "    ctrl_parts = []\n"
    "    if cond_dims:\n"
    "        ctrl_parts.append(panels_html)\n"
    "    if box_serial_panel_html:\n"
    "        if ctrl_parts:\n"
    "            ctrl_parts.append(sep_div)\n"
    "        ctrl_parts.append(box_serial_panel_html)\n"
    "    if box_port_panel_html:\n"
    "        if ctrl_parts:\n"
    "            ctrl_parts.append(sep_div)\n"
    "        ctrl_parts.append(box_port_panel_html)\n"
    "    ctrl_bar = (f'<div class=\"ctrl-bar\">\\n  ' + '\\n  '.join(ctrl_parts) + '\\n</div>\\n') if ctrl_parts else \"\""
)
NEW4 = (
    "    sep_div = '<div class=\"sep\"></div>'\n"
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
    "    ) if cond_dims else ''\n"
    "    ctrl_parts = []\n"
    "    if cond_dims:\n"
    "        ctrl_parts.append(panels_html)\n"
    "    if box_serial_panel_html:\n"
    "        if ctrl_parts:\n"
    "            ctrl_parts.append(sep_div)\n"
    "        ctrl_parts.append(box_serial_panel_html)\n"
    "    if box_port_panel_html:\n"
    "        if ctrl_parts:\n"
    "            ctrl_parts.append(sep_div)\n"
    "        ctrl_parts.append(box_port_panel_html)\n"
    "    if group_by_html:\n"
    "        ctrl_parts.append(group_by_html)\n"
    "    ctrl_bar = (f'<div class=\"ctrl-bar\">\\n  ' + '\\n  '.join(ctrl_parts) + '\\n</div>\\n') if ctrl_parts else \"\""
)
assert src.count(OLD4) == 1, f"Change 4 anchor found {src.count(OLD4)} times"
src = src.replace(OLD4, NEW4, 1)
changes.append("4: Group By dropdown added to ctrl_bar")

# ── Change 5: make box_lf_bar conditional (hide for non-spur/harm data) ──────
OLD5 = (
    "        + '<div class=\"box_lf_bar\">\\n'\n"
    "        + f'<span style=\"font-weight:600;color:#444;margin-right:4px\">{box_lf_primary_label}:</span>\\n'\n"
    "        + f'<input type=\"hidden\" id=\"box_lf_primary_col\" value=\"{box_lf_primary_col}\">\\n'\n"
    "        + f'<select id=\"box_harm_sel\" style=\"font-size:12px;padding:1px 4px;border:1px solid #bbb;border-radius:3px\" onchange=\"updateBoxHarmonic()\">\\n'\n"
    "        + box_lf_opts + \"\\n</select>\\n\"\n"
    "        + '<button style=\"font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff;margin-left:6px\" onclick=\"selAllBoxLf(true)\">All</button>\\n'\n"
    "        + '<button style=\"font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff\" onclick=\"selAllBoxLf(false)\">None</button>\\n'\n"
    "        + '</div>\\n'\n"
    "        + (('<div class=\"box_lf_panel\">\\n' + box_lf_rows + '</div>\\n') if box_lf_rows else \"\")"
)
NEW5 = (
    "        + (\n"
    "            '<div class=\"box_lf_bar\">\\n'\n"
    "            + f'<span style=\"font-weight:600;color:#444;margin-right:4px\">{box_lf_primary_label}:</span>\\n'\n"
    "            + f'<input type=\"hidden\" id=\"box_lf_primary_col\" value=\"{box_lf_primary_col}\">\\n'\n"
    "            + f'<select id=\"box_harm_sel\" style=\"font-size:12px;padding:1px 4px;border:1px solid #bbb;border-radius:3px\" onchange=\"updateBoxHarmonic()\">\\n'\n"
    "            + box_lf_opts + \"\\n</select>\\n\"\n"
    "            + '<button style=\"font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff;margin-left:6px\" onclick=\"selAllBoxLf(true)\">All</button>\\n'\n"
    "            + '<button style=\"font-size:11px;padding:1px 7px;border:1px solid #bbb;border-radius:3px;cursor:pointer;background:#fff\" onclick=\"selAllBoxLf(false)\">None</button>\\n'\n"
    "            + '</div>\\n'\n"
    "            + (('<div class=\"box_lf_panel\">\\n' + box_lf_rows + '</div>\\n') if box_lf_rows else \"\")\n"
    "            if (harm_orders_box or spur_orders_box) else\n"
    "            (('<div class=\"box_lf_panel\">\\n' + box_lf_rows + '</div>\\n') if box_lf_rows else \"\")\n"
    "        )"
)
assert src.count(OLD5) == 1, f"Change 5 anchor found {src.count(OLD5)} times"
src = src.replace(OLD5, NEW5, 1)
changes.append("5: box_lf_bar conditional on harm/spur data")

out = src.replace('\n', '\r\n') if crlf else src
with open(path, 'wb') as f:
    f.write(out.encode('utf-8'))

print("Applied changes:")
for c in changes:
    print(" ", c)
print("Done.")
