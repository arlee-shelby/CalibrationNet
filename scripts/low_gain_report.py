"""Report every fitted pixel's gain relative to nominal, flagging low
(or otherwise off-nominal) gain — the review AS asked for (2026-08-14):
low gain is identified from RESULTS, not from a maintained list
(data/known_low_gain_pixels.csv is historical reference only; low gain
is not stationary).

Two gain measures per stored CE fit, most direct first:

1. **anchor gain** (always available): the fitted CE 482/976 anchor
   centroids give the pixel's true ADC-per-keV slope; divided by the
   nominal relation's slope this is the pixel's gain ratio. Unlike the
   stored scout_ratio it cannot be fooled by which window pass won
   (a low-gain pixel accepted on the nominal-window pass records
   scout_ratio 1.0).
2. **calibration gain** (once calibrations exist): keV/ADC linear term
   vs nominal — the official number; printed when a current
   calibration is stored for the run pixel.

    python scripts/low_gain_report.py                  # all fitted runs
    python scripts/low_gain_report.py --runs 9469
    python scripts/low_gain_report.py --threshold 0.85 --csv report.csv
"""

import argparse
import csv

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.fit_recipes import NOMINAL_RELATION, SCOUT_ANCHORS
from calibrationnet.models import (Calibration, RunPixel, SpectrumFit,
                                   TrapFilterOutput)

# The same anchor peaks the recipes, spacing check, and extraction use:
# peaks 1 and 4 of the CE fit are the 482 K and 976 K lines.
ANCHOR_PEAKS = (1, 4)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, nargs="+", default=None,
                        help="restrict to these runs (default: all)")
    parser.add_argument("--tf-label", default=None,
                        help="restrict to one trap filter label")
    parser.add_argument("--cal-label", default="jin2026a",
                        help="calibration label family to read the "
                             "calibrated gain from (default: jin2026a)")
    parser.add_argument("--threshold", type=float, default=0.90,
                        help="flag pixels with gain ratio below this "
                             "(default 0.90); pixels above 1/threshold "
                             "are flagged as HIGH")
    parser.add_argument("--all", action="store_true",
                        help="print every fitted pixel, not only the "
                             "flagged ones")
    parser.add_argument("--csv", default=None, metavar="PATH",
                        help="also write the full table to this CSV")
    args = parser.parse_args()

    e_lo, e_hi = SCOUT_ANCHORS["Bi-207"]["anchor_energies"]
    nominal_adc_per_kev = 1.0 / NOMINAL_RELATION["Bi-207"]["gain_kev_per_adc"]

    with get_session() as session:
        query = (
            select(RunPixel, TrapFilterOutput, SpectrumFit)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .join(SpectrumFit,
                  SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
            .where(SpectrumFit.label == "ce-6peak")
            .order_by(RunPixel.run_number, RunPixel.segment_index,
                      RunPixel.pixel_number)
        )
        if args.runs:
            query = query.where(RunPixel.run_number.in_(args.runs))
        if args.tf_label:
            query = query.where(TrapFilterOutput.label == args.tf_label)

        rows = []
        for rp, tfo, fit in session.execute(query).all():
            a, b = ANCHOR_PEAKS
            cen_lo = fit.pars.get(f"cen{a}")
            cen_hi = fit.pars.get(f"cen{b}")
            if not cen_lo or not cen_hi or cen_hi <= cen_lo:
                continue
            adc_per_kev = (cen_hi - cen_lo) / (e_hi - e_lo)
            ratio = adc_per_kev / nominal_adc_per_kev
            scout = (fit.config or {}).get("scout_ratio")
            cal_gain = None
            cal = session.execute(
                select(Calibration)
                .where(Calibration.trap_filter_output_id == tfo.id,
                       Calibration.label == args.cal_label,
                       Calibration.calibration_type == "linear")).scalars().first()
            if cal is not None and cal.linear_term:
                cal_gain = (NOMINAL_RELATION["Bi-207"]["gain_kev_per_adc"]
                            / cal.linear_term)
            rows.append({
                "run": rp.run_number, "segment": rp.segment_index,
                "pixel": rp.pixel_number, "tf_label": tfo.label,
                "anchor_gain_ratio": round(ratio, 4),
                "scout_ratio": scout,
                "calibration_gain_ratio":
                    round(cal_gain, 4) if cal_gain else "",
                "flag": ("LOW" if ratio < args.threshold else
                         "HIGH" if ratio > 1.0 / args.threshold else ""),
            })

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {args.csv} ({len(rows)} fitted pixels)")

    flagged = [r for r in rows if r["flag"]]
    shown = rows if args.all else flagged
    shown = sorted(shown, key=lambda r: r["anchor_gain_ratio"])
    print(f"{len(rows)} fitted pixels; {len(flagged)} flagged outside "
          f"[{args.threshold:g}, {1/args.threshold:.3g}]:")
    for r in shown:
        print(f"  {r['flag']:>4} run {r['run']} s{r['segment']} "
              f"p{r['pixel']:>4} ({r['tf_label']}): "
              f"gain {r['anchor_gain_ratio']:.3f}"
              + (f"  scout {r['scout_ratio']:.3f}"
                 if isinstance(r['scout_ratio'], float) else "")
              + (f"  cal {r['calibration_gain_ratio']}"
                 if r['calibration_gain_ratio'] else ""))


if __name__ == "__main__":
    main()
