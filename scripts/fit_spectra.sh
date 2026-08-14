#!/bin/bash
# SLURM array task: run the DATABASE spectrum fitting over a CHUNK of
# run segments (scripts/fit_spectra.py — fits stored trap filter
# outputs, saves accepted fits to spectrum_fits).
#
# Not run directly — scripts/submit_fit_spectra.sh builds the manifest
# and submits the array. Each task processes $4 consecutive manifest
# lines. Chunking exists for the same reason as the trap filter batch:
# the QOS caps SUBMITTED jobs per user (~50 on embers) and every array
# task counts against it at submission time.
#
#   $1 manifest file ("<run> <segment>" per line)
#   $2 plot/output directory (figures + failure CSVs)
#   $3 extra fit_spectra.py arguments as ONE string, e.g.
#      "--detector udet --tf-label nabpy-standard" (may be empty)
#   $4 segments per task (default 1)
#
# Fitting is a SINGLE-CORE lmfit job whose data comes from the
# database, not HDF5 — 1 cpu / 8 GB is plenty (the offline NERSC twin
# uses the same sizing).
#
#SBATCH -A gts-ajezghani3
#SBATCH -J calnet-fitspectra
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8gb
#SBATCH -t 4:00:00
#SBATCH --qos=embers
#SBATCH --requeue

set -euo pipefail

MANIFEST=$1
PLOT_DIR=$2
EXTRA_ARGS=${3:-}
CHUNK=${4:-1}

cd "${SLURM_SUBMIT_DIR}"

# Inherits the submit-time environment (SLURM exports it); set
# CALNET_VENV to activate a specific one instead — same convention as
# apply_trap_filter.sh.
if [ -n "${CALNET_VENV:-}" ]; then
    source "${CALNET_VENV}/bin/activate"
fi
if ! python -c "import calibrationnet.fitting"; then
    echo "ERROR: the active python cannot import calibrationnet."
    echo "Activate the env and resubmit, or set CALNET_VENV."
    exit 1
fi

# Re-running a segment REPLACES its stored fits (same output + label),
# so a preempted or partly-failed task is finished by resubmitting it —
# nothing is double-stored. A failing segment does not stop the rest of
# the chunk; the task reports it at the end instead.
START=$((SLURM_ARRAY_TASK_ID * CHUNK + 1))
FAILED=0
for LINE in $(seq "$START" $((START + CHUNK - 1))); do
    read -r RUN SEGMENT < <(sed -n "${LINE}p" "$MANIFEST")
    if [ -z "${RUN:-}" ]; then
        echo "manifest $MANIFEST ends before line $LINE — chunk done"
        break
    fi
    echo "task ${SLURM_ARRAY_TASK_ID} -> run ${RUN} segment ${SEGMENT}"
    # shellcheck disable=SC2086  # EXTRA_ARGS is deliberately word-split
    if ! python scripts/fit_spectra.py --run "$RUN" --segment "$SEGMENT" \
            --plot "$PLOT_DIR" --failures-detail $EXTRA_ARGS; then
        echo "FAILED: run ${RUN} segment ${SEGMENT}"
        FAILED=$((FAILED + 1))
    fi
done
if [ "$FAILED" -gt 0 ]; then
    echo "${FAILED} segment(s) in this chunk failed — resubmit this task"
    exit 1
fi
