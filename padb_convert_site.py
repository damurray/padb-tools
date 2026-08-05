"""
padb_convert_site.py — Convert a .pod file (or job.json) between PADB database
sites (e.g. Santa Rosa <-> AMC2/Malaysia).

Background: pods pulling the same test results from a different site's PADB
database differ in exactly two [Extract] keys -- Device_Server and
Device_Database. Everything else (analytics, Group strings, spec limits)
stays the same, INCLUDING every AnalyticName/OutputConfig_OutputFile -- which
means running the Santa Rosa and AMC2 versions of the "same" pod writes
identically-named CSVs. If you ever run both (side-by-side comparison, a
shared results/publish location), the second run silently overwrites the
first. This tool avoids that by suffixing every analytic's name/output file
with the target site's tag whenever converting to a non-primary site.

Usage:
    py padb_convert_site.py --pod MyPod.pod --to AMC2
    py padb_convert_site.py --pod MyPod-AMC2.pod --to SantaRosa
    py padb_convert_site.py --job my_job.json --to AMC2
    py padb_convert_site.py --list-sites

Site registry lives in padb_sites.json next to this script -- add a new site
there (suffix + Device_Server + Device_Database) and this tool supports it
immediately, no code changes needed.

Design notes:
- Never overwrites the source .pod/.job -- always writes a new file, same
  convention as make_run_pod() elsewhere in this repo.
- The site whose "suffix" is "" is the *primary* site: its analytic names are
  never suffixed (they're the canonical names everything else disambiguates
  against). Converting TO the primary site strips a known suffix back off if
  present; converting to any other site appends that site's suffix.
- .sao files are a binary PADB format containing site-specific DUT serial
  numbers -- this tool cannot generate one for the target site. It only
  updates SaoFile= to the expected new filename and prints a warning that a
  real .sao for that site still needs to be supplied.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SITES_PATH = Path(__file__).parent / "padb_sites.json"


def load_sites() -> dict:
    with open(SITES_PATH, encoding="utf-8") as f:
        return json.load(f)


def primary_site(sites: dict) -> str:
    for name, cfg in sites.items():
        if cfg.get("suffix", "") == "":
            return name
    raise SystemExit(f"ERROR: no site in {SITES_PATH.name} has an empty \"suffix\" -- "
                      "exactly one site must be the unsuffixed primary")


def detect_site(pod_path: Path, sites: dict) -> str:
    """Identify which registered site a pod currently points at, by matching
    its live Device_Server/Device_Database against the registry. Raises
    rather than guesses if the pod matches no known site or looks ambiguous."""
    server = database = None
    with open(pod_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("Device_Server="):
                server = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("Device_Database="):
                database = stripped.split("=", 1)[1].strip()
    if server is None or database is None:
        raise SystemExit(f"ERROR: {pod_path.name} has no [Extract] Device_Server/"
                          "Device_Database -- not a valid pod, or a section is missing")
    for name, cfg in sites.items():
        if cfg["Device_Server"] == server and cfg["Device_Database"] == database:
            return name
    raise SystemExit(
        f"ERROR: {pod_path.name}'s Device_Server={server!r} / Device_Database={database!r} "
        f"doesn't match any site in {SITES_PATH.name}. Known sites: "
        + ", ".join(f"{n} ({c['Device_Server']!r}/{c['Device_Database']!r})" for n, c in sites.items())
    )


def _strip_known_suffix(stem: str, sites: dict) -> str:
    """Remove a trailing site suffix from a filename stem, if one matches."""
    for cfg in sites.values():
        suf = cfg.get("suffix", "")
        if suf and stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def target_pod_path(src_pod: Path, target_site: str, sites: dict) -> Path:
    base_stem = _strip_known_suffix(src_pod.stem, sites)
    suffix = sites[target_site]["suffix"]
    return src_pod.with_name(f"{base_stem}{suffix}.pod")


def _retag_name(value: str, old_suffix: str, new_suffix: str, sep: str) -> str:
    """Swap a site tag on an AnalyticName/OutputConfig_OutputFile value.
    sep is the separator convention for that field ("_" for OutputFile-style
    identifiers, " " for human-readable AnalyticName text)."""
    if old_suffix:
        old_tag = sep + old_suffix.lstrip("-_")
        if value.endswith(old_tag):
            value = value[: -len(old_tag)]
    if new_suffix:
        new_tag = sep + new_suffix.lstrip("-_")
        if not value.endswith(new_tag):
            value = value + new_tag
    return value


def convert_pod(src_pod: Path, target_site: str, sites: dict, force: bool = False) -> Path:
    source_site = detect_site(src_pod, sites)
    if source_site == target_site:
        raise SystemExit(f"ERROR: {src_pod.name} is already {target_site} -- nothing to convert")

    dest_pod = target_pod_path(src_pod, target_site, sites)
    if dest_pod.exists() and not force:
        print(f"SKIP (exists, use --force to overwrite): {dest_pod.name}")
        return dest_pod

    old_suffix = sites[source_site]["suffix"]
    new_suffix = sites[target_site]["suffix"]
    tgt = sites[target_site]

    with open(src_pod, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    changes: list[str] = []
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""

        if key == "Device_Server":
            line = f"Device_Server={tgt['Device_Server']}\n"
            changes.append(f"Device_Server -> {tgt['Device_Server']}")
        elif key == "Device_Database":
            line = f"Device_Database={tgt['Device_Database']}\n"
            changes.append(f"Device_Database -> {tgt['Device_Database']}")
        elif key == "SaoFile":
            new_name = f"{dest_pod.stem}.sao"
            line = f"SaoFile={new_name}\n"
        elif key == "LastUpdated":
            line = f"LastUpdated={datetime.now().strftime('%m/%d/%Y %I:%M:%S %p')}\n"
        elif key == "AnalyticName":
            old_val = stripped.split("=", 1)[1]
            new_val = _retag_name(old_val, old_suffix, new_suffix, sep=" ")
            if new_val != old_val:
                changes.append(f"AnalyticName {old_val!r} -> {new_val!r}")
            line = f"AnalyticName={new_val}\n"
        elif key == "OutputConfig_OutputFile":
            old_val = stripped.split("=", 1)[1]
            new_val = _retag_name(old_val, old_suffix, new_suffix, sep="_")
            if new_val != old_val:
                changes.append(f"OutputConfig_OutputFile {old_val!r} -> {new_val!r}")
            line = f"OutputConfig_OutputFile={new_val}\n"

        out_lines.append(line)

    dest_pod.write_text("".join(out_lines), encoding="utf-8")

    print(f"Wrote {dest_pod.name}  ({source_site} -> {target_site})")
    for c in changes:
        print(f"  {c}")
    print(f"  WARNING: SaoFile now points at '{dest_pod.stem}.sao', which does not exist yet -- "
          f".sao files are a binary PADB format containing site-specific DUT serial numbers and "
          f"can't be auto-converted. Supply a real .sao extracted at {target_site} before running this pod.")
    return dest_pod


def convert_job(src_job: Path, target_site: str, sites: dict, force: bool = False) -> Path:
    with open(src_job, encoding="utf-8") as f:
        cfg = json.load(f)

    pod_name = cfg.get("pod")
    if not pod_name:
        raise SystemExit(f"ERROR: {src_job.name} has no \"pod\" key -- is this a V2 plot job? "
                          "Convert its companion *_run_job.json instead, then re-run "
                          "padb_make_v2_job.py against the converted pod for the plot-job side.")

    src_pod = (src_job.parent / pod_name).resolve()
    if not src_pod.exists():
        raise SystemExit(f"ERROR: {src_job.name} references pod {pod_name!r}, not found at {src_pod}")

    source_site = detect_site(src_pod, sites)
    if source_site == target_site:
        raise SystemExit(f"ERROR: {src_job.name} already points at {target_site} -- nothing to convert")

    dest_pod = target_pod_path(src_pod, target_site, sites)
    if not dest_pod.exists():
        print(f"Converted pod not found yet -- creating it first:")
        convert_pod(src_pod, target_site, sites, force=force)

    old_stem = src_pod.stem
    new_stem = dest_pod.stem

    def _retag_path_str(value):
        if isinstance(value, str) and old_stem in value:
            return value.replace(old_stem, new_stem)
        return value

    new_cfg = dict(cfg)
    new_cfg["pod"] = dest_pod.name
    for key in ("results_dir",):
        if key in new_cfg:
            new_cfg[key] = _retag_path_str(new_cfg[key])
    if "publish" in new_cfg and isinstance(new_cfg["publish"], dict):
        new_cfg["publish"] = {k: _retag_path_str(v) for k, v in new_cfg["publish"].items()}
    if "description" in new_cfg:
        new_cfg["description"] = _retag_path_str(new_cfg["description"])

    # Job filenames follow <pod_stem>... conventions from padb_make_job.py/padb_make_v2_job.py;
    # swap the pod stem portion the same way we swapped it inside the JSON.
    dest_job_name = src_job.name.replace(old_stem, new_stem)
    if dest_job_name == src_job.name:
        dest_job_name = f"{new_stem}_{src_job.stem}_job.json"
    dest_job = src_job.with_name(dest_job_name)

    if dest_job.exists() and not force:
        print(f"SKIP (exists, use --force to overwrite): {dest_job.name}")
        return dest_job

    dest_job.write_text(json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {dest_job.name}  ({source_site} -> {target_site}, pod={dest_pod.name})")
    if cfg.get("mode") == "interactive":
        print(f"  This is a V2 run job -- also regenerate its plot job(s) against the new pod:")
        print(f"    py padb_make_v2_job.py {dest_pod.name} --module <YourModule>")
    return dest_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a .pod or job.json between PADB database sites")
    parser.add_argument("--pod", help="Path to a .pod file to convert")
    parser.add_argument("--job", help="Path to a job.json to convert")
    parser.add_argument("--to", help="Target site name (see --list-sites)")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    parser.add_argument("--list-sites", action="store_true", help="Print the known site registry and exit")
    args = parser.parse_args()

    sites = load_sites()

    if args.list_sites:
        for name, cfg in sites.items():
            tag = "(primary, no suffix)" if cfg.get("suffix", "") == "" else f"suffix {cfg['suffix']!r}"
            print(f"{name}: Device_Server={cfg['Device_Server']!r} Device_Database={cfg['Device_Database']!r} {tag}")
        return

    if not args.pod and not args.job:
        parser.error("specify --pod or --job (or --list-sites)")
    if args.pod and args.job:
        parser.error("specify only one of --pod / --job at a time")
    if not args.to:
        parser.error("--to SITE is required")
    if args.to not in sites:
        parser.error(f"unknown site {args.to!r} -- known sites: {', '.join(sites)} (see --list-sites)")

    if args.pod:
        convert_pod(Path(args.pod).resolve(), args.to, sites, force=args.force)
    else:
        convert_job(Path(args.job).resolve(), args.to, sites, force=args.force)


if __name__ == "__main__":
    main()
