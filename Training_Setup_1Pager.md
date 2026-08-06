# padb-tools Training — Setup Checklist

One page. Do this *before* the session so you can follow along live through all three tutorials (Tutorial 1 — PADB Simple, Tutorial 2 — PADB Interactive, Tutorial 3 — `/padb-tools`).

---

## 1. Prerequisites (once, on your own machine)

- [ ] **PADB-R.NET** installed at `C:\Program Files\KEYSIGHT\PADB-R.NET\PADB-R.exe` (ask IT if missing)
- [ ] **Oracle DB / VPN access** — PADB-R.exe extracts live from Oracle; you need real connectivity, not just the app installed
- [ ] **Python** on PATH (`py --version` works in a terminal)
- [ ] Install the packages every script needs:
  ```
  py -m pip install pandas numpy matplotlib scipy plotly flask
  ```
- [ ] **Claude Code** installed and working, if you plan to follow Tutorial 3 (`/padb-tools`)

## 2. Get your own clean copy of the tools

The shared `Padb\Data` folder most of us use day-to-day is genuinely busy (100+ pods/jobs) — not a good place to learn on. Set up an isolated sandbox instead:

- [ ] Copy the whole `tools` folder to **`C:\temp\tools`** (a plain folder copy — no install step)
- [ ] Create an empty folder: **`C:\temp\data`**
- [ ] Create (or edit) this file — note it's always in this *exact* location regardless of where you put `tools`:
  `%USERPROFILE%\OneDrive - Keysight Technologies\Documents\Padb\padb_config.json`
  ```json
  { "data_dir": "C:\\temp\\data" }
  ```
  This one setting redirects every script (web app, CLI, scheduler) to treat `C:\temp\data` as home — your own sandbox, isolated from everyone else's.

## 3. Get the tutorial pods

Ask the trainer for these 6 files (3 pods + their `.sao` companions) and drop them straight into `C:\temp\data`:

- [ ] `MaxPowerTutorial1.pod` / `.sao`
- [ ] `MaxPowerTutorial2.pod` / `.sao`
- [ ] `MaxPowerTutorial3.pod` / `.sao`

## 4. Launch the web app once, to confirm it all works

```
py C:\temp\tools\webapp\padb_web.py
```

This opens `http://127.0.0.1:5000` in your browser. Confirm:
- [ ] The page loads with no errors
- [ ] Dropping `MaxPowerTutorial1.pod` onto the page shows its analytics (6 rows) — if this works, extraction/plotting will too

## 5. What each tutorial will actually ask you to do

| | Mode | Pod | What you'll do |
|---|---|---|---|
| Tutorial 1 | PADB Simple | `MaxPowerTutorial1.pod` | Drop pod → Generate Job (mode: simple) → Run it → view the native PNG/PDF gallery |
| Tutorial 2 | PADB Interactive | `MaxPowerTutorial2.pod` | Drop pod → Generate Job (mode: interactive) → Run the extraction job → auto-builds the interactive plot gallery |
| Tutorial 3 | `/padb-tools` | `MaxPowerTutorial3.pod` | Open Claude Code in `C:\temp\tools`, ask it questions about the pod via `/padb-tools` — no web app needed for this one |

If steps 1–4 above are done, you're ready to follow along live for all three.
