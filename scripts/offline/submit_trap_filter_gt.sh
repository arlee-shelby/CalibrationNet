#!/bin/bash
# Submit the OFFLINE trap filter for one run as SLURM jobs on GT — files
# in, files out, the database is never touched. The GT counterpart of
# submit_trap_filter_nersc.sh, with the job header of the database
# pipeline's apply_trap_filter.sh (same account, QOS and sizing: 24 cpus,
# 100 GB, 4 h — under-provisioned memory gets a task OOM-killed with no
# output).
#
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run>                       # single-position run: whole run = segment 0
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run> <segments.csv>        # dwell run: one array task per segment
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run> <segments.csv> <out>  # output folder (default offline_output/filter)
#   ./scripts/offline/submit_trap_filter_gt.sh <h5 dir> <run> "" <out>              # whole run, custom output folder
#
# A whole run is split into PARTS of SUBRUNS_PER_TASK subrun files
# (default 8, ~10 min of compute each), one array task per part, so a
# long run finishes in about the time of one part instead of hitting the
# 4 h walltime. The QOS caps SUBMITTED jobs per user (~50 on embers) and
# every array task counts at submission time, so at most MAX_SUBMIT parts
# (default 40) go in per ROUND; a job queued behind the array either
# merges the parts into the ONE normal CSV
# (Run<run>_seg0_<wave>_filter_output_rt..._ft..._fall....csv) and
# removes them, or — while parts are still missing — submits the next
# round itself, up to MAX_ROUNDS (default 12). Nothing to babysit: a
# 1,700-subrun run is ~215 parts = 6 rounds, hands-off. The filter is
# applied waveform by waveform, so the merged file has exactly the rows a
# single pass would have, in a different order, which nothing downstream
# depends on. Re-running the script by hand redoes ONLY the parts whose
# file is missing (a part that keeps failing stops the chain at
# MAX_ROUNDS with a message). SUBRUNS_PER_TASK=0 submits the old single
# job. SUBRUN_FIRST / SUBRUN_LAST restrict the whole-run mode to a range
# of subrun files when a slice of the run is enough for a look.
#
# Trap settings default to the nabPy standard; override with environment
# variables: RISETIME=150 FLATTOP=20 FALLTIME=1250 WAVE=pulsers. Run from
# the repo root with the Python environment active (SLURM exports it to
# the jobs, and the continuation inherits it), or set CALNET_VENV to the
# environment to activate instead — the same convention as every other GT
# wrapper here.
#
# Outputs land in <out>/ with the cluster staging names, so they also
# ingest unchanged later if the run should end up in the database after
# all. Logs: <out>/slurmout/.

set -euo pipefail

H5_DIR=${1:?usage: submit_trap_filter_gt.sh <h5 dir> <run> [segments.csv] [out dir]}
RUN=${2:?usage: submit_trap_filter_gt.sh <h5 dir> <run> [segments.csv] [out dir]}
SEGMENTS=${3:-}
OUT_DIR=${4:-offline_output/filter}
RISETIME=${RISETIME:-1250}
FLATTOP=${FLATTOP:-50}
FALLTIME=${FALLTIME:-1250}
WAVE=${WAVE:-singles}
SUBRUNS_PER_TASK=${SUBRUNS_PER_TASK:-8}
SUBRUN_FIRST=${SUBRUN_FIRST:-}
SUBRUN_LAST=${SUBRUN_LAST:-}
# submission limits: parts per round (QOS submit cap, ~50 incl. your
# other jobs), tasks running at once (per array), rounds before giving up
MAX_SUBMIT=${MAX_SUBMIT:-40}
MAX_CONCURRENT=${MAX_CONCURRENT:-40}
MAX_ROUNDS=${MAX_ROUNDS:-12}
ROUND=${CALNET_OFFLINE_ROUND:-1}

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
# the job header shared by every submission below
HEADER="#!/bin/bash
#SBATCH -A gts-ajezghani3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --qos=embers
#SBATCH --requeue"

