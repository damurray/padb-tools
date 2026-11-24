r"""qa_limits_groupspec.py -- regression: a limit override must win in scatter AND boxplot when the data's spec is
carried in the Group text (PADB's `Upper Spec` / `Upper Uncertainty` grouping items -- the usual case).

Those two views reload their data from a temporary CSV that carries only Upper/Lower Limit; Spec_Hi / uncertainty come
back from the Group text and the classifier prefers Spec, so the data's own spec used to silently beat the override
(found 2026-11-23). No browser.   py qa_limits_groupspec.py   -> exit 1 on failure.
"""
import json, re, sys, tempfile
from pathlib import Path

import pandas as pd

import padb_plots

PASS = FAIL = 0


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS ", desc)
    else:
        FAIL += 1; print("  FAIL ", desc, ("-- " + str(detail))[:400] if detail else "")


def const(path, name):
    h = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^var %s\s*=\s*" % name, h, re.M)
    return json.loads(h[m.end():h.index(";\n", m.end())])


def make_csv(path):
    rows = []
    for sn in ("US1", "US2", "US3"):
        for f in (10.0, 50.0, 100.0, 200.0, 300.0):
            g = ("Serial Number: %s  Temp: Room  Upper Spec (<=): -76.0  Upper Uncertainty (<=): 2.0" % sn)
            rows.append({"Group": g, "Frequency (MHz)": f, "Value (dBc)": -80.0 - (f / 100.0),
                         "Upper Limit": -78.0, "Lower Limit": ""})
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_gs_"))
    csv = tmp / "gs.csv"
    make_csv(csv)
    base = {"title": "gs", "y_label": "dBc", "x_label": "Frequency (MHz)", "x_unit": "MHz"}
    ov = dict(base, limit_overrides=[{"lo": 0, "hi": 400, "upper": -90}])
    for name, fn, const_name, pick in (
        ("scatter", padb_plots.accuracy_vs_freq, "DATA", lambda d: (d[0]["Spec_Hi"], d[0]["Unc_Hi"])),
        ("boxplot", padb_plots.stat_boxplot, "BOX_DATA",
         lambda d: (d[0]["freq_stats"][0]["vals_detail"][0]["spec_hi"], d[0]["freq_stats"][0]["vals_detail"][0]["unc_hi"])),
    ):
        try:
            fn(csv, ov, tmp / (name + "_ov.html"))
            fn(csv, base, tmp / (name + "_plain.html"))
            with_ov = pick(const(tmp / (name + "_ov.html"), const_name))
            plain = pick(const(tmp / (name + "_plain.html"), const_name))
        except Exception as exc:  # noqa: BLE001
            check("GROUPSPEC[%s]: page builds" % name, False, repr(exc)); continue
        check("GROUPSPEC[%s]: override (-90, uncertainty 0) wins over the Group-text spec (-76, 2)" % name,
              with_ov == (-90.0, 0.0), with_ov)
        check("GROUPSPEC[%s] teeth: without the override the page keeps the data's own spec" % name,
              plain == (-76.0, 2.0), plain)
    print("\nqa_limits_groupspec: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
