r"""qa_tiers.py -- the size-tier planner (padb_tiers) and its wiring.

v3.0 stage 5. Checks:

  TIER-MODEL    the fitted time model reproduces every one of the 10 recorded measurements (rows x x-values x
                conditions, 5 views = 50 points) within 45% (worst fitted error is 38%), and is not trivially
                loose: the biggest/smallest predictions keep their order
  TIER-EP5      independent check against the real EP5 build in CLAUDE.md (965,412 rows, 7,662 x; boxplot 619 s,
                stat_summary 228 s, summary 87 s): the condition count solved from the boxplot time predicts the
                other two within 35%
  TIER-POLICY   the routing rules, as a pure function: automatic views over budget are dropped with a reason; explicit
                views are NEVER dropped; 'giant' keeps the single cheapest view; views with no model are never
                judged; the budget is honoured
  TIER-COUNT    conditions are counted over distinct Group strings ignoring serial-like keys
  TIER-BUILD    end to end through padb_v2 / padb_analyze with a tiny budget (so routing happens on small data):
                over-budget automatic views are not built, tier.json + the result JSON + the index banner say so;
                explicit views are all built; `tiering: false` is v2.5 behaviour
  teeth         mutations are recorded in QA_GUIDE

    py qa/qa_tiers.py          -> exit 1 on a failure
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # the repository root (this file lives in qa/)
sys.path.insert(0, str(HERE))
PASS = FAIL = 0

# (rows, distinct x, conditions, {view: measured build seconds}) -- measured 2026-11-24 through padb_analyze, one
# view per run, one machine; case labels D A B C F E G1 G8 G32 H32 in the calibration notes
CALIBRATION = [
    (10000, 100, 4, {"boxplot": 2.9, "stat_summary": 2.6, "summary": 2.4, "scatter": 2.5, "reference": 2.2}),
    (40000, 100, 4, {"boxplot": 3.9, "stat_summary": 2.9, "summary": 2.8, "scatter": 3.1, "reference": 2.7}),
    (160000, 400, 4, {"boxplot": 9.6, "stat_summary": 4.9, "summary": 4.0, "scatter": 7.9, "reference": 4.1}),
    (160000, 100, 4, {"boxplot": 8.0, "stat_summary": 4.2, "summary": 3.9, "scatter": 6.6, "reference": 4.2}),
    (320000, 2000, 4, {"boxplot": 25.8, "stat_summary": 14.6, "summary": 6.3, "scatter": 12.1, "reference": 8.4}),
    (640000, 400, 4, {"boxplot": 23.6, "stat_summary": 11.6, "summary": 9.2, "scatter": 12.8, "reference": 14.2}),
    (4000, 100, 4, {"boxplot": 3.2, "stat_summary": 2.8, "summary": 2.7, "scatter": 2.7, "reference": 2.7}),
    (32000, 100, 32, {"boxplot": 7.2, "stat_summary": 4.5, "summary": 3.0, "scatter": 3.0, "reference": 2.6}),
    (128000, 100, 128, {"boxplot": 22.4, "stat_summary": 9.3, "summary": 5.6, "scatter": 7.4, "reference": 4.7}),
    (512000, 400, 128, {"boxplot": 87.7, "stat_summary": 29.4, "summary": 13.4, "scatter": 19.3, "reference": 11.7}),
]


def check(desc, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS ", desc)
    else:
        FAIL += 1
        print("  FAIL ", desc, ("-- " + str(detail))[:500] if detail else "")


def main() -> int:
    import padb_tiers as T

    # ---- TIER-MODEL
    worst, bad = 0.0, []
    for rows, nx, nc, secs in CALIBRATION:
        for v, s in secs.items():
            p = T.predict_seconds(v, rows, nx, nc)
            rel = abs(p - s) / s
            worst = max(worst, rel)
            if rel > 0.45:
                bad.append((v, rows, nx, nc, s, round(p, 1)))
    check("TIER-MODEL: all %d recorded measurements reproduced within 45%% (worst %.0f%%)"
          % (sum(len(c[3]) for c in CALIBRATION), 100 * worst), not bad, bad[:3])
    big = T.predict_seconds("boxplot", 512000, 400, 128)
    small = T.predict_seconds("boxplot", 4000, 100, 4)
    check("TIER-MODEL: the model is not flat (largest case predicted > 15x the smallest)", big > 15 * small, (big, small))

    # ---- TIER-EP5
    rows, nx = 965412, 7662
    t0, a, b = T.COEF["boxplot"]
    n_cond = max(1, round((619 - t0 - a * rows) / (b * nx)))
    ok = True
    detail = []
    for v, real in (("stat_summary", 228.0), ("summary", 87.0)):
        p = T.predict_seconds(v, rows, nx, n_cond)
        detail.append("%s %.0f s vs real %.0f s" % (v, p, real))
        ok = ok and abs(p - real) / real <= 0.35
    check("TIER-EP5: from the real boxplot time (condition count solved = %d) the other views land within 35%% of "
          "the real EP5 build -- %s" % (n_cond, "; ".join(detail)), ok, detail)

    # ---- TIER-POLICY
    views = ["scatter", "boxplot", "reference", "summary", "stat_summary"]
    std = T.plan(10000, 100, 4, views, False)
    check("TIER-POLICY: small data -> standard, everything built, nothing skipped, no viewer advice",
          std["tier"] == "standard" and std["build"] == views and not std["skipped"] and not std["viewer_recommended"], std)
    heavy = T.plan(512000, 400, 128, views, False, budget_s=60)       # boxplot ~ 90 s is over; the rest are not
    check("TIER-POLICY: automatic views over budget are dropped with a reason (boxplot), the rest built, viewer advised",
          heavy["tier"] == "heavy" and [s["view"] for s in heavy["skipped"]] == ["boxplot"]
          and "boxplot" not in heavy["build"] and len(heavy["build"]) == 4 and heavy["viewer_recommended"]
          and "budget" in heavy["skipped"][0]["reason"], heavy)
    expl = T.plan(512000, 400, 128, views, True, budget_s=60)
    check("TIER-POLICY: EXPLICITLY requested views are never dropped -- only reported", expl["build"] == views
          and not expl["skipped"] and expl["over_budget"] == ["boxplot"] and expl["notes"], expl)
    giant = T.plan(512000, 400, 128, views, False, budget_s=1)
    check("TIER-POLICY: when every candidate is over budget the tier is 'giant' and the single cheapest view is still built",
          giant["tier"] == "giant" and len(giant["build"]) == 1
          and giant["build"][0] == min(views, key=lambda v: giant["predictions"][v]), giant)
    unm = T.plan(512000, 400, 128, ["boxplot", "distribution", "env_coverage"], False, budget_s=60)
    check("TIER-POLICY: views with no fitted model are never judged or dropped",
          "distribution" in unm["build"] and "env_coverage" in unm["build"] and unm["predictions"]["distribution"] is None, unm)
    loose = T.plan(512000, 400, 128, views, False, budget_s=10000)
    check("TIER-POLICY: the budget is honoured (a large budget builds everything)", loose["tier"] == "standard" and loose["build"] == views)

    # ---- TIER-COUNT
    from padb_core.conditions import parse_group
    groups = ["A: 1  Port: x  Serial Number: S1", "A: 1  Port: x  Serial Number: S2", "A: 2  Port: x  Serial Number: S1"]
    n = T.count_conditions(groups, parse_group, lambda k: "serial" in k.lower())
    check("TIER-COUNT: two conditions across three Group strings once the serial is ignored", n == 2, n)

    # ---- TIER-BUILD
    ns = {}
    import qa_viewer_ui as fx
    tmp = Path(tempfile.mkdtemp(prefix="qa_tier_"))
    df = fx.make_fixture(tmp)
    src = tmp / "tierfx.parquet"
    df.to_parquet(src)

    def run(extra_job, tag, views=None):
        """Build through the real padb_v2 entry point with a hand-written job; -> (view names built, tier.json, index)."""
        rd = tmp / tag
        job = {"parquet_path": str(src), "results_dir": str(rd), "publish_to": "", "title_prefix": "tierfx",
               "x_col": "Frequency (MHz)", "auto_bands": False}
        if views:
            job["views"] = views
        job.update(extra_job)
        jp = tmp / (tag + "_job.json")
        jp.write_text(json.dumps(job, indent=2), encoding="utf-8")
        r = subprocess.run([sys.executable, str(HERE / "padb_v2.py"), str(jp), "--no-publish"],
                           capture_output=True, text=True, timeout=1800)
        names = [f.stem for f in rd.glob("*.html") if f.stem != "index"]
        built = {v for v in ("scatter", "boxplot", "reference", "summary", "stat_summary")
                 if any(n.endswith("_" + v) for n in names)}
        tj = rd / "tier.json"
        tier = json.loads(tj.read_text(encoding="utf-8")) if tj.is_file() else None
        idx = (rd / "index.html").read_text(encoding="utf-8") if (rd / "index.html").is_file() else ""
        if not built:
            print("   (no pages: rc=%s) %s" % (r.returncode, (r.stdout + r.stderr)[-500:]))
        return built, tier, idx

    # tiny budget: the boxplot is the slowest predicted view on the fixture and goes over a 2.5 s budget
    v1, t1, idx = run({"tier_budget_s": 2.5}, "auto")
    check("TIER-BUILD: with a tiny budget the over-budget AUTOMATIC views are not built, the cheap ones are",
          v1 and "boxplot" not in v1, (sorted(v1), (t1 or {}).get("predictions")))
    check("TIER-BUILD: tier.json carries the tier, the predictions and a reason per skipped view",
          t1 and t1.get("tier") in ("heavy", "giant") and t1.get("skipped") and all(s.get("reason") for s in t1["skipped"])
          and t1.get("viewer_recommended"), t1)
    check("TIER-BUILD: the index page shows the size-tier banner", "Size tier:" in idx, idx[:200])
    v2, t2, _ = run({"tier_budget_s": 2.5}, "explicit", views=["boxplot", "scatter", "summary"])
    check("TIER-BUILD: the same budget with EXPLICIT views builds them all (and still reports the prediction)",
          {"boxplot", "scatter", "summary"} <= v2 and t2 and t2.get("notes"), (sorted(v2), t2))
    v3, t3, _ = run({"tier_budget_s": 2.5, "tiering": False}, "off")
    check("TIER-BUILD: `tiering: false` is v2.5 behaviour (all automatic views built, no tier record)",
          "boxplot" in v3 and t3 is None, (sorted(v3), t3))
    out = tmp / "viaanalyze"
    r = subprocess.run([sys.executable, str(HERE / "padb_analyze.py"), "--input", str(src), "--out", str(out),
                        "--views", "scatter"], capture_output=True, text=True, timeout=900)
    a = json.loads(r.stdout)["analyses"][0]
    check("TIER-BUILD: padb_analyze's result JSON includes the tier of each analysis",
          isinstance(a.get("tier"), dict) and a["tier"].get("tier") == "standard", a.get("tier"))
    print("\nqa_tiers: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
