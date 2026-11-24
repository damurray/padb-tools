# Examples

- `jobs/` -- reference job files (`*_job.json`). **Templates, not directly runnable from a fresh clone:** the `.pod` each
  one names is not in this repository. Copy one next to your own pod and edit it; `py padb_make_v2_job.py <pod>` writes a
  complete set for a pod. Every key is described in `docs/PADB_Tools_Guide.md` and checked by `padb_jobschema.py`.
- `bands/padb_viewer_bands.json` -- the standard SG6311A frequency bands (DAC / Low / Mid / High). Copy it next to a
  results folder to get the "Named bands" segment step with these bands; `py padb_make_bands.py --sg6311a` writes the same.
