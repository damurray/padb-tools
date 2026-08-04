"""
padb_make_job.py — Generate job.json files from .pod files

Writes a <pod_stem>_job.json next to each given .pod, using the same
template this project's jobs already follow. Any date range in the pod's
own [Extract] section is left untouched unless --min-date/--max-date are
given, in which case they're written into subex verbatim -- including
sentinel strings like "today" or "8 weeks ago" that load_job() in
padb_run.py resolves at run time (see _resolve_date_sentinel()).

Usage:
    py padb_make_job.py pod1.pod [pod2.pod ...] --module MiniMoab
    py padb_make_job.py pod1.pod --module VSWR --min-date "8 weeks ago" --max-date today
    py padb_make_job.py pod1.pod --no-publish   # local results only, no publish key
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import padb_config

# Personal defaults come from padb_config.json if present, else values
# derived from Path.home() (see padb_config.py) -- never hardcoded to one
# username.
_DEFAULTS = padb_config.load_defaults()
DEFAULT_PADB_EXE = _DEFAULTS["padb_exe"]
DEFAULT_OUTPUT_DIR = _DEFAULTS["padb_output_dir"]
DEFAULT_LOGS_DIR = _DEFAULTS["padb_logs_dir"]
DEFAULT_PUBLISH_ROOT = _DEFAULTS["publish_root"]
DEFAULT_TIMEOUT = 7200


def make_job_cfg(
    pod_path: Path, mode: str, module: str | None,
    min_date: str | None, max_date: str | None,
    padb_exe: str, output_dir: str, logs_dir: str, publish_root: str | None,
) -> dict:
    stem = pod_path.stem
    cfg = {
        "description": f"SG6311A {stem} — {mode.capitalize()} mode",
        "pod": pod_path.name,
        "mode": mode,
        "padb_exe": padb_exe,
        "results_dir": f"{stem}_{mode}_results",
        "padb_timeout": DEFAULT_TIMEOUT,
        "run_analytics": True,
    }

    subex = {}
    if min_date:
        subex["Device_MinDate"] = min_date
    if max_date:
        subex["Device_MaxDate"] = max_date
    if subex:
        cfg["subex"] = subex

    cfg["padb_output_dir"] = output_dir
    cfg["padb_logs_dir"] = logs_dir

    if publish_root and module:
        cfg["publish"] = {"destination": f"{publish_root}\\{module}\\{stem}"}

    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate job.json files from .pod files")
    parser.add_argument("pods", nargs="+", help="One or more .pod file paths")
    parser.add_argument("--module", help="Subfolder name under --publish-root (e.g. 'MiniMoab'). "
                                          "Required unless --no-publish is given.")
    parser.add_argument("--mode", default="simple", choices=["legacy", "simple", "interactive"])
    parser.add_argument("--min-date", help='Device_MinDate override, e.g. "8 weeks ago" or "2026-05-21". '
                                            "Omit to use whatever is baked into the pod.")
    parser.add_argument("--max-date", help='Device_MaxDate override, e.g. "today". '
                                            "Omit to use whatever is baked into the pod.")
    parser.add_argument("--padb-exe", default=DEFAULT_PADB_EXE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--logs-dir", default=DEFAULT_LOGS_DIR)
    parser.add_argument("--publish-root", default=DEFAULT_PUBLISH_ROOT)
    parser.add_argument("--no-publish", action="store_true", help="Skip the publish key entirely")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing job.json")
    args = parser.parse_args()

    if not args.module and not args.no_publish:
        parser.error("--module is required unless --no-publish is given "
                      "(avoids silently guessing the wrong publish subfolder)")

    publish_root = None if args.no_publish else args.publish_root

    for pod_str in args.pods:
        pod_path = Path(pod_str).resolve()
        if not pod_path.exists():
            print(f"ERROR: pod not found: {pod_path}")
            continue

        job_path = pod_path.with_name(f"{pod_path.stem}_job.json")
        if job_path.exists() and not args.force:
            print(f"SKIP (exists, use --force to overwrite): {job_path.name}")
            continue

        cfg = make_job_cfg(pod_path, args.mode, args.module, args.min_date, args.max_date,
                            args.padb_exe, args.output_dir, args.logs_dir, publish_root)

        job_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {job_path.name}")
        padb_config.warn_if_path_long(job_path)


if __name__ == "__main__":
    main()
