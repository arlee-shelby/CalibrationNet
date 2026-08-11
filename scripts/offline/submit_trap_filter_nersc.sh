#!/bin/bash
# Submit the offline trap filter as a SLURM array on NERSC: ONE ARRAY
# TASK PER (run, segment) line of the segments CSV — mirrors the GT
# submission's manifest pattern. Shared QOS: starts sooner, bills only
# the cores used; a ~30 min dwell filters in ~10 min, the multi-hour
# 9416 dwells in ~1-2 h, so 4 h walltime is honest headroom (a task
# that dies is finished by resubmitting just its index).
#
#   ./scripts/offline/submit_trap_filter_nersc.sh <h5 dir> <segments.csv> [out dir]
#
# Run from the CalibrationNet directory on NERSC. Assumes the
# environment from scripts/offline/README.md at $HOME/pyNabEnv.

set -euo pipefail

H5_DIR=${1:?usage: submit_trap_filter_nersc.sh <h5 dir> <segments.csv> [out dir]}
SEGMENTS=${2:?path to segments.csv (scripts/offline/export_segments.py)}
OUT_DIR=${3:-offline_output/filter}
ENV_PY=${ENV_PY:-$HOME/pyNabEnv/bin/python}

mkdir -p "$OUT_DIR/slurmout"

# Manifest: one "run segment" pair per line, from the CSV (header skipped).
MANIFEST="$OUT_DIR/manifest.txt"
tail -n +2 "$SEGMENTS" | awk -F',' '{print $1, $2}' > "$MANIFEST"
N_TASKS=$(wc -l < "$MANIFEST" | tr -d ' ')
echo "$N_TASKS (run, segment) task(s) -> $MANIFEST"

JOB=$(sbatch --parsable <<EOF
#!/bin/bash
#SBATCH --qos=shared
#SBATCH -N1 --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --constraint=cpu
#SBATCH --time=04:00:00
#SBATCH --array=1-${N_TASKS}
#SBATCH --output=${OUT_DIR}/slurmout/trapfilter_%A_%a.out
set -euo pipefail
cd $PWD
LINE=\$(sed -n "\${SLURM_ARRAY_TASK_ID}p" "$MANIFEST")
RUN=\$(echo \$LINE | cut -d' ' -f1)
SEG=\$(echo \$LINE | cut -d' ' -f2)
echo "task \${SLURM_ARRAY_TASK_ID}: run \$RUN segment \$SEG"
$ENV_PY scripts/offline/trap_filter.py --h5-dir "$H5_DIR" \\
    --run \$RUN --segments "$SEGMENTS" --segment \$SEG --out "$OUT_DIR"
EOF
)
echo "submitted array job $JOB"
echo "progress:  squeue -u \$USER"
echo "when done: ls $OUT_DIR/*.csv | wc -l   # expect $N_TASKS files"
echo "a failed task N is redone with: sbatch --array=N (same script)"
