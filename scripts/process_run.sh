#!/bin/bash
# The whole per-run pipeline as ONE hands-off SLURM job:
#
#   ./scripts/process_run.sh <run> <h5_dir> [extra process_run.py args]
#
# Run from the repo root on a login node — the script sbatch's itself
# and exits. Inside the job it runs
#   python scripts/process_run.py <run> --skip-ingest --h5-dir <h5_dir>
# (ingest stays a LOCAL step: it needs the slow-controls tunnel; do it
# before submitting — see docs/pipeline_usage.md).
#
# The trap-filter handoff is automatic: when process_run.py submits
# the trap array and exits ("trap filter array submitted — re-run
# ..."), this job parses the submitted job ids from the output and
# resubmits ITSELF with --dependency=afterany:<ids>, so the
# continuation fires when the array drains. A resubmit counter
# (CALNET_PR_TRIES, cap 3) stops the chain if trap tasks keep failing.
#
# Preemption-safe: every stage is idempotent (skip-frozen fits,
# replace-semantics extraction/calibration), so --requeue simply
# continues where the job stopped. Logs + figures land in
# fit_plots/run_<run>/ (git-ignored).
#
# The fit stage runs the run's segments SERIALLY in this one task —
# fine for a typical run inside the 8 h walltime. For a very large
# multi-position campaign use the parallel path instead:
# submit_fit_spectra.sh + calibrate.sh.
#
#SBATCH -A gts-ajezghani3
#SBATCH -J calnet-processrun
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8gb
#SBATCH -t 8:00:00
#SBATCH --qos=embers
#SBATCH --requeue

set -uo pipefail

RUN=${1:?usage: process_run.sh <run> <h5_dir> [process_run.py args...]}
H5DIR=${2:?usage: process_run.sh <run> <h5_dir> [process_run.py args...]}
shift 2
EXTRA_ARGS="$*"

LOG_DIR="fit_plots/run_${RUN}/slurmout"

# ---------------- submitter mode (login node) ----------------
if [ -z "${SLURM_JOB_ID:-}" ]; then
    mkdir -p "$LOG_DIR"
    JOB=$(sbatch --parsable -o "$LOG_DIR/process_run_%j.out" \
          --export=ALL,CALNET_PR_TRIES=0 \
          scripts/process_run.sh "$RUN" "$H5DIR" $EXTRA_ARGS)
    echo "submitted job ${JOB} — log: $LOG_DIR/process_run_${JOB}.out"
    echo "(it resubmits itself after the trap array if outputs are missing)"
    exit 0
fi

# ---------------- worker mode (inside the job) ----------------
cd "${SLURM_SUBMIT_DIR}"
if [ -n "${CALNET_VENV:-}" ]; then
    source "${CALNET_VENV}/bin/activate"
fi
TRIES=${CALNET_PR_TRIES:-0}
TMPLOG=$(mktemp)

python scripts/process_run.py "$RUN" --skip-ingest --h5-dir "$H5DIR" \
    $EXTRA_ARGS 2>&1 | tee "$TMPLOG"
CODE=${PIPESTATUS[0]}

if [ "$CODE" -eq 0 ]; then
    rm -f "$TMPLOG"
    exit 0
fi

if grep -q "trap filter array submitted" "$TMPLOG"; then
    if [ "$TRIES" -ge 3 ]; then
        echo "ERROR: trap outputs still missing after $TRIES chained"
        echo "attempts — trap tasks are failing; check their slurmout."
        rm -f "$TMPLOG"
        exit 1
    fi
    DEPS=$(awk '/^submitted (array|summary) job/ {print $4}' "$TMPLOG" \
           | paste -sd: -)
    rm -f "$TMPLOG"
    if [ -z "$DEPS" ]; then
        echo "ERROR: could not parse trap job ids from the output —"
        echo "re-run scripts/process_run.sh $RUN when the array drains."
        exit 1
    fi
    NEXT=$(sbatch --parsable -o "$LOG_DIR/process_run_%j.out" \
           --dependency="afterany:$DEPS" \
           --export=ALL,CALNET_PR_TRIES=$((TRIES + 1)) \
           scripts/process_run.sh "$RUN" "$H5DIR" $EXTRA_ARGS)
    echo "trap array running — continuation job ${NEXT} queued" \
         "(afterany:$DEPS, attempt $((TRIES + 1))/3)"
    exit 0
fi

rm -f "$TMPLOG"
echo "process_run.py failed (exit $CODE) — see the log above."
exit "$CODE"
