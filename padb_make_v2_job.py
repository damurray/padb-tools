"""
padb_make_v2_job.py — Generate V2 (Interactive mode) job.json files from a .pod file

Writes one shared extraction job (`<pod_stem>_run_job.json`, padb_run.py's
schema) plus one plot job per Type=80 Scatter analytic
(`<pod_stem>_<analytic>_v2_job.json`, padb_v2.py's schema) -- mirroring the
existing hand-written MaxPower3 V2 job set.

Usage:
    py padb_make_v2_job.py MyPod.pod --module MyModule
    py padb_make_v2_job.py MyPod.pod --module MyModule --spec-direction lo
    py padb_make_v2_job.py MyPod.pod --no-publish
    py padb_make_v2_job.py MyPod.pod --module MyModule --force

Design notes (see also PADB_Tools_Guide.md):
- "views" is deliberately omitted from every generated plot job -- padb_v2.py
  already auto-detects Room-only (scatter+boxplot) vs multi-temp (all six
  views, including env_coverage/distribution when non-Room/Environmental
  data is present) from the actual extracted CSV at run time. This is the
  existing "Auto view-selection" mechanism (see CLAUDE.md) -- no new
  detection logic needed here.
- Every Type=80 analytic gets its own plot job with the full auto-detected
  view set by default. There's no reliable way to tell from the pod alone
  which of several near-duplicate analytics (e.g. Leveled Log vs Leveled
  Linear vs Unleveled Log/Linear) should get the "primary" full treatment
  vs a lighter scatter-only comparison view, the way the hand-written
  MaxPower3 jobs do -- trim unwanted views by hand afterward if a pod has
  several such variants and you don't want the full set duplicated across
  all of them.
- csv_path is PREDICTED from the analytic's OutputConfig_OutputFile (the
  same naming convention find_csvs() in padb_run.py relies on), not
  verified against a real extraction -- it can't be confirmed correct
  until the run job has actually executed once. Re-check it against the
  real filename in <run_results_dir>\\padb\\ on the first run.
- If any analytics in the pod share one OutputConfig_OutputFile (e.g. the
  Harmonics_and_Subharmonics pod, 13 of 19 analytics sharing one file), the
  generated run job gets "unique_output_filenames": true -- make_run_pod()
  then forces every analytic's AnalyticName/OutputConfig_OutputFile to a
  guaranteed-unique slug in the _run.pod copy (never the original pod), so
  every analytic in the pod (not just the colliding ones) writes its own
  distinct, predictable CSV. csv_path predictions here use the identical
  slug + collision-disambiguation logic make_run_pod() applies, so the two
  stay in sync.
- spec_direction defaults to "auto" -- a measurement that's one-sided
  despite having no configured pod-level spec limits (e.g. MaxPower3's
  guaranteed-minimum-power convention) can't be inferred from the pod
  alone; override with --spec-direction if you know better.
- All plot jobs for one pod share one results_dir and one publish
  destination -- padb_v2.py's _write_index() merges multiple runs into one
  combined gallery, matching the real MaxPower3 example.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import padb_config
from padb_run import parse_pod_analytics, _slugify_name
from padb_simple import parse_pod_sections

DEFAULT_TIMEOUT = 7200


def _y_label(sections: dict, analytic_index: int) -> str:
    """Best-effort Y-axis label: the segment after the last ':' in
    Data_YData, e.g. "...:Measured Power (dBm)" -> "Measured Power (dBm)"."""
    analytic = sections.get(f"PADBAnalytic{analytic_index}", {})
    data_y = analytic.get("Data_YData", "")
    if ":" in data_y:
        return data_y.rsplit(":", 1)[-1].strip()
    return data_y.strip() or "Value"


def _has_output_file_collision(all_analytics: list[dict]) -> bool:
    counts: dict[str, int] = {}
    for a in all_analytics:
        of = a.get("output_file") or ""
        if of:
            counts[of] = counts.get(of, 0) + 1
    return any(c > 1 for c in counts.values())


def _unique_slugs(all_analytics: list[dict]) -> dict[int, str]:
    """The same guaranteed-unique-slug logic make_run_pod() applies when
    unique_output_filenames=True -- kept in sync so predicted csv_path here
    matches what that function will actually write."""
    base = {a["index"]: _slugify_name(a["name"]) for a in all_analytics if a.get("name")}
    counts: dict[str, int] = {}
    for slug in base.values():
        counts[slug] = counts.get(slug, 0) + 1
    return {i: (f"{s}_{i}" if counts[s] > 1 else s) for i, s in base.items()}


def _predict_csv_stem(a: dict, all_analytics: list[dict], unique_slugs: dict[int, str] | None) -> str:
    """Best-effort prediction of the CSV filename PADB will write for this
    analytic. If unique_slugs is set (a pod-wide OutputFile collision was
    detected), every analytic's stem is that guaranteed-unique slug -- this
    is what make_run_pod(unique_output_filenames=True) will force. Otherwise
    mirrors find_csvs()'s own priority: OutputConfig_OutputFile normally,
    falling back to AnalyticName only if OutputFile is missing entirely."""
    if unique_slugs is not None:
        return unique_slugs.get(a["index"], _slugify_name(a.get("name") or ""))
    output_file = a.get("output_file") or ""
    name = a.get("name") or ""
    return (output_file or name).replace(" ", "_")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate V2 (interactive) job.json files from a .pod file")
    parser.add_argument("pod", help="Path to the .pod file")
    parser.add_argument("--module", help="Subfolder under --publish-root. Required unless --no-publish.")
    parser.add_argument("--spec-direction", default="auto", choices=["auto", "lo", "hi", "both", "none"],
                         help='Applied to every generated plot job. Default "auto" -- override if you know '
                              "a measurement is one-sided despite the pod having no configured spec limits.")
    parser.add_argument("--min-date", help='Device_MinDate override on the run job, e.g. "8 weeks ago" or '
                                            '"2026-05-21". Omit to use whatever is baked into the pod.')
    parser.add_argument("--max-date", help='Device_MaxDate override on the run job, e.g. "today". '
                                            "Omit to use whatever is baked into the pod.")
    parser.add_argument("--padb-exe", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--logs-dir", default=None)
    parser.add_argument("--publish-root", default=None,
                         help="Default: this user's padb_config publish_root + '\\Interactive' "
                              "(kept separate from the Simple-mode PADB-Simple tree).")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not args.module and not args.no_publish:
        parser.error("--module is required unless --no-publish is given")

    defaults = padb_config.load_defaults()
    padb_exe = args.padb_exe or defaults["padb_exe"]
    output_dir = args.output_dir or defaults["padb_output_dir"]
    logs_dir = args.logs_dir or defaults["padb_logs_dir"]
    interactive_root = defaults["publish_root"].replace("PADB-Simple", "PADB-Interactive")
    publish_root = None if args.no_publish else (args.publish_root or interactive_root)

    pod_path = Path(args.pod).resolve()
    if not pod_path.exists():
        parser.error(f"pod not found: {pod_path}")
    stem = pod_path.stem

    analytics = parse_pod_analytics(pod_path)
    sections = parse_pod_sections(pod_path)
    scatter_analytics = [a for a in analytics if a.get("type") == 80]
    if not scatter_analytics:
        parser.error(f"No Type=80 Scatter analytics found in {pod_path.name} -- "
                      "V2 plots are built from Scatter CSVs only.")

    run_results_dir = f"{stem}_run_results"
    plot_results_dir = f"{stem}_v2_results"

    has_collision = _has_output_file_collision(analytics)
    unique_slugs = _unique_slugs(analytics) if has_collision else None

    # -- shared extraction job --
    run_job = {
        "description": f"SG6311A {stem} — extract step",
        "pod": pod_path.name,
        "mode": "interactive",
        "padb_exe": padb_exe,
        "results_dir": run_results_dir,
        "padb_timeout": DEFAULT_TIMEOUT,
        "run_analytics": True,
    }
    subex = {}
    if args.min_date:
        subex["Device_MinDate"] = args.min_date
    if args.max_date:
        subex["Device_MaxDate"] = args.max_date
    if subex:
        run_job["subex"] = subex
    run_job["padb_output_dir"] = output_dir
    run_job["padb_logs_dir"] = logs_dir
    if has_collision:
        run_job["unique_output_filenames"] = True
        print(f"NOTE: OutputConfig_OutputFile collides between analytics in this pod -- "
              f"setting \"unique_output_filenames\": true on the run job so every analytic writes "
              f"a guaranteed-unique, AnalyticName-derived CSV instead of depending on the pod's own "
              f"OutputFile values being unique.")
    csv_disabled = [a for a in scatter_analytics if not a.get("output_csv", True)]
    if csv_disabled:
        run_job["force_output_csv"] = True
        names = ", ".join(a.get("name", f"analytic{a['index']}") for a in csv_disabled)
        print(f"NOTE: OutputConfig_OutputCSV=0 on Type=80 analytic(s) [{names}] -- "
              f"setting \"force_output_csv\": true on the run job so PADB writes a CSV for every "
              f"Scatter analytic regardless of the pod's own setting (V2 plots can't be built "
              f"without one).")
    run_job_path = pod_path.with_name(f"{stem}_run_job.json")
    if run_job_path.exists() and not args.force:
        print(f"SKIP (exists, use --force to overwrite): {run_job_path.name}")
    else:
        run_job_path.write_text(json.dumps(run_job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {run_job_path.name}")
        padb_config.warn_if_path_long(run_job_path)

    # -- one plot job per Type=80 analytic, sharing results_dir/publish --
    for a in scatter_analytics:
        name = a.get("name") or a.get("output_file") or f"analytic{a['index']}"
        csv_stem = _predict_csv_stem(a, analytics, unique_slugs)
        predicted_csv = pod_path.parent / run_results_dir / "padb" / f"{csv_stem}.csv"

        plot_job = {
            "description": f"SG6311A {name}",
            "title_prefix": f"SG6311A {name}",
            "y_label": _y_label(sections, a["index"]),
            "csv_path": str(predicted_csv),
            "results_dir": plot_results_dir,
            "index_title": f"SG6311A {stem}",
            "spec_direction": args.spec_direction,
        }
        if publish_root and args.module:
            plot_job["publish_to"] = f"{publish_root}\\{args.module}\\{stem}"

        # Only append the analytic name when there's more than one Type=80
        # analytic to disambiguate between -- with just one, the analytic's
        # own name is usually a near-repeat of the pod's own name (a real
        # case produced a 256-character path, right at Windows' MAX_PATH),
        # and there's nothing else it could be confused with anyway.
        if len(scatter_analytics) > 1:
            plot_job_path = pod_path.with_name(f"{stem}_{_slugify_name(name)}_v2_job.json")
        else:
            plot_job_path = pod_path.with_name(f"{stem}_v2_job.json")
        if plot_job_path.exists() and not args.force:
            print(f"SKIP (exists, use --force to overwrite): {plot_job_path.name}")
            continue
        plot_job_path.write_text(json.dumps(plot_job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {plot_job_path.name}  (csv_path PREDICTED -- verify against "
              f"{run_results_dir}\\padb\\ after running the extraction job)")
        padb_config.warn_if_path_long(plot_job_path)


if __name__ == "__main__":
    main()
