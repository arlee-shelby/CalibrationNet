"""Fit ADC -> keV calibrations from matched adc_peaks and store them.

For each run pixel (per trap filter output — the ADC scale is a property
of the trap setting, so points from different outputs never mix):

1. Collect the output's matched adc_peaks (scripts/extract_adc_peaks.py)
   across all of its fits — CE and Auger windows together.
2. Pair each peak with a keV value: a kev_peaks row bound to the pixel's
   physical source when one exists (simulation-corrected values), else
   the newest generic NNDC row. The exact row used is recorded per point,
   so the calibration stays reproducible whatever gets seeded later.
3. Require at least --min-points points (default 3 — two or fewer of
   eight is never enough). Peaks without a centroid error are excluded:
   they cannot be weighted.
4. Weighted least squares (lmfit, scale_covar=False like the spectrum
   fits): keV = constant + linear*ADC (+ quadratic*ADC^2), with
   sigma_i = sqrt((gain*centroid_err_i)^2 + kev_err_i^2), gain refined
   once from the first pass. The quadratic fit needs >= 4 points (one
   degree of freedom).
5. Store one Calibration row per type with coefficients +- errors,
   chi2/ndf/reduced chi2, success, var_names + covariance (correlations
   derive on demand — docs/fit_storage.md), config, and one
   CalibrationPoint per (adc_peak, kev_peak). By default the new
   calibration becomes is_current for its (run_pixel, type), demoting
   any previous one; --no-current stores it unblessed.

Re-running REPLACES the calibration with the same (run_pixel, output,
type, label); use a different --label to keep alternatives side by side.

    python scripts/calibrate.py --run 8622 --pixels 60
    python scripts/calibrate.py --run 8622                # every pixel
"""

import argparse

from lmfit import Minimizer, Parameters
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import (ADCPeak, Calibration, CalibrationPoint,
                                   RunPixel, SpectrumFit, TrapFilterOutput)

KEV_POLICY = ("kev_peaks row bound to the pixel's source if present, "
              "else newest generic NNDC row")


def choose_kev(line, source_id):
    """The keV row to calibrate against for this decay line."""
    bound = [p for p in line.kev_peaks if p.source_id == source_id]
    if source_id is not None and bound:
        return max(bound, key=lambda p: p.created_at)
    generic = [p for p in line.kev_peaks
               if p.source_id is None and p.origin == "nndc"]
    return max(generic, key=lambda p: p.created_at) if generic else None


def fit_calibration(points, quadratic: bool):
    """Weighted polynomial fit of keV vs ADC. points: [(adc, adc_err,
    kev, kev_err)]. Returns the lmfit MinimizerResult."""
    import numpy as np
    adc = np.array([p[0] for p in points])
    adc_err = np.array([p[1] for p in points])
    kev = np.array([p[2] for p in points])
    kev_err = np.array([p[3] or 0.0 for p in points])

    gain = (kev.max() - kev.min()) / (adc.max() - adc.min())
    for _ in range(2):  # refine the error projection once
        sigma = np.sqrt((gain * adc_err) ** 2 + kev_err ** 2)
        params = Parameters()
        params.add("constant", value=0.0)
        params.add("linear", value=gain)
        if quadratic:
            params.add("quadratic", value=0.0)

        def residual(p):
            model = p["constant"] + p["linear"] * adc
            if quadratic:
                model = model + p["quadratic"] * adc * adc
            return (model - kev) / sigma

        result = Minimizer(residual, params, scale_covar=False).minimize()
        gain = result.params["linear"].value
    return result


