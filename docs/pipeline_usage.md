# Running the full pipeline for a run

`scripts/process_run.py` is the roadmap's end product: one command per
run, from ingest to calibrations. Every stage is idempotent and
pre-checked, so re-running the script continues wherever things stand.

## Quick start: the commands, in order

Once per installation, before the first run taken with it: sources and
the installation seeded ("A new source installation" below). Then, per
run:

```bash
# laptop (slow-controls tunnel + database tunnel): run metadata + segments
./scripts/with_sc_tunnel.sh python scripts/ingest_run.py <run>

# GT, repo root, environment active, checkout current (git pull):
# everything else as one hands-off job chain (trap array -> fit array -> extraction + calibration)
./scripts/process_run.sh <run> <h5 path>

# watch, then look
squeue -u $USER
python scripts/pending_segments.py --runs <run> --summary   # trap outputs still missing?
python scripts/plot_stored_fits.py --run <run>               # QA figure for every stored fit
```

Per-detector runs or non-default trap settings: see that section
below. A run that must stay out of the database: see "Offline" at the
end. What each stage does, and the knobs, follow.

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

# cluster one-shot: everything after ingest as a single hands-off
# SLURM job chain (job -> trap array -> job -> fit array -> job):
./scripts/process_run.sh <run> <h5 path>
```

`process_run.sh` runs the fit stage as a SLURM array (one task per
segment, all parallel — `--fits-via-array`), resubmitting itself
behind each array with a dependency, so wall-clock is about the
slowest single segment regardless of run size. Extraction and
calibration run serially in the final job (seconds per segment).
Note fit failures are never stored, so re-running a finished run
still re-attempts its hard-failure pixels through the full retry
ladder — a re-run is NOT fast just because everything is frozen.

Re-runs also only plot what they actually fit (frozen fits are kept,
not re-fitted). To regenerate the QA figure for every STORED fit of
a run without refitting: `python scripts/plot_stored_fits.py --run
<run>` (draws from the database into `fit_plots/run_<run>/`,
identical to the fit-time figures plus a "stored" tag).

## When ingest says the run is not in the slow-controls database

`ingest_run.py` takes a run's time window and subrun count from
`runlog.status` in the slow-controls database. A run that was aborted or
restarted before the DAQ closed it has no row there (first seen: run
10081, which left no trace while 10078 and 10082 around it did), and
ingest stops with "Run <N> not found in the slow-controls database".
Everything else ingest stores comes from archives indexed by TIME, not
by run number — settings from the Test archive, dwell segments from the
motion archive — so the run can still be ingested once the window is
supplied. `scripts/ingest_run_from_files.py` does that, then runs the
same segment derivation and settings fetch as a normal ingest:

```bash
# the window from the data: first and last waveform timestamps of the
# run's h5 files (on GT, where the files are; slow-controls tunnel open)
python scripts/ingest_run_from_files.py 10081 --h5-dir <h5 dir> --dry-run
python scripts/ingest_run_from_files.py 10081 --h5-dir <h5 dir>

# or typed, from the laptop with both tunnels up (ISO times WITH timezone)
python scripts/ingest_run_from_files.py 10081 \
    --start "2026-10-07 18:02:11-04:00" --end "2026-10-08 00:14:50-04:00" --subruns 1722
```

`--dry-run` prints the window, the settings found and every segment with
its position, then rolls back; drop it to store. The settings come from
where a normal ingest reads them — the slow-controls instrument tables
averaged over the window (the magnet currents live only there), then
the Test archive for the 2026-epoch values — and the subrun count
stored is the number of subruns (last index + 1), as `ingest_run`
stores it. From there the run goes through the normal chain
(`process_run.sh`). Confirm the run number against the files or the
elog first: a run with no files either simply never produced data.

The slow-controls tunnel for either machine: `with_sc_tunnel.sh`
expects a `bl13-replay` alias in `~/.ssh/config` (the entries are in its
header) and key-based login on both hops, or open the tunnel by hand in
a second terminal with the passwords —
`ssh -N -J <you>@analysis.sns.gov -L 15432:localhost:5432 nabreplay@bl13-replay.sns.gov`
— and run the script plainly.

## Per-detector runs / non-default trap settings

The default processes both detectors at `nabpy-standard`. When one
detector needs its own trap settings (the Fall 2025 LDET short trap
is the standing example), give the label AND its settings AND the
detector — the trap stage filters at those settings, the fit stage
fits only that detector, and extraction + calibration scope by the
trap label automatically:

```bash
./scripts/process_run.sh <run> <h5 path> \
    --tf-label short-trap-Fall2025 --rt 150 --ft 20 --fall 1250 \
    --detector ldet
