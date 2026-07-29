#!/bin/bash
# SLURM array task: apply the trap filter to ONE run segment and ingest it.
#
# Not run directly — scripts/submit_trap_filter.sh builds the manifest and
# submits the array. Each task reads its own line of the manifest, so the
# work unit is a segment (~30 min of data, ~10 min of compute) and no task
# comes near the wall-clock limit however long the run is.
#
#   $1 manifest file ("<run> <segment>" per line)
#   $2 line offset (for run lists longer than one array)
#   $3 h5 directory   $4 risetime   $5 flattop   $6 falltime   $7 wave type
#   $8 label
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

set -euo pipefail

MANIFEST=$1
OFFSET=$2
H5_DIR=$3
RISETIME=$4
FLATTOP=$5
FALLTIME=$6
WAVE=$7
LABEL=$8

# embers is preemptible; --requeue plus per-segment idempotence means a
# preempted task simply redoes its own segment.
LINE=$((OFFSET + SLURM_ARRAY_TASK_ID + 1))
read -r RUN SEGMENT < <(sed -n "${LINE}p" "$MANIFEST")
if [ -z "${RUN:-}" ]; then
    echo "manifest $MANIFEST has no line $LINE — nothing to do"
    exit 0
fi

cd "${SLURM_SUBMIT_DIR}"
source "${CALNET_VENV:-$HOME/Manitoba}/bin/activate"

echo "task ${SLURM_ARRAY_TASK_ID} -> run ${RUN} segment ${SEGMENT}"
python scripts/apply_trap_filter.py \
    -d "$H5_DIR" -r "$RUN" -s "$SEGMENT" \
    -rt "$RISETIME" -ft "$FLATTOP" -fall "$FALLTIME" \
    -w "$WAVE" --label "$LABEL"
