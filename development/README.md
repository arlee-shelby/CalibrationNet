# Development material — not needed to run the pipeline

Everything in here is kept for reference and provenance only. The live
pipeline (package, scripts, data seeds, docs) never reads from this
directory.

- `inputs/` — files uploaded during development: the sample raw run
  (`Run8622_0.h5`, used to derive the board-channel maps), the original
  nabPy standard filter output CSVs (long since ingested), the
  collaboration's pixel-map example code, and reference hit-map images
  used to validate scripts/show_hitmap.py.
- `notebooks/` — exploration notebooks and `fit_trap_filter_data.py`,
  the pre-pipeline fitting sketch that became scripts/fit_spectra.py.
- `outputs/` — superseded generated files: the first-generation
  per-pixel optimal-position CSVs, the pre-envelope position plan (16 of
  its 36 positions violate the stage motion limits — never feed it to
  the automation), review-CSV manual edits, and old one-off run lists.
  (The LIVE `run_list.txt` — the master batch-processing list read by
  scripts/submit_trap_filter.sh — was moved to the repo root on
  2026-08-10; it is a pipeline input, not development material.)

Current deliverables live in `plans/`; QA figures in `fit_plots/` and
`hitmaps/`; the repo map is docs/repo_layout.md.
