# QA

All test code for padb-tools lives here. Run everything from the repository root:

```
py qa/qa_selfcheck.py          # the umbrella gate: every suite, scored against qa_baseline.json (GREEN / RED / AMBER)
py qa/qa_core.py               # or any single suite
```

The scripts find the repository from their own location, so they also run from any other working directory.
`qa_baseline.json` is the last known-good count per suite; update it (`py qa/qa_selfcheck.py --update-baseline`) when a
suite legitimately gains checks. **`QA_GUIDE.md` describes every suite, what it proves and how each was shown to catch a
broken version** (QA here must have teeth). `QA_Checklist.md` is the manual browser checklist.

Suites that need a browser (Playwright + Chromium) honest-exit with code 3 when none is installed (reported as skipped,
never as passed). Some suites also use the team's real job files / pods from the OneDrive Data folder and skip cleanly
when it is absent.
