# Repo cleanup for public release — the charter

**Working mode (AS ruling 2026-08-20): AS makes the edits; the
assistant explains, answers syntax questions, and verifies safety.**
Goals: (1) readability, (2) AS understands every line, (3) remove
AS-specific development comments, (4) ready the repo for public use.
Development is COMPLETE and signed off (docs/development_plan.md,
"2026-08-20 CLOSING STATE") — every edit in this phase must be
BEHAVIOR-NEUTRAL unless explicitly decided otherwise.

## The per-file loop

1. Pick the next unreviewed file below; read it together — the
   assistant explains anything unclear BEFORE edits start.
2. Check its "imported/used by" column: those are the files an edit
   here can break. Renaming any function/argument means touching all
   of them in the same sitting.
3. AS edits (on the `cleanup` branch, never `main`).
4. Verify, scaled to the risk class:
   - all classes: `git diff` reviewed together — the assistant reads
     the diff and confirms it is behavior-neutral (comments, names,
     formatting) or flags exactly what changed semantically;
     `python -m py_compile <file>`.
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
  decision in development_plan.md, then do it with the full engine
  verification — that is development, not cleanup.

## Session handoff

Start each cleanup session with: "Read docs/cleanup_plan.md and
continue the cleanup from the first unchecked file." The assistant's
persistent memory carries the project rulings; this file carries the
cleanup state. Nothing else needs re-explaining.

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
| `calibrationnet/models/run.py` | calibrationnet/models/run_segment.py | schema |   |
| `calibrationnet/models/run_pixel.py` | calibrationnet/models/calibration.py, calibrationnet/models/pixel.py, calibrationnet/models/run.py, calibrationnet/models/run_segment.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py | schema |   |
| `calibrationnet/models/run_segment.py` | calibrationnet/models/run.py, calibrationnet/models/run_pixel.py | schema |   |
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
