#!/bin/bash
# SLURM array task: apply the trap filter to a CHUNK of run segments and
# ingest them.
#
# Not run directly — scripts/submit_trap_filter.sh builds the manifest and
# submits the array. Each task processes $9 consecutive manifest lines
# (segments, ~30 min of data / ~10 min of compute each). Chunking exists
# because the QOS caps SUBMITTED jobs per user (QOSMaxSubmitJobPerUserLimit,
# ~50 on embers) and every array task counts against it at submission time —
# so a big batch must mean more segments per task, not more tasks.
#
#   $1 manifest file ("<run> <segment>" per line)
#   $2 line offset (for run lists longer than one array)
#   $3 h5 directory   $4 risetime   $5 flattop   $6 falltime   $7 wave type
#   $8 label   $9 segments per task (default 1)
#
#SBATCH -A gts-ajezghani3
#SBATCH -J calnet-trapfilter
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
#SBATCH --qos=embers
#SBATCH --requeue
# Granite Rapids nodes: measurably faster for this dask/numba work, and
# worth asking for whenever a task wants a lot of cpus. Drop these two
# lines if that partition is unavailable or backlogged.
#SBATCH -C graniterapids
#SBATCH -p cpu-gnr

set -euo pipefail

MANIFEST=$1
OFFSET=$2
H5_DIR=$3
RISETIME=$4
FLATTOP=$5
FALLTIME=$6
WAVE=$7
LABEL=$8
CHUNK=${9:-1}

cd "${SLURM_SUBMIT_DIR}"

# By default the job inherits the environment you submitted from (SLURM
# exports it), so `source <your env>/bin/activate` before submitting and
# nothing needs configuring here. Set CALNET_VENV to activate a specific
# environment instead.
if [ -n "${CALNET_VENV:-}" ]; then
    source "${CALNET_VENV}/bin/activate"
fi

# Fail immediately with a useful message rather than part-way through a
# segment: this needs ONE environment providing both nabPy and this package
# (see "Setup" in README.md).
if ! python -c "from calibrationnet.acquisition.waveforms import import_nabpy
import_nabpy()"; then
    echo "ERROR: the active python cannot run this job. It needs ONE"
    echo "environment providing both nabPy and calibrationnet — see 'Setup'"
    echo "in README.md. Activate one and resubmit, or set CALNET_VENV."
    exit 1
fi

# embers is preemptible; --requeue plus per-segment idempotence means a
# preempted or partly-failed task can simply be resubmitted — the next
# manifest leaves out whatever already made it in. A failing segment does
# not stop the rest of the chunk; the task reports it at the end instead.
START=$((OFFSET + SLURM_ARRAY_TASK_ID * CHUNK + 1))
FAILED=0
for LINE in $(seq "$START" $((START + CHUNK - 1))); do
    read -r RUN SEGMENT < <(sed -n "${LINE}p" "$MANIFEST")
    if [ -z "${RUN:-}" ]; then
        echo "manifest $MANIFEST ends before line $LINE — chunk done"
        break
    fi
    echo "task ${SLURM_ARRAY_TASK_ID} -> run ${RUN} segment ${SEGMENT}"
    python scripts/apply_trap_filter.py \
        -d "$H5_DIR" -r "$RUN" -s "$SEGMENT" \
        -rt "$RISETIME" -ft "$FLATTOP" -fall "$FALLTIME" \
        -w "$WAVE" --label "$LABEL" \
        || { echo "FAILED: run ${RUN} segment ${SEGMENT}"; FAILED=$((FAILED + 1)); }
done

if [ "$FAILED" -gt 0 ]; then
    echo "${FAILED} segment(s) in this chunk failed — re-run submit_trap_filter.sh to redo them"
    exit 1
fi
