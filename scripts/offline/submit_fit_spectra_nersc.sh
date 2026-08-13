#!/bin/bash
# Submit the offline spectrum fitting as a SLURM array on NERSC: ONE
# ARRAY TASK PER FILTER CSV (= one run segment), the same manifest
# pattern as submit_trap_filter_nersc.sh. All tasks write into one
# output folder: per-segment fits CSVs and figures never collide, and
# the shared failure summary (fit_failures_summary.csv) replaces only
# its own rows per invocation, so concurrent segments are safe.
#
# Sizing: fitting is a SINGLE-CORE lmfit job — the heavy part is just
# reading the filter CSV (up to ~1 GB) into memory, hence 1 cpu / 8 GB.
# A 215-pixel segment fits in a few minutes; 30 min walltime is
# generous headroom (a task that dies is finished by resubmitting just
# its index).
#
#   ./scripts/offline/submit_fit_spectra_nersc.sh [filter dir] [out dir]
#
# Defaults:  filter dir offline_output/filter, out dir
# offline_output/fits_2026. Run from the CalibrationNet directory on
# NERSC. Assumes the environment from scripts/offline/README.md at
# $HOME/pyNabEnv.

set -euo pipefail

FILTER_DIR=${1:-offline_output/filter}
OUT_DIR=${2:-offline_output/fits_2026}
ENV_PY=${ENV_PY:-$HOME/pyNabEnv/bin/python}

mkdir -p "$OUT_DIR/slurmout" "$OUT_DIR/plots"

# Manifest: one filter CSV per line.
MANIFEST="$OUT_DIR/manifest.txt"
ls "$FILTER_DIR"/Run*_singles_filter_output_*.csv > "$MANIFEST"
N_TASKS=$(wc -l < "$MANIFEST" | tr -d ' ')
echo "$N_TASKS filter CSV task(s) -> $MANIFEST"

JOB=$(sbatch --parsable <<EOF
#!/bin/bash
#SBATCH --qos=shared
#SBATCH -N1 --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --constraint=cpu
#SBATCH --time=00:30:00
#SBATCH --array=1-${N_TASKS}
#SBATCH --output=${OUT_DIR}/slurmout/fitspectra_%A_%a.out
set -euo pipefail
cd $PWD
CSV=\$(sed -n "\${SLURM_ARRAY_TASK_ID}p" "$MANIFEST")
echo "task \${SLURM_ARRAY_TASK_ID}: \$CSV"
$ENV_PY scripts/offline/fit_spectra.py "\$CSV" --out "$OUT_DIR" --plot "$OUT_DIR/plots"
EOF
)
echo "submitted array job $JOB"
echo "progress:  squeue -u \$USER"
echo "when done: ls $OUT_DIR/Run*_fits.csv | wc -l   # expect $N_TASKS files"
echo "a failed task N is redone with: sbatch --array=N (same script)"
