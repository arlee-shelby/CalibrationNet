# Offline pipeline — the same analysis, no database

These three scripts run the pipeline's core chain — trap filter →
spectrum fits → calibrations — entirely from files, for situations
where the GT database is unreachable (first used 2026-08-11 at NERSC
during GT maintenance). They are NOT a fork: the physics comes from the
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
# 1. h5 subruns -> filter CSVs (whole run = segment 0; or provide the
#    dwell windows the database would have supplied)
python scripts/offline/trap_filter.py --h5-dir /pscratch/.../TempCal \
    --run 9416 --out offline_output/filter
#    with segments: --segments segments.csv
#    (columns run,segment,start_time,end_time; ISO timestamps WITH
#     timezone, e.g. 2026-08-10 12:55:48-04:00)

# 2. filter CSVs -> fits CSV + the usual per-fit figures + failure list
python scripts/offline/fit_spectra.py offline_output/filter

# 3. fits + YOUR keV values (e.g. simulation-corrected) -> calibrations
python scripts/offline/calibrate.py offline_output/fits \
    --kev my_bi207_kev.csv
#    kev CSV: label,energy_kev[,energy_err_kev] rows like "CE 482,481.6935,0.0021"
```

Everything lands under `offline_output/` (gitignored): `filter/`,
`fits/`, `fit_plots/` (figures + `fit_failures_summary.csv`),
`calibrations/` (CSV + per-pixel QA figures).

Mind the runtime: filtering is the expensive step (~10 min of compute
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
