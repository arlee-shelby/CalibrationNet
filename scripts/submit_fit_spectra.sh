#!/bin/bash
# Submit DATABASE spectrum fitting for a list of runs as one SLURM
# array: one manifest line per (run, segment) that has trap filter
# outputs at the requested label, chunked so the batch stays under the
# QOS submit cap — the same pattern as submit_trap_filter.sh.
#
#   ./scripts/submit_fit_spectra.sh run_list.txt fit_plots_9464/
#   ./scripts/submit_fit_spectra.sh fall2025_udet.txt fit_plots_fall2025/ \
#       --detector udet
#
# Everything after the first two arguments is passed straight to
# scripts/fit_spectra.py (e.g. --detector udet, --tf-label X,
# --isotope Bi-207). Accepted fits go to the spectrum_fits table
# (re-running REPLACES same-label fits, so resubmitting is safe);
# figures and the failure CSVs go to the plot directory.
#
# Run from the repo root on the cluster with the database reachable and
# the python environment active (or CALNET_VENV set).

set -euo pipefail

RUN_LIST=${1:?usage: submit_fit_spectra.sh <run_list.txt> <plot_dir> [fit_spectra.py args...]}
PLOT_DIR=${2:?usage: submit_fit_spectra.sh <run_list.txt> <plot_dir> [fit_spectra.py args...]}
shift 2
EXTRA_ARGS="$*"

# The trap filter label decides which (run, segment) pairs exist; keep
# the manifest query consistent with what fit_spectra.py will look for.
TF_LABEL="nabpy-standard"
prev=""
for a in $EXTRA_ARGS; do
    if [ "$prev" = "--tf-label" ]; then TF_LABEL="$a"; fi
    prev="$a"
done

MAX_CONCURRENT=${MAX_CONCURRENT:-40}
MAX_SUBMIT=${MAX_SUBMIT:-40}

mkdir -p "$PLOT_DIR/slurmout"
MANIFEST="$PLOT_DIR/manifest.txt"

# Manifest: every (run, segment) of the run list that holds trap filter
# outputs at this label. Fits are replaced on re-run, so there is no
# "pending" notion here — resubmitting redoes the whole list.
python - "$RUN_LIST" "$TF_LABEL" > "$MANIFEST" <<'EOF'
import sys
from sqlalchemy import select, distinct
from calibrationnet.db import get_session
from calibrationnet.models import RunPixel, TrapFilterOutput

runs = [int(line.split()[0]) for line in open(sys.argv[1])
        if line.strip() and not line.startswith("#")]
with get_session() as session:
    rows = session.execute(
        select(distinct(RunPixel.run_number), RunPixel.segment_index)
        .join(TrapFilterOutput,
              TrapFilterOutput.run_pixel_id == RunPixel.id)
        .where(RunPixel.run_number.in_(runs),
               TrapFilterOutput.label == sys.argv[2])
        .order_by(RunPixel.run_number, RunPixel.segment_index)).all()
for run, segment in rows:
    print(run, segment)
EOF

TOTAL=$(wc -l < "$MANIFEST" | tr -d ' ')
if [ "$TOTAL" -eq 0 ]; then
    echo "nothing to fit: no '$TF_LABEL' trap filter outputs for the"
    echo "runs in $RUN_LIST."
    exit 0
fi
echo "$TOTAL run segment(s) to fit -> $MANIFEST"

# A segment is a few minutes of single-core fitting; 30 min per segment
# is generous headroom, capped at the 7:59 embers maximum.
SEGMENTS_PER_TASK=${SEGMENTS_PER_TASK:-$(( (TOTAL + MAX_SUBMIT - 1) / MAX_SUBMIT ))}
N_TASKS=$(( (TOTAL + SEGMENTS_PER_TASK - 1) / SEGMENTS_PER_TASK ))
MINUTES=$(( 30 * SEGMENTS_PER_TASK ))
if [ "$MINUTES" -gt 479 ]; then
    MINUTES=479
    echo "note: ${SEGMENTS_PER_TASK} segments/task may not fit the 7:59"
    echo "walltime cap; anything cut off is redone by resubmitting."
fi
echo "-> ${N_TASKS} array task(s), ${SEGMENTS_PER_TASK} segment(s) each, ${MINUTES} min walltime"

JOB=$(sbatch --parsable \
    --array=0-$((N_TASKS - 1))%${MAX_CONCURRENT} \
    --time="$MINUTES" \
    --output="${PLOT_DIR}/slurmout/fitspectra_%A_%a.out" \
    scripts/fit_spectra.sh \
    "$MANIFEST" "$PLOT_DIR" "$EXTRA_ARGS" "$SEGMENTS_PER_TASK")
echo "submitted array job ${JOB}"
echo
echo "progress:   squeue -u \$USER"
echo "failures:   ${PLOT_DIR}/fit_failures_summary.csv (flock-safe, shared)"
echo "a failed task N is redone with: sbatch --array=N scripts/fit_spectra.sh \\"
echo "    '$MANIFEST' '$PLOT_DIR' '$EXTRA_ARGS' $SEGMENTS_PER_TASK"
