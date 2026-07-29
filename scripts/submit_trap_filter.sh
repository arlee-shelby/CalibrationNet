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

# Run this from the repo root.
RUN_LIST=${1:?usage: submit_trap_filter.sh <run_list.txt> <h5_dir> [rt ft fall wave]}
H5_DIR=${2:?usage: submit_trap_filter.sh <run_list.txt> <h5_dir> [rt ft fall wave]}
RISETIME=${3:-1250}
FLATTOP=${4:-50}
FALLTIME=${5:-1250}
WAVE=${6:-singles}
LABEL=${7:-nabpy-standard}

# How many tasks OF THIS ARRAY may run at once (SLURM's %N throttle). It is
# per-array, not per-account: any other jobs you have running count against
# the 50-job account limit separately, so leave headroom or the surplus just
# sits pending. MAX_ARRAY caps tasks per array submission (MaxArraySize).
MAX_CONCURRENT=${MAX_CONCURRENT:-45}
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
JOB_IDS=()
while [ "$OFFSET" -lt "$TOTAL" ]; do
    REMAINING=$((TOTAL - OFFSET))
    COUNT=$(( REMAINING < MAX_ARRAY ? REMAINING : MAX_ARRAY ))
    JOB=$(sbatch --parsable \
        --array=0-$((COUNT - 1))%${MAX_CONCURRENT} \
        --output="${OUT_DIR}/slurmout/trapfilter_%A_%a.out" \
        scripts/apply_trap_filter.sh \
        "$MANIFEST" "$OFFSET" "$H5_DIR" \
        "$RISETIME" "$FLATTOP" "$FALLTIME" "$WAVE" "$LABEL")
    JOB_IDS+=("$JOB")
    echo "submitted array job ${JOB}: manifest lines $((OFFSET + 1))-$((OFFSET + COUNT))"
    OFFSET=$((OFFSET + COUNT))
done

# One short job that runs after every array task finishes (whatever their
# exit status) and writes a single per-run progress report — the
# unambiguous "is the whole batch done?" answer, instead of reading
# scattered per-task logs.
DEPENDENCY=$(IFS=:; echo "${JOB_IDS[*]}")
SUMMARY=$(sbatch --parsable \
    --dependency=afterany:"${DEPENDENCY}" --kill-on-invalid-dep=yes \
    -A "${SLURM_ACCOUNT:-gts-ajezghani3}" -J calnet-trapfilter-summary \
    -N1 --cpus-per-task=1 --mem=4gb -t 10:00 \
    --output="${OUT_DIR}/slurmout/trapfilter_summary_%j.out" \
    --wrap="cd '$PWD' && python scripts/pending_segments.py \
        --runs-file '$RUN_LIST' -rt $RISETIME -ft $FLATTOP \
        -fall $FALLTIME --label '$LABEL' --summary")
echo "submitted summary job ${SUMMARY} (runs after the array finishes)"

echo
echo "progress:  squeue -u \$USER"
echo "when done: cat ${OUT_DIR}/slurmout/trapfilter_summary_${SUMMARY}.out"
echo "or check any time:  python scripts/pending_segments.py --runs-file $RUN_LIST --summary"
echo "re-run this script to pick up anything that failed or was preempted."
