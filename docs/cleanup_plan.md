# Repo cleanup for public release — the charter

**Working mode (AS ruling 2026-08-20): AS makes the edits; the
assistant explains, answers syntax questions, and verifies safety.**
Goals: (1) readability, (2) AS understands every line, (3) remove
AS-specific development comments, (4) ready the repo for public use.
Development is COMPLETE and signed off (docs/development_plan.md,
"2026-08-20 CLOSING STATE") — every edit in this phase must be
BEHAVIOR-NEUTRAL unless explicitly decided otherwise.

**"Behavior-neutral" means:** the program does exactly the same
thing after the edit — same results, same files, same database rows,
byte-identical outputs. Comments, docstrings, blank lines, and
variable RENAMES (applied consistently everywhere) are neutral;
anything that changes a value, a condition, an argument default, an
order of operations, or an output format is NOT — that is a behavior
change and follows the escape hatch below.

## The per-file loop

1. **AS picks the file** — any order is fine; the loop is
   self-contained per file. The one caution: files that form a tight
   cluster (a rename in one ripples into its importers) are best
   done in the same sitting — the dependency column shows the
   cluster.
2. Check its "imported/used by" column — BUT treat the table as an
   index, not the truth: before certifying any rename or signature
   change, the assistant re-greps the LIVE imports/usages of the
   touched names across the repo (the table can go stale as cleanup
   progresses). Regenerate the whole table any time by asking the
   session — it is derived purely from the import statements.
3. AS edits (on the `cleanup` branch, never `main`). This phase is
   interactive, not one-shot (lesson from run.py, the first file):
   AS asks questions about constructs and conventions while editing,
   the assistant answers (and logs Python/library explanations to
   docs/python_notes.md), and findings that surface go to
   docs/cleanup_findings.md. The assistant does NOT start the diff
   review until AS explicitly says they are done editing the file —
   partial-state reviews waste effort and confuse both sides.
