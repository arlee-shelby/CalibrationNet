#!/bin/bash
# Submit the OFFLINE trap filter for one run as a SLURM job on GT — files
# in, files out, the database is never touched. The GT counterpart of
# submit_trap_filter_nersc.sh, with the job header of the database
# pipeline's apply_trap_filter.sh (same account, QOS and sizing: 24 cpus,
# 100 GB, 4 h — under-provisioned memory gets the task OOM-killed with no
# output).
#
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run>                       # single-position run: whole run = segment 0
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run> <segments.csv>        # dwell run: one array task per segment
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run> <segments.csv> <out>  # output folder (default offline_output/filter)
#
# Trap settings default to the nabPy standard; override with environment
# variables: RISETIME=150 FLATTOP=20 FALLTIME=1250 WAVE=singles. Run from
# the repo root with the Python environment active (SLURM exports it to
# the job), or set CALNET_VENV to the environment to activate instead —
# the same convention as every other GT wrapper here.
#
# Outputs land in <out>/ with the cluster staging names
# (Run<run>_seg<seg>_<wave>_filter_output_rt..._ft..._fall....csv), so
# they also ingest unchanged later if the run should end up in the
# database after all. Logs: <out>/slurmout/.

set -euo pipefail

H5_DIR=${1:?usage: submit_trap_filter_gt.sh <h5 dir> <run> [segments.csv] [out dir]}
RUN=${2:?usage: submit_trap_filter_gt.sh <h5 dir> <run> [segments.csv] [out dir]}
SEGMENTS=${3:-}
OUT_DIR=${4:-offline_output/filter}
RISETIME=${RISETIME:-1250}
FLATTOP=${FLATTOP:-50}
FALLTIME=${FALLTIME:-1250}
WAVE=${WAVE:-singles}
# how many array tasks may run at once (per array, not per account)
MAX_CONCURRENT=${MAX_CONCURRENT:-20}

if [ ! -d "$H5_DIR" ]; then
    echo "ERROR: h5 directory not found: $H5_DIR"
    exit 1
fi
if [ ! -f scripts/offline/trap_filter.py ]; then
    echo "ERROR: run this from the CalibrationNet repo root"
    exit 1
fi
if [ -n "${CALNET_VENV:-}" ]; then
    source "${CALNET_VENV}/bin/activate"
fi
# fail here on the login node, not an hour into the job: the job needs ONE
# environment providing both nabPy and this package (README "Setup"). The
# waveforms module moved from calibrationnet.pipeline to
# calibrationnet.acquisition during the cleanup, so accept either.
if ! python - <<'EOF'
try:
    from calibrationnet.acquisition.waveforms import import_nabpy
except ImportError:
    from calibrationnet.pipeline.waveforms import import_nabpy
import_nabpy()
EOF
then
    echo "ERROR: the active python cannot run the trap filter. Activate the"
    echo "environment that has nabPy + calibrationnet, or set CALNET_VENV."
    exit 1
fi

mkdir -p "$OUT_DIR/slurmout"
SETTINGS="-rt $RISETIME -ft $FLATTOP -fall $FALLTIME --wave $WAVE"

if [ -z "$SEGMENTS" ]; then
    # whole run as segment 0 — one job
    JOB=$(sbatch --parsable <<EOF
#!/bin/bash
#SBATCH -A gts-ajezghani3
#SBATCH -J offline-trap-${RUN}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
#SBATCH --qos=embers
#SBATCH --requeue
#SBATCH -C graniterapids
#SBATCH -p cpu-gnr
#SBATCH --output=${OUT_DIR}/slurmout/trapfilter_${RUN}_%j.out
set -euo pipefail
cd "$PWD"
echo "run ${RUN}: whole run as segment 0"
python scripts/offline/trap_filter.py --h5-dir "$H5_DIR" --run "$RUN" $SETTINGS --out "$OUT_DIR"
EOF
)
    echo "submitted job $JOB for run $RUN (whole run as segment 0)"
    echo "when done, expect: ls $OUT_DIR/Run${RUN}_seg0_*.csv"
else
    if [ ! -f "$SEGMENTS" ]; then
        echo "ERROR: segments file not found: $SEGMENTS (scripts/offline/export_segments.py writes it)"
        exit 1
    fi
    # manifest: this run's segment indices, one per array task
    MANIFEST="$OUT_DIR/manifest_run${RUN}.txt"
    tail -n +2 "$SEGMENTS" | awk -F',' -v run="$RUN" '$1 == run {print $2}' > "$MANIFEST"
    N_TASKS=$(wc -l < "$MANIFEST" | tr -d ' ')
    if [ "$N_TASKS" -eq 0 ]; then
        echo "ERROR: no segments for run $RUN in $SEGMENTS"
        exit 1
    fi
    echo "$N_TASKS segment task(s) for run $RUN -> $MANIFEST"
    JOB=$(sbatch --parsable <<EOF
#!/bin/bash
#SBATCH -A gts-ajezghani3
#SBATCH -J offline-trap-${RUN}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
#SBATCH --qos=embers
#SBATCH --requeue
#SBATCH -C graniterapids
#SBATCH -p cpu-gnr
#SBATCH --array=1-${N_TASKS}%${MAX_CONCURRENT}
#SBATCH --output=${OUT_DIR}/slurmout/trapfilter_${RUN}_%A_%a.out
set -euo pipefail
cd "$PWD"
SEGMENT=\$(sed -n "\${SLURM_ARRAY_TASK_ID}p" "$MANIFEST")
echo "task \${SLURM_ARRAY_TASK_ID}: run ${RUN} segment \${SEGMENT}"
python scripts/offline/trap_filter.py --h5-dir "$H5_DIR" --run "$RUN" --segments "$SEGMENTS" --segment "\$SEGMENT" $SETTINGS --out "$OUT_DIR"
EOF
)
    echo "submitted array job $JOB for run $RUN ($N_TASKS tasks)"
    echo "when done, expect $N_TASKS files: ls $OUT_DIR/Run${RUN}_seg*_${WAVE}_*.csv | wc -l"
    echo "a failed task N is redone with: sbatch --array=N <the same job>; simplest is to re-run this script"
fi
echo "progress: squeue -u \$USER   |   logs: $OUT_DIR/slurmout/"