```

(All three of `--rt/--ft/--fall` matter: the pending-check and the
trap array both use them, and a label must always mean one set of
settings — check an existing label's settings with
`select distinct label, trap_rise, trap_flattop, trap_falltime from
trap_filter_outputs`.) The same flags work with `process_run.py`
directly. Run the default pass and a per-detector pass independently
— calibrations are keyed by trap output, so the two never collide.

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

Do steps 1 and 2 BEFORE ingesting the first run taken with the new
installation: `process_run.py`'s preflight stops with "no source
installation covers <date>" otherwise. Both seeders need the database
tunnel.

1. **New sources, if any.** Add them to `data/Current CAL2702 Sources.xlsx`
   (two columns: Source ID like `Bi-207-9176`, Serial number like
   `Y2-743`), then `python scripts/seed_sources.py` — idempotent, only
   unknown labels are created. This comes first: the installation
   seeder refuses a source label it does not know.
2. **The installation.** In `data/source_installations.csv` put the new
   date in `removed_on` for every row of the previous installation and
   add one row per occupied slot: `installed_on`, source label, slot in
   the Facing UP convention (README "Source frame slot convention"),
   holder, notes. Empty slots have no row. Then
   `python scripts/seed_source_installations.py` — it wipes and
   re-inserts the whole table from the CSV, so the file is the record.
   Commit the spreadsheet and the CSV.
3. **Position plan BEFORE any new data exists:** use the previous pool's
   trend with the coverage slots overridden to the new contents, e.g.
   `python scripts/optimal_positions.py --runs 9464 9469
   --slots R1C2 R1C3 R2C2 R2C3 --exclude-rings 6 --tag <name>` —
   valid because the rigid tray's slopes carry over; a dismount can
   shift the frame a few mm, so treat this plan as provisional.
4. **Make the production run's FIRST dwell the verification:** hitmap vs
   the plan's predicted pixels (10 minutes settles it).
5. **The verified tray position for the new mounting.** From that one
   eye-verified segment (`python scripts/show_hitmap.py`) add an entry
   to `VERIFIED_TRAY_POSITIONS` in `calibrationnet/positions.py`. Until
   it exists, source assignment for the new installation's runs warns
   and is skipped — fits and calibrations are unaffected (gate-only
   fitting). From then on `python scripts/assign_sources.py --runs <run>`
   followed by `--apply --runs <run>` places the sources (or
   `process_run` does it in passing), and the installation's own
   segments feed its trend. Ordinary multi-position production runs
   ARE trend data: no raster is ever needed again.

## Merged segments: when a raster's dwells are too short for the fit gate

A sweep with steps smaller than a pixel (run 10075: 81 dwells of 5 min,
0.25 inch apart) illuminates every pixel for several consecutive dwells,
none of which alone reaches the 15,000-count gate. Merging the dwells
PER PIXEL is physically clean — same electronics, same isotope, same run
conditions; where the source sat within the pixel changes how many
counts it collected, not where its lines fall — and turns the raster
into a calibration of both faces. `scripts/merge_segments.py` builds one
derived segment whose per-pixel output is the concatenation of the
member dwells' energies (done inside the database), under its own trap
label so nothing asking for the recorded label ever sees it:

```bash
python scripts/merge_segments.py --run 10075 --segments 0-80 --merged-label nabpy-standard-raster --dry-run
python scripts/merge_segments.py --run 10075 --segments 0-80 --merged-label nabpy-standard-raster
# then the three stages on the merged segment, directly (a long fit: one SLURM job)
python scripts/fit_spectra.py       --run 10075 --segment -1 --tf-label nabpy-standard-raster --plot fit_plots/run_10075
python scripts/extract_adc_peaks.py --run 10075 --segment -1 --tf-label nabpy-standard-raster
python scripts/calibrate.py         --run 10075 --segment -1 --tf-label nabpy-standard-raster --plot fit_plots/run_10075
```

Conventions: a merged segment has a NEGATIVE index (default -1; no
recorded dwell can be negative, so the sign says "merged"), the members'
time span, no position (a merged dwell has no single one), and a note
naming its members. `process_run.py` and `pending_segments.py` skip
negative segments, so a chain re-run never tries to filter one from the
h5 files; source assignment and the position planner are label-scoped
and never meet them. Every fit and calibration built on it carries the
merged label, which is how the trend queries tell it apart. Merge the
raster dwells only and leave a long stationary dwell (10075's 504-minute
centred dwell) as its own segment: the pixels under the sources then get
two independent calibrations, a free consistency check. Re-running the
merge with `--force` replaces the derived segment and everything built
on it.

## Offline: a run that must NOT go into the database

`scripts/offline/` runs the same filter -> fits -> calibration chain
files-in, files-out: same package modules, no tunnel, nothing stored
(full description, the dwell-run variant and the NERSC environment:
scripts/offline/README.md). For a single-position run on GT, from the
repo root with the environment active:

```bash
./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run>     # the whole run = segment 0, filtered in parallel parts and merged into one CSV
python scripts/offline/show_hitmap.py offline_output/filter    # where the sources sat
python scripts/offline/show_spectra.py offline_output/filter/Run<run>_seg0_singles_filter_output_rt1250_ft50_fall1250.csv --pixels <pixels>
python scripts/offline/fit_spectra.py offline_output/filter    # fits CSV + figures (Bi-207 recipes)
python scripts/offline/calibrate.py offline_output/fits --kev bi207_nndc_kev.csv
```

Everything lands under `offline_output/` (gitignored). The filter CSVs
use the cluster staging names, so if the run should end up in the
database after all, `scripts/ingest_filter_output.py` takes them
unchanged and the normal pipeline refits from there.

## Related documents

`docs/pipeline_roadmap.md` (original design) ·
`docs/development_plan.md` (every ruling + closing state) ·
`docs/fit_retry_ladder.md` (how fits are decided) ·
`docs/fit_storage.md` (storage semantics, label registry, the
redevelopment ritual) · `docs/notebook_fitting.md` (interactive
analysis) · `docs/position_planning.md` (the planner).