if [ -n "$SEGMENTS" ]; then
    # ------------------------------------------------ dwell run: one task per segment
    if [ ! -f "$SEGMENTS" ]; then
        echo "ERROR: segments file not found: $SEGMENTS (scripts/offline/export_segments.py writes it)"
        exit 1
    fi
    MANIFEST="$OUT_DIR/manifest_run${RUN}.txt"
    tail -n +2 "$SEGMENTS" | awk -F',' -v run="$RUN" '$1 == run {print $2}' > "$MANIFEST"
    N_TASKS=$(wc -l < "$MANIFEST" | tr -d ' ')
    if [ "$N_TASKS" -eq 0 ]; then
        echo "ERROR: no segments for run $RUN in $SEGMENTS"
        exit 1
    fi
    echo "$N_TASKS segment task(s) for run $RUN -> $MANIFEST"
    JOB=$(sbatch --parsable <<EOF
$HEADER
#SBATCH -J offline-trap-${RUN}
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
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
    echo "a failed task is redone by re-running this script (or sbatch --array=N on the same job)"
    echo "progress: squeue -u \$USER   |   logs: $OUT_DIR/slurmout/"
    exit 0
fi

# ---------------------------------------------------- whole run = segment 0
FINAL_NAME="Run${RUN}_seg0_${WAVE}_filter_output_rt${RISETIME}_ft${FLATTOP}_fall${FALLTIME}.csv"
FINAL="$OUT_DIR/$FINAL_NAME"

if [ "$SUBRUNS_PER_TASK" -eq 0 ]; then
    # one job for the whole run (short runs only: the 4 h walltime is the limit)
    JOB=$(sbatch --parsable <<EOF
$HEADER
#SBATCH -J offline-trap-${RUN}
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
#SBATCH -C graniterapids
#SBATCH -p cpu-gnr
#SBATCH --output=${OUT_DIR}/slurmout/trapfilter_${RUN}_%j.out
set -euo pipefail
cd "$PWD"
echo "run ${RUN}: whole run as segment 0"
python scripts/offline/trap_filter.py --h5-dir "$H5_DIR" --run "$RUN" $SETTINGS --out "$OUT_DIR"
EOF
)
    echo "submitted job $JOB for run $RUN (whole run as segment 0, one job)"
    echo "when done, expect: $FINAL"
    echo "progress: squeue -u \$USER   |   logs: $OUT_DIR/slurmout/"
    exit 0
fi

PARTS_DIR="$OUT_DIR/parts/run${RUN}_${WAVE}"
mkdir -p "$PARTS_DIR"
MANIFEST="$PARTS_DIR/parts.txt"     # lines: "<part> <first subrun> <last subrun>"

if [ -f "$MANIFEST" ]; then
    # the split is fixed on the first round: later rounds (and manual
    # re-runs) must see the same parts, whatever appeared in the h5 dir since
    echo "reusing the parts manifest $MANIFEST"
else
    # the run's subrun files, in numeric order, optionally a range of them
    SUBRUNS=()
    while IFS= read -r index; do
        if [ -n "$SUBRUN_FIRST" ] && [ "$index" -lt "$SUBRUN_FIRST" ]; then continue; fi
        if [ -n "$SUBRUN_LAST" ] && [ "$index" -gt "$SUBRUN_LAST" ]; then continue; fi
        SUBRUNS+=("$index")
    done < <(ls "$H5_DIR" | grep -E "^Run${RUN}_[0-9]+\.h5$" | sed -E 's/^.*_([0-9]+)\.h5$/\1/' | sort -n)
    if [ "${#SUBRUNS[@]}" -eq 0 ]; then
        echo "ERROR: no Run${RUN}_<subrun>.h5 files in $H5_DIR${SUBRUN_FIRST:+ within subruns $SUBRUN_FIRST..${SUBRUN_LAST:-end}}"
        exit 1
    fi
    N=0
    for ((k = 0; k < ${#SUBRUNS[@]}; k += SUBRUNS_PER_TASK)); do
        last=$((k + SUBRUNS_PER_TASK - 1))
        if [ "$last" -ge "${#SUBRUNS[@]}" ]; then last=$((${#SUBRUNS[@]} - 1)); fi
        N=$((N + 1))
        echo "$N ${SUBRUNS[k]} ${SUBRUNS[last]}" >> "$MANIFEST"
    done
    LAST_SUBRUN=${SUBRUNS[$((${#SUBRUNS[@]} - 1))]}
    echo "run $RUN: ${#SUBRUNS[@]} subrun files (${SUBRUNS[0]}..${LAST_SUBRUN}) -> $N part(s) of up to $SUBRUNS_PER_TASK; manifest $MANIFEST"
fi
N_PARTS=$(wc -l < "$MANIFEST" | tr -d ' ')

# parts still without an output file: the first MAX_SUBMIT of them go now
TODO=""
N_TODO=0
N_NOW=0
while read -r PART _LO _HI; do
    if [ ! -f "$PARTS_DIR/part_${PART}/$FINAL_NAME" ]; then
        N_TODO=$((N_TODO + 1))
        if [ "$N_NOW" -lt "$MAX_SUBMIT" ]; then
            TODO="${TODO:+$TODO,}$PART"
            N_NOW=$((N_NOW + 1))
        fi
    fi
done < "$MANIFEST"
echo "round $ROUND of at most $MAX_ROUNDS: $N_PARTS part(s), $N_TODO without output, $N_NOW submitted now"

DEPENDENCY=""
if [ "$N_NOW" -gt 0 ]; then
    JOB=$(sbatch --parsable <<EOF
$HEADER
#SBATCH -J offline-trap-${RUN}
#SBATCH --cpus-per-task=24
#SBATCH --mem=100gb
#SBATCH -t 4:00:00
#SBATCH -C graniterapids
#SBATCH -p cpu-gnr
#SBATCH --array=${TODO}%${MAX_CONCURRENT}
#SBATCH --output=${OUT_DIR}/slurmout/trapfilter_${RUN}_%A_%a.out
set -euo pipefail
cd "$PWD"
ENTRY=\$(sed -n "\${SLURM_ARRAY_TASK_ID}p" "$MANIFEST")
read -r PART LO HI <<< "\$ENTRY"
echo "task \${SLURM_ARRAY_TASK_ID}: run ${RUN} part \${PART}, subruns \${LO}..\${HI}"
mkdir -p "$PARTS_DIR/part_\${PART}"
python scripts/offline/trap_filter.py --h5-dir "$H5_DIR" --run "$RUN" --subruns "\$LO" "\$HI" $SETTINGS --out "$PARTS_DIR/part_\${PART}"
EOF
)
    echo "submitted array job $JOB: parts $TODO"
    DEPENDENCY="--dependency=afterany:$JOB"
fi

# behind the array: merge when every part has its file, otherwise submit
# the next round (this same script, one round higher) — or stop at the cap
NEXT=$(sbatch --parsable $DEPENDENCY <<EOF
$HEADER
#SBATCH -J offline-merge-${RUN}
#SBATCH --cpus-per-task=1
#SBATCH --mem=4gb
#SBATCH -t 1:00:00
#SBATCH --output=${OUT_DIR}/slurmout/merge_${RUN}_round${ROUND}_%j.out
set -euo pipefail
cd "$PWD"
MISSING=0
for PART in \$(seq 1 $N_PARTS); do
    if [ ! -f "$PARTS_DIR/part_\${PART}/$FINAL_NAME" ]; then
        MISSING=\$((MISSING + 1))
    fi
done
if [ "\$MISSING" -ne 0 ]; then
    if [ "$ROUND" -ge "$MAX_ROUNDS" ]; then
        echo "round $ROUND: \$MISSING part(s) still without output after $MAX_ROUNDS rounds — their tasks are failing; see their trapfilter logs, then re-run scripts/offline/submit_trap_filter_gt.sh with the same arguments"
        exit 1
    fi
    echo "round $ROUND done: \$MISSING part(s) still without output — submitting round $((ROUND + 1))"
    CALNET_OFFLINE_ROUND=$((ROUND + 1)) ./scripts/offline/submit_trap_filter_gt.sh "$H5_DIR" "$RUN" "" "$OUT_DIR"
    exit 0
fi
head -n 1 "$PARTS_DIR/part_1/$FINAL_NAME" > "$FINAL.tmp"
for PART in \$(seq 1 $N_PARTS); do
    tail -n +2 "$PARTS_DIR/part_\${PART}/$FINAL_NAME" >> "$FINAL.tmp"
done
mv "$FINAL.tmp" "$FINAL"
rm -r "$PARTS_DIR"
echo "merged $N_PARTS part(s) -> $FINAL (\$(( \$(wc -l < "$FINAL") - 1 )) waveform rows)"
EOF
)
if [ "$N_TODO" -gt "$N_NOW" ]; then
    echo "continuation job $NEXT queued behind the array: it submits the next round ($((N_TODO - N_NOW)) part(s) left) by itself"
else
    echo "merge job $NEXT queued${DEPENDENCY:+ behind the array}"
fi
echo "when done, expect: $FINAL"
echo "progress: squeue -u \$USER   |   logs: $OUT_DIR/slurmout/"