4. When AS declares the file done, verify, scaled to the risk class:
   - all classes: `git diff` reviewed together — the assistant reads
     the diff and confirms it is behavior-neutral (comments, names,
     formatting) or flags exactly what changed semantically;
     `python -m py_compile <file>`. The diff review ALSO checks for
     KNOWLEDGE LOSS: any deleted comment carrying non-obvious
     understanding (a why, a constraint, a lesson) must be relocated
     to its durable home (see "Where the lore lives"), never just
     dropped.
   - **engine** (calibrationnet/*.py): additionally
     `python scripts/benchmark_fits.py --check-only`, and for
     fitting.py/fit_recipes.py also `--reference-pixels`
     (needs the GT tunnel; pulls must stay 0.000).
   - **driver** (scripts/): additionally one smoke run on a reference
     pixel (e.g. `fit_spectra.py --run 8622 --pixels 60 --no-plot`,
     or the script's own cheapest invocation).
   - **schema** (models/): comments/docstrings ONLY — any column or
     constraint change is a migration, i.e. development, not cleanup.
5. Tick the file off below (edit this document), commit with a
   `cleanup:` prefixed message, move on.

## Hard rules

- `calibrationnet/fit_functions.py` and `fit_functions_reference.py`
  are FROZEN — never edited, byte-identical, md5-checked by
  `benchmark_fits.py --check-only`. Not even comments.
- `alembic/versions/*` are history — never edited.
- Comment policy for public release: comments explain what the code
  cannot say itself (physics constraints, units, conventions).
  Date-stamped "AS ruling" development notes move to
  docs/development_plan.md if worth keeping, else delete. Docstrings
  stay complete — they are the public documentation.
- If an edit turns out to need behavior change: STOP, record it as a
  decision in **docs/cleanup_findings.md** (per-file log of everything
  the cleanup turns up — AS ruling 2026-08-24: development_plan.md is
  closed and stays closed), then do it with the full engine
  verification — that is development, not cleanup.

## Where the lore lives (the development nuance is NOT only in
## old chat sessions — each arc has a durable home)

- The complete development timeline, every ruling with its evidence,
  every found-and-fixed issue, ops lessons, closing state:
  **docs/development_plan.md** (closed as of 2026-08-20; anything
  worth keeping from deleted comments moves to the matching topic doc
  below, or to cleanup_findings.md if it belongs nowhere else).
- Everything FOUND during cleanup — latent bugs, stale docs,
  escape-hatch decisions with their evidence and verification —
  per file: **docs/cleanup_findings.md**.
- The retry ladder — passes, rungs, gates, quality check, why:
  **docs/fit_retry_ladder.md**.
- Storage semantics, label registry, freeze protections, the
  redevelopment ritual: **docs/fit_storage.md**.
- Position planning method (+ the shared-trend saga: plan item 11):
  **docs/position_planning.md** and development_plan.md.
- Source assignment design (joint method, pools, anchors, claims):
  the module docstrings in calibrationnet/pipeline/
  source_assignment.py + scripts/assign_sources.py, and the
  assistant's persistent memory.
- Notebook analysis how-to: **docs/notebook_fitting.md**.
- Original architecture: **docs/pipeline_roadmap.md**.

If during cleanup something important seems to live NOWHERE durable,
that is a gap: stop and write it into the right document above
before deleting anything. And the long development session itself
remains consultable — it can be resumed to ask "why did we do X",
even after cleanup sessions have started.

## Session handoff

Start each cleanup session with: "Read docs/cleanup_plan.md; we
are cleaning <file>." (AS chooses the file.) The assistant's
persistent memory carries the project rulings; this file carries the
cleanup state. Nothing else needs re-explaining.

Session scope (AS + assistant, 2026-08-24): one session per
DIRECTORY/CLUSTER, not per file — the files in a directory share
conventions (models/ especially), and the shared context makes later
files in the batch go faster and stay consistent. Start a fresh
session when switching directories, or sooner if the session gets
sluggish or unfocused.

## Known items to address during cleanup

**`recipe_isotope` backfill (decide when cleaning
scripts/extract_adc_peaks.py).** The config key `recipe_isotope` was
added with gate-only fitting (2026-08-18); the bulk Fall 2025
campaign fits (2026-08-13/14) predate it — 1377 of 1505 stored fits
lack it. Extraction falls back to the pixel's assigned source; a
pixel with neither (10 pixels DB-wide, e.g. 8622 p80) prints
"skipped (fits record no isotope and no source is assigned)".
Verified benign 2026-08-24: every missing-isotope fit is FROZEN
(peaks referenced by a jin2026a calibration) and none has zero
peaks — the skip changes nothing. Options:

1. RECOMMENDED — backfill `recipe_isotope: "Bi-207"` on the 1377 old
   configs (a fact, not an invention: ce-6peak/auger-2peak ARE the
   Bi-207 recipes; every pre-gate-only fit used them). One-time
   UPDATE script; afterwards every fit is self-describing, the
   source-assignment fallback in extract_adc_peaks.py becomes dead
   code to delete, and the message can never recur.
2. Cosmetic only — reorder extraction so the frozen check precedes
   isotope resolution ("kept (frozen)" instead of the skip); the
   fallback stays.

Either way the redevelopment ritual is unaffected (it re-FITS, which
writes modern configs).

**`number_subruns` = lastsubrun + 1 (check when cleaning
calibrationnet/pipeline/slow_controls.py).** The run-ingestion query
stores `lastsubrun + 1 AS number_subruns` on the premise that
lastsubrun is 0-indexed. Verify the premise when cleaning that file —
e.g. count the actual Run<r>_<subrun>.h5 files for a couple of runs
and confirm the count equals the stored number_subruns.

## File inventory — tick when cleaned ([ ] -> [x])

### calibrationnet/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/__init__.py` | (nothing imports it — leaf) | engine |   |
| `calibrationnet/calibration.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/run_pixel.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py, scripts/calibrate.py, scripts/offline/calibrate.py | engine |   |
| `calibrationnet/db.py` | calibrationnet/pipeline/source_assignment.py, calibrationnet/queries.py, scripts/apply_trap_filter.py, scripts/assign_sources.py, scripts/benchmark_fits.py, scripts/calibrate.py, scripts/extract_adc_peaks.py, scripts/fit_spectra.py, scripts/ingest_board_channels.py, scripts/ingest_filter_output.py, scripts/ingest_run.py, scripts/low_gain_report.py, scripts/optimal_positions.py, scripts/pending_segments.py, scripts/process_run.py, scripts/seed_decay_energies.py, scripts/seed_pixels.py, scripts/seed_source_installations.py, scripts/seed_sources.py, scripts/show_hitmap.py | engine |   |
| `calibrationnet/fit_functions.py` | calibrationnet/fitting.py, calibrationnet/queries.py, scripts/benchmark_fits.py | FROZEN |   |
| `calibrationnet/fit_functions_reference.py` | scripts/benchmark_fits.py | FROZEN |   |
| `calibrationnet/fit_recipes.py` | calibrationnet/fitting.py, calibrationnet/queries.py, scripts/benchmark_fits.py, scripts/fit_spectra.py, scripts/low_gain_report.py, scripts/offline/calibrate.py, scripts/offline/fit_spectra.py | engine |   |
| `calibrationnet/fitting.py` | scripts/fit_spectra.py, scripts/offline/fit_spectra.py | engine |   |
| `calibrationnet/geometry.py` | calibrationnet/hitmap.py, calibrationnet/pipeline/source_assignment.py, calibrationnet/positions.py, scripts/assign_sources.py, scripts/optimal_positions.py | engine |   |
| `calibrationnet/hitmap.py` | scripts/offline/show_hitmap.py, scripts/show_hitmap.py | engine |   |
| `calibrationnet/positions.py` | calibrationnet/pipeline/ingest.py, calibrationnet/pipeline/source_assignment.py, scripts/optimal_positions.py | engine |   |
| `calibrationnet/queries.py` | scripts/fit_spectra.py | engine |   |

### calibrationnet/models/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/models/__init__.py` | (nothing imports it — leaf) | schema |   |
| `calibrationnet/models/adc_peak.py` | calibrationnet/models/calibration.py, calibrationnet/models/source.py, calibrationnet/models/spectrum_fit.py | schema |   |
| `calibrationnet/models/base.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/calibration.py, calibrationnet/models/pixel.py, calibrationnet/models/run.py, calibrationnet/models/run_pixel.py, calibrationnet/models/run_segment.py, calibrationnet/models/source.py, calibrationnet/models/spectrum_fit.py, calibrationnet/models/trap_filter_output.py | schema |   |
| `calibrationnet/models/calibration.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/run_pixel.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py, scripts/calibrate.py, scripts/offline/calibrate.py | schema |   |
| `calibrationnet/models/covariance.py` | calibrationnet/models/calibration.py, calibrationnet/models/spectrum_fit.py | schema |   |
| `calibrationnet/models/pixel.py` | calibrationnet/models/run_pixel.py, scripts/optimal_positions.py | schema |   |
| `calibrationnet/models/run.py` | calibrationnet/models/run_segment.py | schema | x |
| `calibrationnet/models/run_pixel.py` | calibrationnet/models/calibration.py, calibrationnet/models/pixel.py, calibrationnet/models/run.py, calibrationnet/models/run_segment.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py | schema |   |
| `calibrationnet/models/run_segment.py` | calibrationnet/models/run.py, calibrationnet/models/run_pixel.py | schema | x |
| `calibrationnet/models/source.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/calibration.py, calibrationnet/models/run_pixel.py | schema |   |
| `calibrationnet/models/spectrum_fit.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/trap_filter_output.py | schema |   |
| `calibrationnet/models/trap_filter_output.py` | calibrationnet/models/calibration.py, calibrationnet/models/run_pixel.py, calibrationnet/models/spectrum_fit.py | schema |   |

### calibrationnet/pipeline/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/pipeline/__init__.py` | (nothing imports it — leaf) | engine |   |
| `calibrationnet/pipeline/board_channels.py` | scripts/apply_trap_filter.py, scripts/ingest_board_channels.py | engine |   |
| `calibrationnet/pipeline/ingest.py` | scripts/ingest_run.py, scripts/offline/export_segments.py | engine |   |
| `calibrationnet/pipeline/motion_control.py` | calibrationnet/pipeline/ingest.py | engine |   |
| `calibrationnet/pipeline/slow_controls.py` | calibrationnet/pipeline/ingest.py, scripts/offline/export_segments.py | engine |   |
| `calibrationnet/pipeline/source_assignment.py` | scripts/assign_sources.py, scripts/optimal_positions.py | engine |   |
| `calibrationnet/pipeline/trap_filter.py` | scripts/apply_trap_filter.py, scripts/ingest_filter_output.py, scripts/offline/fit_spectra.py, scripts/offline/show_hitmap.py, scripts/offline/show_spectra.py, scripts/pending_segments.py | engine |   |
| `calibrationnet/pipeline/waveforms.py` | scripts/apply_trap_filter.py, scripts/offline/trap_filter.py | engine |   |
| `calibrationnet/pipeline/wiring.py` | scripts/seed_pixels.py | engine |   |

### scripts/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `scripts/apply_trap_filter.py` | (nothing imports it — leaf) | driver |   |
| `scripts/assign_sources.py` | (nothing imports it — leaf) | driver |   |
| `scripts/benchmark_fits.py` | (nothing imports it — leaf) | driver |   |
| `scripts/calibrate.py` | (nothing imports it — leaf) | driver |   |
| `scripts/extract_adc_peaks.py` | (nothing imports it — leaf) | driver |   |
| `scripts/extract_bc_maps.py` | scripts/ingest_board_channels.py | driver |   |
| `scripts/fit_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/ingest_board_channels.py` | scripts/apply_trap_filter.py | driver |   |
| `scripts/ingest_filter_output.py` | (nothing imports it — leaf) | driver |   |
| `scripts/ingest_run.py` | (nothing imports it — leaf) | driver |   |
| `scripts/low_gain_report.py` | (nothing imports it — leaf) | driver |   |
| `scripts/optimal_positions.py` | (nothing imports it — leaf) | driver |   |
| `scripts/pending_segments.py` | (nothing imports it — leaf) | driver |   |
| `scripts/process_run.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_decay_energies.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_pixels.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_source_installations.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_sources.py` | (nothing imports it — leaf) | driver |   |
| `scripts/show_hitmap.py` | (nothing imports it — leaf) | driver |   |
| `scripts/apply_trap_filter.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/calibrate.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/fit_spectra.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/ingest_filter_outputs.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/setup_env.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/submit_fit_spectra.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/submit_trap_filter.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/with_sc_tunnel.sh` | (shell — called by AS on clusters) | driver |   |

### scripts/offline/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `scripts/offline/calibrate.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/export_segments.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/fit_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/show_hitmap.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/show_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/trap_filter.py` | scripts/apply_trap_filter.py, scripts/ingest_filter_output.py, scripts/offline/fit_spectra.py, scripts/offline/show_hitmap.py, scripts/offline/show_spectra.py, scripts/pending_segments.py | driver |   |
| `scripts/offline/submit_fit_spectra_nersc.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/offline/submit_trap_filter_nersc.sh` | (shell — called by AS on clusters) | driver |   |

### docs/ (prose pass at the end: consistency, remove AS-specific
### references, add a public-facing README)