def store(session, rp, tfo, label, cal_type, result, pairs, make_current,
          min_points):
    """One Calibration row + its points, replacing a same-keyed one."""
    for old in session.scalars(
            select(Calibration)
            .where(Calibration.run_pixel_id == rp.id,
                   Calibration.trap_filter_output_id == tfo.id,
                   Calibration.calibration_type == cal_type,
                   Calibration.label == label)):
        session.delete(old)
    if make_current:
        for other in session.scalars(
                select(Calibration)
                .where(Calibration.run_pixel_id == rp.id,
                       Calibration.calibration_type == cal_type,
                       Calibration.is_current)):
            other.is_current = False

    p = result.params
    calibration = Calibration(
        run_pixel=rp, trap_filter_output=tfo, label=label,
        calibration_type=cal_type,
        constant_term=p["constant"].value,
        constant_error=p["constant"].stderr,
        linear_term=p["linear"].value,
        linear_error=p["linear"].stderr,
        quadratic_term=(p["quadratic"].value if "quadratic" in p else None),
        quadratic_error=(p["quadratic"].stderr if "quadratic" in p else None),
        chi2=float(result.chisqr), ndf=int(result.nfree),
        reduced_chi2=float(result.redchi), success=bool(result.success),
        var_names=list(result.var_names),
        covariance=(result.covar.tolist()
                    if result.covar is not None else None),
        config={"kev_selection": KEV_POLICY, "min_points": min_points,
                "weighting": "sigma = gain*centroid_err (+) kev_err, "
                             "gain refined once"},
        is_current=make_current,
    )
    session.add(calibration)
    for peak, kev_row in pairs:
        session.add(CalibrationPoint(calibration=calibration,
                                     adc_peak=peak, kev_peak=kev_row))
    return calibration


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--pixels", type=int, nargs="+", default=None)
    parser.add_argument("--tf-label", default="nabpy-standard")
    parser.add_argument("--label", default="nndc",
                        help='calibration attempt name (default "nndc")')
    parser.add_argument("--min-points", type=int, default=3,
                        help="fewest matched points that still make a "
                             "calibration (default 3)")
    parser.add_argument("--no-current", action="store_true",
                        help="store without blessing as is_current")
    args = parser.parse_args()

    made = skipped = 0
    with get_session() as session:
        query = (
            select(RunPixel, TrapFilterOutput)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == args.run,
                   RunPixel.segment_index == args.segment,
                   TrapFilterOutput.label == args.tf_label)
            .order_by(RunPixel.pixel_number)
        )
        if args.pixels:
            query = query.where(RunPixel.pixel_number.in_(args.pixels))

        for rp, tfo in session.execute(query).all():
            peaks = session.scalars(
                select(ADCPeak)
                .join(SpectrumFit,
                      ADCPeak.spectrum_fit_id == SpectrumFit.id)
                .where(SpectrumFit.trap_filter_output_id == tfo.id,
                       ADCPeak.isotope_decay_energy_id.is_not(None))
            ).all()
            if not peaks:
                continue

            points, pairs, dropped = [], [], []
            for peak in sorted(peaks, key=lambda q: q.centroid_adc):
                kev_row = choose_kev(peak.isotope_decay_energy,
                                     rp.source_id)
                if kev_row is None or peak.centroid_error_adc is None:
                    dropped.append(peak.isotope_decay_energy.label)
                    continue
                points.append((peak.centroid_adc, peak.centroid_error_adc,
                               kev_row.energy_kev, kev_row.energy_error_kev))
                pairs.append((peak, kev_row))
            if dropped:
                print(f"pixel {rp.pixel_number}: dropped "
                      f"{', '.join(dropped)} (no keV value or no "
                      "centroid error)")
            if len(points) < args.min_points:
                print(f"pixel {rp.pixel_number}: skipped — only "
                      f"{len(points)} usable point(s), fewer than "
                      f"--min-points {args.min_points}")
                skipped += 1
                continue

            lines = ", ".join(p.isotope_decay_energy.label for p, _ in pairs)
            print(f"pixel {rp.pixel_number}: {len(points)} points ({lines})")
            for cal_type in ("linear", "quadratic"):
                quadratic = cal_type == "quadratic"
                if len(points) < (4 if quadratic else 3):
                    print(f"  {cal_type}: skipped (needs at least "
                          f"{4 if quadratic else 3} points for ndf >= 1)")
                    continue
                result = fit_calibration(points, quadratic)
                store(session, rp, tfo, args.label, cal_type, result,
                      pairs, not args.no_current, args.min_points)
                made += 1
                p = result.params
                quad = (f" quadratic={p['quadratic'].value:+.3e}"
                        f"+-{p['quadratic'].stderr:.1e}"
                        if quadratic else "")
                print(f"  {cal_type}: keV = "
                      f"{p['constant'].value:+.3f}+-{p['constant'].stderr:.3f}"
                      f" + {p['linear'].value:.5f}+-{p['linear'].stderr:.5f}"
                      f"*ADC{quad}  "
                      f"(reduced_chi2={result.redchi:.2f}, "
                      f"ndf={result.nfree}, success={result.success})")
            session.commit()  # per pixel

    print(f"\n{made} calibration(s) stored, {skipped} pixel(s) below "
          "the point minimum")


if __name__ == "__main__":
    main()
