"""
padb_config.py — Shared per-user defaults for padb-tools scripts.

Reads an optional config file for personal defaults (padb_exe,
padb_output_dir, padb_logs_dir, data_dir, publish_root) so you set them
once instead of every script hardcoding a specific username, or every
job.json repeating the same three lines:

    C:\\Users\\<you>\\OneDrive - Keysight Technologies\\Documents\\Padb\\padb_config.json

    {
        "padb_exe": "C:\\Program Files\\KEYSIGHT\\PADB-R.NET\\PADB-R.exe",
        "padb_output_dir": "C:\\Users\\<you>\\OneDrive - Keysight Technologies\\Documents\\Padb\\R-Plots",
        "padb_logs_dir": "C:\\Users\\<you>\\OneDrive - Keysight Technologies\\Documents\\Padb\\Logs",
        "data_dir": "C:\\Users\\<you>\\OneDrive - Keysight Technologies\\Documents\\Padb\\Data",
        "publish_root": "\\\\srsnas01.srs.is.keysight.com\\prod\\MIDRF3\\SG6311A\\PADB-Simple"
    }

The file is entirely optional -- if it doesn't exist, every key falls back
to a value derived from Path.home() (so it's still correct for whoever
runs the script, not hardcoded to one username) or this project's existing
shared defaults (padb_exe, publish_root).
"""
from __future__ import annotations

import json
from pathlib import Path

_PADB_DIR = Path.home() / "OneDrive - Keysight Technologies" / "Documents" / "Padb"
CONFIG_PATH = _PADB_DIR / "padb_config.json"

_BUILTIN_DEFAULTS = {
    "padb_exe": r"C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe",
    "padb_output_dir": str(_PADB_DIR / "R-Plots"),
    "padb_logs_dir": str(_PADB_DIR / "Logs"),
    "data_dir": str(_PADB_DIR / "Data"),
    "publish_root": r"\\srsnas01.srs.is.keysight.com\prod\MIDRF3\SG6311A\PADB-Simple",
}


def load_defaults() -> dict:
    """This user's padb-tools defaults: built-in fallbacks (derived from
    Path.home(), never hardcoded to one username), overridden by whatever
    keys are actually present in padb_config.json if it exists."""
    cfg = dict(_BUILTIN_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: could not read {CONFIG_PATH}: {exc} -- using built-in defaults")
    return cfg
