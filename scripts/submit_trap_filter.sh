#!/bin/bash
# Submit trap filter jobs for a list of runs: one SLURM array whose tasks
# each process a chunk of pending segments.
#
#   ./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/
#   ./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/ 1250 50 1250 singles
#
# Asks the database which segments still need this filter setting, writes a
# manifest, and submits ONE array over it. The QOS caps SUBMITTED jobs per
# user (QOSMaxSubmitJobPerUserLimit, ~50 on embers) and every array task
# counts against that cap at submission time — a 62-task array is rejected
# outright — so when the batch is bigger than MAX_SUBMIT tasks, each task
# processes several consecutive segments (chunking) instead. There is no
# submit-and-poll loop to babysit and nothing to keep alive in tmux —
# detaching or logging out is fine.
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
# the account limit separately, so leave headroom or the surplus just
# sits pending.
MAX_CONCURRENT=${MAX_CONCURRENT:-40}
# Most tasks the whole submission may hold in the queue at once — must stay
# under the QOS submit cap (~50 on embers) with room for the summary job
# and anything else you have queued. Segments are packed into this many
# tasks: SEGMENTS_PER_TASK overrides the computed chunk size if set.
MAX_SUBMIT=${MAX_SUBMIT:-40}

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

# Pack the batch into at most MAX_SUBMIT array tasks: each task works
# SEGMENTS_PER_TASK consecutive manifest lines. Walltime scales with the
# chunk (a segment is ~10 min of compute; 4 h base keeps the old
# single-segment margin) and is capped at the 7:59 embers maximum — a task
# that hits the cap is finished by simply re-running this script.
SEGMENTS_PER_TASK=${SEGMENTS_PER_TASK:-$(( (TOTAL + MAX_SUBMIT - 1) / MAX_SUBMIT ))}
N_TASKS=$(( (TOTAL + SEGMENTS_PER_TASK - 1) / SEGMENTS_PER_TASK ))
MINUTES=$(( 240 + (SEGMENTS_PER_TASK - 1) * 40 ))
if [ "$MINUTES" -gt 479 ]; then
    MINUTES=479
    echo "note: ${SEGMENTS_PER_TASK} segments/task may not fit the 7:59"
    echo "walltime cap; anything cut off is picked up by re-running this script."
fi
echo "-> ${N_TASKS} array task(s), ${SEGMENTS_PER_TASK} segment(s) each, ${MINUTES} min walltime"

JOB=$(sbatch --parsable \
    --array=0-$((N_TASKS - 1))%${MAX_CONCURRENT} \
    --time="$MINUTES" \
    --output="${OUT_DIR}/slurmout/trapfilter_%A_%a.out" \
    scripts/apply_trap_filter.sh \
    "$MANIFEST" 0 "$H5_DIR" \
    "$RISETIME" "$FLATTOP" "$FALLTIME" "$WAVE" "$LABEL" \
    "$SEGMENTS_PER_TASK")
echo "submitted array job ${JOB}"

# One short job that runs after every array task finishes (whatever their
# exit status) and writes a single per-run progress report — the
# unambiguous "is the whole batch done?" answer, instead of reading
# scattered per-task logs.
DEPENDENCY=$JOB
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
