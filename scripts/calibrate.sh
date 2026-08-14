#!/bin/bash
# Stage 2 + stage 4 over EVERYTHING: extract adc_peaks from every
# stored spectrum fit, then fit and store a calibration for every
# pixel with matched peaks — one (run, segment, trap label) at a time,
# discovered from the database itself, so this needs no run list and
# re-running it redoes everything idempotently (extraction and
# calibration both REPLACE their own previous rows).
#
#   ./scripts/calibrate.sh [plot_dir]
#
# Plot dir (default calibration_plots) receives one QA figure per
# calibrated pixel (points, linear + quadratic fits, residuals) — the
# review artifact for the calibration eye pass. Logs go next to it.
#
# Serial and light (SQL + weighted least squares — no spectrum
# fitting): the full ~340 segment batch is roughly an hour. Run it in
# a terminal or under sbatch --wrap if the login node frowns:
#   sbatch -A gts-ajezghani3 -N1 -c1 --mem=8gb -t 4:00:00 --qos=embers \
#       --wrap="cd $PWD && ./scripts/calibrate.sh"
#
# A failing segment does not stop the batch; failures are collected
# and reported at the end (and in the logs).

set -uo pipefail

PLOT_DIR=${1:-calibration_plots}
mkdir -p "$PLOT_DIR"
EXTRACT_LOG="$PLOT_DIR/extract.log"
CALIBRATE_LOG="$PLOT_DIR/calibrate.log"
: > "$EXTRACT_LOG"; : > "$CALIBRATE_LOG"

# Every (run, segment, trap label) that holds stored fits.
COMBOS=$(python - <<'EOF'
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
)
TOTAL=$(echo "$COMBOS" | wc -l | tr -d ' ')
echo "$TOTAL run segment(s) hold fits"

FAILED_EXTRACT=""; FAILED_CAL=""
N=0
while read -r RUN SEG LABEL; do
    N=$((N + 1))
    echo "[$N/$TOTAL] extract: run $RUN seg $SEG ($LABEL)"
    if ! python scripts/extract_adc_peaks.py --run "$RUN" \
            --segment "$SEG" --tf-label "$LABEL" \
            >> "$EXTRACT_LOG" 2>&1; then
        echo "    EXTRACTION FAILED (see $EXTRACT_LOG)"
        FAILED_EXTRACT="$FAILED_EXTRACT $RUN/$SEG/$LABEL"
    fi
done <<< "$COMBOS"

N=0
while read -r RUN SEG LABEL; do
    N=$((N + 1))
    echo "[$N/$TOTAL] calibrate: run $RUN seg $SEG ($LABEL)"
    if ! python scripts/calibrate.py --run "$RUN" --segment "$SEG" \
            --tf-label "$LABEL" --plot "$PLOT_DIR" \
            >> "$CALIBRATE_LOG" 2>&1; then
        echo "    CALIBRATION FAILED (see $CALIBRATE_LOG)"
        FAILED_CAL="$FAILED_CAL $RUN/$SEG/$LABEL"
    fi
done <<< "$COMBOS"

echo
echo "extraction summary:"
grep -c "adc_peaks stored" "$EXTRACT_LOG" | xargs echo "  segments completed:"
echo "calibration summary:"
grep -hE "calibration\(s\) stored" "$CALIBRATE_LOG" | \
    awk '{stored += $1} END {print "  calibrations stored:", stored}'
if [ -n "$FAILED_EXTRACT" ]; then
    echo "FAILED extractions:$FAILED_EXTRACT"
fi
if [ -n "$FAILED_CAL" ]; then
    echo "FAILED calibrations:$FAILED_CAL"
fi
[ -z "$FAILED_EXTRACT" ] && [ -z "$FAILED_CAL" ] && echo "ALL CLEAN"
