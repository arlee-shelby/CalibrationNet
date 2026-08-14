#!/bin/bash
# Stage 2 + stage 4 over EVERYTHING, as one SLURM array: extract
# adc_peaks from every stored spectrum fit, then fit and store a
# calibration for every pixel with matched peaks. The work list —
# every (run, segment, trap label) holding fits — is discovered from
# the database, so there is no run list, and re-running redoes
# everything idempotently (extraction and calibration both REPLACE
# their own previous rows).
#
#   ./scripts/calibrate.sh [plot_dir]        # builds manifest, submits
#
# The script submits ITSELF as the array: each task processes CHUNK
# consecutive manifest lines, running extract-then-calibrate per
# segment (calibrations only ever use peaks from their own segment's
# outputs, so tasks never depend on each other). Figures — one QA
# plot per calibrated pixel — land in the plot dir (default
# calibration_plots), per-task logs in its slurmout/.
#
# Sizing: SQL + weighted least squares, no spectrum fitting — the
# whole ~340-segment batch is a few minutes at 40-way parallelism.
#
#   check afterwards:  grep -h "FAILED" <plot_dir>/slurmout/*.out
#
#SBATCH -A gts-ajezghani3
#SBATCH -J calnet-calibrate
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8gb
#SBATCH -t 1:00:00
#SBATCH --qos=embers
#SBATCH --requeue

set -uo pipefail

if [ -n "${CALNET_VENV:-}" ]; then
    source "${CALNET_VENV}/bin/activate"
fi

# ---------------- worker mode (inside the array) ----------------
if [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then
    cd "${SLURM_SUBMIT_DIR}"
    MANIFEST="$PLOT_DIR/manifest.txt"
    START=$((SLURM_ARRAY_TASK_ID * CHUNK + 1))
    for LINE in $(seq "$START" $((START + CHUNK - 1))); do
        read -r RUN SEG LABEL < <(sed -n "${LINE}p" "$MANIFEST")
        if [ -z "${RUN:-}" ]; then
            echo "manifest ends before line $LINE — chunk done"
            break
        fi
        echo "== run $RUN seg $SEG ($LABEL) =="
        if ! python scripts/extract_adc_peaks.py --run "$RUN" \
                --segment "$SEG" --tf-label "$LABEL"; then
            echo "FAILED extract: $RUN/$SEG/$LABEL"
            continue      # no peaks -> nothing to calibrate
        fi
        if ! python scripts/calibrate.py --run "$RUN" --segment "$SEG" \
                --tf-label "$LABEL" --plot "$PLOT_DIR"; then
            echo "FAILED calibrate: $RUN/$SEG/$LABEL"
        fi
    done
    exit 0
fi

# ---------------- submitter mode (run by hand) ----------------
PLOT_DIR=${1:-calibration_plots}
mkdir -p "$PLOT_DIR/slurmout"
MANIFEST="$PLOT_DIR/manifest.txt"

python - > "$MANIFEST" <<'EOF'
from sqlalchemy import distinct, select
from calibrationnet.db import get_session
from calibrationnet.models import RunPixel, SpectrumFit, TrapFilterOutput

with get_session() as session:
    rows = session.execute(
        select(distinct(RunPixel.run_number), RunPixel.segment_index,
               TrapFilterOutput.label)
        .join(TrapFilterOutput,
              TrapFilterOutput.run_pixel_id == RunPixel.id)
        .join(SpectrumFit,
              SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
        .order_by(RunPixel.run_number, RunPixel.segment_index)).all()
for run, segment, label in rows:
    print(run, segment, label)
EOF

TOTAL=$(wc -l < "$MANIFEST" | tr -d ' ')
if [ "$TOTAL" -eq 0 ]; then
    echo "nothing to do: no stored fits found."
    exit 0
fi
MAX_SUBMIT=${MAX_SUBMIT:-40}
MAX_CONCURRENT=${MAX_CONCURRENT:-40}
CHUNK=${CHUNK:-$(( (TOTAL + MAX_SUBMIT - 1) / MAX_SUBMIT ))}
N_TASKS=$(( (TOTAL + CHUNK - 1) / CHUNK ))
echo "$TOTAL run segment(s) -> $N_TASKS task(s), $CHUNK segment(s) each"

JOB=$(sbatch --parsable \
    --array=0-$((N_TASKS - 1))%${MAX_CONCURRENT} \
    --output="${PLOT_DIR}/slurmout/calibrate_%A_%a.out" \
    --export=ALL,PLOT_DIR="$PLOT_DIR",CHUNK="$CHUNK" \
    "$0")
echo "submitted array job $JOB"
echo "progress:   squeue -u \$USER"
echo "when done:  grep -h FAILED ${PLOT_DIR}/slurmout/*.out   # empty = clean"
echo "figures:    ${PLOT_DIR}/  (one QA plot per calibrated pixel)"
