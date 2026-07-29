#!/bin/bash
# Submit trap filter jobs for a list of runs, one array task per segment.
#
#   ./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/
#   ./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/ 1250 50 1250 singles
#
# Asks the database which segments still need this filter setting, writes a
# manifest, and submits ONE array over it. SLURM's %N throttle enforces the
# 50-job account limit, so there is no submit-and-poll loop to babysit and
# nothing to keep alive in tmux — detaching or logging out is fine.
#
# Re-running is safe and is the way to finish an interrupted batch: segments
# already ingested with these settings are left out of the new manifest.

set -euo pipefail

RUN_LIST=${1:?usage: submit_trap_filter.sh <run_list.txt> <h5_dir> [rt ft fall wave]}
H5_DIR=${2:?usage: submit_trap_filter.sh <run_list.txt> <h5_dir> [rt ft fall wave]}
RISETIME=${3:-1250}
FLATTOP=${4:-50}
FALLTIME=${5:-1250}
WAVE=${6:-singles}
LABEL=${7:-nabpy-standard}

# Concurrency cap (account limit is 50) and max tasks per array submission.
MAX_CONCURRENT=${MAX_CONCURRENT:-50}
MAX_ARRAY=${MAX_ARRAY:-1000}

OUT_DIR=data/TrapFilterData
MANIFEST="${OUT_DIR}/manifest_rt${RISETIME}_ft${FLATTOP}_fall${FALLTIME}_${WAVE}.txt"
mkdir -p "${OUT_DIR}/slurmout"

python scripts/pending_segments.py --runs-file "$RUN_LIST" \
    -rt "$RISETIME" -ft "$FLATTOP" -fall "$FALLTIME" --label "$LABEL" \
    > "$MANIFEST"

TOTAL=$(wc -l < "$MANIFEST" | tr -d ' ')
if [ "$TOTAL" -eq 0 ]; then
    echo "nothing to do: every segment in $RUN_LIST already has "
    echo "rt=$RISETIME ft=$FLATTOP fall=$FALLTIME ($LABEL) ingested."
    exit 0
fi
echo "$TOTAL segment(s) to process -> $MANIFEST"

# One array per MAX_ARRAY manifest lines; each task adds its offset.
OFFSET=0
while [ "$OFFSET" -lt "$TOTAL" ]; do
    REMAINING=$((TOTAL - OFFSET))
    COUNT=$(( REMAINING < MAX_ARRAY ? REMAINING : MAX_ARRAY ))
    JOB=$(sbatch --parsable \
        --array=0-$((COUNT - 1))%${MAX_CONCURRENT} \
        --output="${OUT_DIR}/slurmout/trapfilter_%A_%a.out" \
        scripts/apply_trap_filter.sh \
        "$MANIFEST" "$OFFSET" "$H5_DIR" \
        "$RISETIME" "$FLATTOP" "$FALLTIME" "$WAVE" "$LABEL")
    echo "submitted array job ${JOB}: manifest lines $((OFFSET + 1))-$((OFFSET + COUNT))"
    OFFSET=$((OFFSET + COUNT))
done

echo
echo "watch with:  squeue -u \$USER   |   tail -f ${OUT_DIR}/slurmout/trapfilter_*.out"
echo "re-run this script later to pick up anything that failed or was preempted."
