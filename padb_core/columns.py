"""Parquet column detection -- same keyword rules as the v2 loaders (padb_plots / padb_viewer)."""
from __future__ import annotations

META_COLS = {
    "analysis type", "model(s)", "algorithm -> result", "units", "group",
    "station", "test step",
}


def qi(name: str) -> str:
    """Quote a column name for DuckDB SQL."""
    return '"' + str(name).replace('"', '""') + '"'


def detect_columns(schema_names, x_override=None, value_override=None):
    """-> (x_col, value_col, serial_col, group_col, temp_col); any may be None."""
    lower = {n.lower(): n for n in schema_names}

    def first(pred):
        for n in schema_names:
            if pred(n.lower()):
                return n
        return None

    x_col = x_override or first(lambda l: "frequency" in l or "x value" in l)
    serial_col = first(lambda l: "serial num" in l or "serial no" in l
                       or l == "serial number" or l == "serial")
    group_col = lower.get("group")
    temp_col = first(lambda l: l == "test step" or "temp" in l)
    value_col = value_override
    if not value_col:
        skip = META_COLS | {"", (x_col or "").lower(), (serial_col or "").lower(),
                            (temp_col or "").lower()}
        for n in schema_names:
            l = n.lower()
            if l in skip or "limit" in l:
                continue
            value_col = n
            break
    return x_col, value_col, serial_col, group_col, temp_col
