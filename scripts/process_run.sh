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
# The array handoffs are automatic. Both the trap-filter stage and
# the fit stage (--fits-via-array, passed by this wrapper) submit a
# SLURM array and exit with a distinctive message; this job parses
# the submitted job ids from the output and resubmits ITSELF with
# --dependency=afterany:<ids> (the fit continuation adds --skip-fits).
# Chain: job -> trap array -> job -> fit array -> job (extract +
# calibrate, seconds per segment) -> done. Wall-clock for the fit
# stage is therefore ~the slowest single segment, whatever the run
# size. A resubmit counter (CALNET_PR_TRIES, cap 4) stops the chain
# if a stage's tasks keep failing.
#
# Preemption-safe: every stage is idempotent (skip-frozen fits,
# replace-semantics extraction/calibration), so --requeue simply
# continues where the job stopped. Logs + figures land in
# fit_plots/run_<run>/ (git-ignored).
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
    --fits-via-array $EXTRA_ARGS 2>&1 | tee "$TMPLOG"
CODE=${PIPESTATUS[0]}

if [ "$CODE" -eq 0 ]; then
    rm -f "$TMPLOG"
    exit 0
fi

# Shared handoff: parse the job ids the stage printed and queue a
# continuation of this job behind them. $1 = the stage's exit message
# marker, $2 = extra args for the continuation (e.g. --skip-fits).
resubmit_after() {
    if [ "$TRIES" -ge 4 ]; then
        echo "ERROR: still waiting on '$1' after $TRIES chained"
        echo "attempts — its tasks are failing; check their slurmout."
        rm -f "$TMPLOG"
        exit 1
    fi
    DEPS=$(awk '/^submitted (array|summary) job/ {print $4}' "$TMPLOG" \
           | paste -sd: -)
    rm -f "$TMPLOG"
    if [ -z "$DEPS" ]; then
        echo "ERROR: could not parse job ids from the output —"
        echo "re-run scripts/process_run.sh $RUN when the array drains."
        exit 1
    fi
    NEXT=$(sbatch --parsable -o "$LOG_DIR/process_run_%j.out" \
           --dependency="afterany:$DEPS" \
           --export=ALL,CALNET_PR_TRIES=$((TRIES + 1)) \
           scripts/process_run.sh "$RUN" "$H5DIR" $EXTRA_ARGS $2)
    echo "$1 running — continuation job ${NEXT} queued" \
         "(afterany:$DEPS, attempt $((TRIES + 1))/4)"
    exit 0
}

if grep -q "trap filter array submitted" "$TMPLOG"; then
    resubmit_after "trap filter array" ""
fi
if grep -q "fit array submitted" "$TMPLOG"; then
    resubmit_after "fit array" "--skip-fits"
fi

rm -f "$TMPLOG"
echo "process_run.py failed (exit $CODE) — see the log above."
exit "$CODE"
