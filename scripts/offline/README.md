# Offline pipeline — the same analysis, no database

These scripts run the pipeline's core chain — trap filter →
spectrum fits → calibrations — entirely from files, for situations
where the GT database is unreachable (first used 2026-08-11 at NERSC
during GT maintenance) or for a run that should not be stored at all
(a quick look at a single-position run, say). They are NOT a fork: the physics comes from the
same package modules the database pipeline imports —
`calibrationnet/fit_functions.py` (the frozen model),
`calibrationnet/fit_recipes.py` (recipes and the quality check),
`calibrationnet/fitting.py` (the retry/rescue procedure) and
`calibrationnet/calibration.py` (the calibration fit math). Only the
glue differs: files in, files out.

**The database remains the record.** Filter CSVs written here use the
exact cluster staging format, so when the database returns they ingest
unchanged (`scripts/ingest_filter_output.py`), and the runs get refit
and calibrated through the normal pipeline; the offline results then
serve as a cross-check. Scope: Bi-207 (the only isotope with recipes).

## The chain

```bash
# 0. segment windows (multi-dwell runs). The slow-controls computer is
#    SEPARATE from GT and stays reachable during GT downtime — run this
#    on the machine with the slow-controls tunnel + .env (the laptop),
#    then transfer the CSV (it is gitignored, so scp it):
python scripts/offline/export_segments.py 9409 9415 9416 \
    --out offline_output/segments.csv
scp offline_output/segments.csv <nersc>:CalibrationNet/offline_output/

# 1. h5 subruns -> filter CSVs (whole run = segment 0; or the exported
#    dwell windows), as compute jobs, one per segment. On GT, from the
#    repo root with the environment active — a single-position run
#    needs no segments file at all:
./scripts/offline/submit_trap_filter_gt.sh /storage/.../TempCal 9416
./scripts/offline/submit_trap_filter_gt.sh /storage/.../TempCal 9409 offline_output/segments.csv
#    On NERSC:
./scripts/offline/submit_trap_filter_nersc.sh \
    /pscratch/sd/a/ashelby/TempCal offline_output/segments.csv
#    (4 h walltime; a failed task N is redone with sbatch --array=N, or
#    by re-running the submit script). For a smoke test on a login
#    node, a few subruns only:
python scripts/offline/trap_filter.py --h5-dir /pscratch/.../TempCal \
    --run 9416 --subruns 0 2 --out offline_output/filter

# 1b. see where the sources actually sat (recommended before fitting),
#     and what a pixel actually saw (raw ADC spectrum, full + CE zoom):
python scripts/offline/show_hitmap.py offline_output/filter
python scripts/offline/show_spectra.py \
    offline_output/filter/Run9416_seg0_singles_filter_output_rt1250_ft50_fall1250.csv \
    --pixels 63 64 1064

# 2. filter CSVs -> fits CSV + per-fit figures + failure list
#    (statistics-gated pixels get CSV rows but NO figures)
python scripts/offline/fit_spectra.py offline_output/filter

# 3. fits + YOUR keV values (e.g. simulation-corrected) -> calibrations
python scripts/offline/calibrate.py offline_output/fits \
    --kev my_bi207_kev.csv
#    kev CSV: label,energy_kev[,energy_err_kev] rows like "CE 482,481.6935,0.0021".
#    The NNDC values straight from the repo's own data file:
python -c "import pandas as pd; d = pd.read_csv('data/decay_energies.csv'); d[d.isotope == 'Bi-207'][['label', 'energy_kev', 'energy_error_kev']].to_csv('bi207_nndc_kev.csv', index=False)"
#    (data/simulated_energies_Jin_simulations.csv has the simulation-
#    corrected values — specific to the detector and HV they were run at)
```

Everything lands under `offline_output/` (gitignored): `filter/`,
`hitmaps/`, `spectra/`, `fits/`, `fit_plots/` (figures +
`fit_failures_summary.csv`), `calibrations/` (CSV + per-pixel QA
figures).

Branch note: the package folder `calibrationnet/pipeline/` is named
`acquisition/` on the `cleanup` branch and these scripts' imports
follow it there, so always run them from the checkout they came with —
a `main` script against a `cleanup` package (or the reverse) fails at
import.

Resource sizing, GT-vs-NERSC differences, and the job-conversion
checklist live in docs/cluster_resources.md. Mind the runtime:
filtering is the expensive step (~10 min of compute
per ~30 min subrun); a whole long run on a login node is unkind and
slow — use a compute job for full runs, the login node only for a few
subruns' smoke test.

## NERSC environment (proven from scratch 2026-08-11, Perlmutter)

Everything self-contained in ONE folder in `$HOME` (delete it and every
trace is gone), pip-only, no conda, no sudo — using the repo's own
setup script with pyNab and deltarice cloned from their public repos:

```bash
mkdir -p $HOME/pyNabEnv
git clone https://gitlab.com/NabExperiment/pyNab.git   $HOME/pyNabEnv/pyNab
git clone https://gitlab.com/dgma224/deltarice.git     $HOME/pyNabEnv/deltarice

# NERSC-specific build variables: deltarice compiles against the
# cray-hdf5 module's headers, and gcc 14 hard-errors on an upstream
# quirk in deltaRice_h5plugin.c (H5PLget_plugin_info returns the
# filter ID int where the API wants a struct pointer) that clang only
# warns about. The rpath bakes the HDF5 location in, so no module
# load is needed at runtime.
module load cray-hdf5
export HDF5_DIR CFLAGS="-Wno-int-conversion" LDFLAGS="-Wl,-rpath,$HDF5_DIR/lib"

cd $HOME/CalibrationNet     # rsync'd, or cloned once you have access set up
./scripts/setup_env.sh $HOME/pyNabEnv $HOME/pyNabEnv/pyNab $HOME/pyNabEnv/deltarice
```

(The setup script also installs this package's own dependencies —
sqlalchemy among them, which a shared module imports but the offline
chain never connects with: no tunnel and no `.env` file are needed.)
