# Running the full pipeline for a run

`scripts/process_run.py` is the roadmap's end product: one command per
run, from ingest to calibrations. Every stage is idempotent and
pre-checked, so re-running the script continues wherever things stand.

## The stages, in order

1. **Ingest** (`ingest_run.py`): run metadata + segments from slow
   controls and the motion archive. Needs the slow-controls tunnel
   (:15432) and the database (:5432). If both tunnels are already up,
   `python scripts/ingest_run.py <run>` works directly;
   `./scripts/with_sc_tunnel.sh <command>` is only a wrapper that
   opens the slow-controls tunnel for you.
2. **Trap filter**: segments lacking outputs are submitted as a SLURM
   array (needs `--h5-dir` and sbatch, i.e. the cluster) and the
   script EXITS — re-run it when the array drains
   (`pending_segments.py --runs <run> --summary` reports).
3. **Source assignment** (per run; bookkeeping only): writes
   `assignments/review_run_<run>.csv` and applies its non-CHECK rows,
   scoped to this run. Since the gate-only ruling, assignment does
   NOT influence which pixels get fitted — a failure here (e.g. a new
   installation without an anchor) warns and the pipeline continues.
   `--skip-assignment` skips the stage; `scripts/assign_sources.py
   --runs <run>` (then `--apply --runs <run>`) can be run any time.
4. **Fits -> ADC peaks -> calibrations**, per segment, honoring every
   standing rule (gate-only selection, skip-frozen, unweighted
   jin2026a calibrations). QA figures: `fit_plots/run_<run>/`
   (git-ignored, one folder per run).

## Typical usage

```bash
# everything on the cluster (h5 files reachable):
python scripts/process_run.py <run> --h5-dir <h5 path>
# ... trap array submitted, script exits; when drained:
python scripts/process_run.py <run> --h5-dir <h5 path>   # continues

# split: ingest locally (both tunnels up), the rest on the cluster:
python scripts/ingest_run.py <run>                        # local
python scripts/process_run.py <run> --skip-ingest --h5-dir <h5 path>
```

## Defaults and where each knob lives

| what | default | where to change |
|---|---|---|
| trap filter | rt 1250, ft 50, fall 1250 (4 ns bins), singles | `process_run.py --rt/--ft/--fall` |
| trap label | `nabpy-standard` | `--tf-label` |
| statistics gate | 15k CE-window counts / 300 peak height | `calibrationnet/fit_recipes.py::STATS_GATE` |
| fit windows/thresholds | see fit_recipes.py | `calibrationnet/fit_recipes.py` (the ONLY tunable fit file) |
| recipe choice | assigned isotope's recipes, else Bi-207 | gate-only ruling (docs/development_plan.md) |
| extraction match tolerance | 5 keV | `extract_adc_peaks.py --tolerance-kev` |
| calibration targets | label `jin2026a` (Jin 2026a values + per-run HV shift from `runs.hv`) | `calibrate.py --label`; registry in docs/fit_storage.md |
| calibration fit | UNWEIGHTED least squares, linear + quadratic both stored | design ruling — not a knob |
| figures | `fit_plots/run_<run>/` | `process_run.py --plot` |

## Source-assignment CSV policy

The DATABASE is the master record of applied claims. Per-run review
CSVs (`assignments/review_run_<run>.csv`) are the review trail —
git-tracked because the flags are human decisions — and are never
merged into each other. Applying a CSV touches ONLY the runs it
contains. The historical whole-database mode still exists (no
`--runs`: `source_assignment_review.csv`), for global re-derivations.
Baselines/frames/trends are always computed from ALL pooled data, so
per-run output is identical to the global mode's placements.

## A new source installation (tray dismounted / sources swapped)

1. Update `data/source_installations.csv` (which slot holds what) and
   re-seed (`scripts/seed_source_installations.py`).
2. Position plan BEFORE any new data exists: use the previous pool's
   trend with the coverage slots overridden to the new contents, e.g.
   `python scripts/optimal_positions.py --runs 9464 9469
   --slots R1C2 R1C3 R2C2 R2C3 --exclude-rings 6 --tag <name>` —
   valid because the rigid tray's slopes carry over; a dismount can
   shift the frame a few mm, so treat this plan as provisional.
3. Make the production run's FIRST dwell the verification: hitmap vs
   the plan's predicted pixels (10 minutes settles it).
4. Anchor the new installation from one eye-verified segment (add it
   to `calibrationnet/positions.py` ANCHORS) — assignment for the new
   installation's runs works from then on, and the installation's own
   segments feed its trend. Ordinary multi-position production runs
   ARE trend data: no raster is ever needed again.

## Related documents

`docs/pipeline_roadmap.md` (original design) ·
`docs/development_plan.md` (every ruling + closing state) ·
`docs/fit_retry_ladder.md` (how fits are decided) ·
`docs/fit_storage.md` (storage semantics, label registry, the
redevelopment ritual) · `docs/notebook_fitting.md` (interactive
analysis) · `docs/position_planning.md` (the planner).
