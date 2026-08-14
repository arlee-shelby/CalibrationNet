"""Fit ADC -> keV calibrations from matched adc_peaks and store them.

For each run pixel (per trap filter output — the ADC scale is a property
of the trap setting, so points from different outputs never mix):

1. Collect the output's matched adc_peaks (scripts/extract_adc_peaks.py)
   across all of its fits — CE and Auger windows together.
2. Pair each peak with a keV value (AS design 2026-08-14): SIMULATION
   rows only — detected-energy frame, never mixed with NNDC physical
   energies (those exist for fit predictions, not calibration). The
   most specific row wins: bound to the pixel's source AND detector,
   else detector-specific, preferring an exact simulation-HV match;
   the per-run HV shift target = value + (row_HV - run_HV) keV (HV
   magnitudes — readback reports +27 for -27 kV; run HV rounded to
   integer) is applied at calibration time and recorded in config.
   Validated 2026-08-14: same pixels in 9409 (HV 0) vs 9469 (HV 27)
   show +27.4 keV displacement on UDET, ~0 on LDET. A line with NO
   simulation value is a hard error (cannot happen for Bi-207 — all
   8 lines seeded per detector). The exact row used is recorded per
   point, so the calibration stays reproducible whatever gets seeded
   later.
3. Require at least --min-points points (default 3 — two or fewer of
   eight is never enough). Peaks without a centroid error are excluded:
   they cannot be weighted.
4. Weighted least squares: keV = constant + linear*ADC (+ quadratic*
   ADC^2) — UNITS: constant in keV, linear in keV/ADC, quadratic in
   keV/ADC^2 — with sigma_i = sqrt((gain*centroid_err_i)^2 +
   kev_err_i^2), gain refined once from the first pass. The quadratic
   fit needs >= 4 points (one degree of freedom). CONVENTION: lmfit
   with scale_covar=False, like every fit in this database — stored
   uncertainties are never rescaled by reduced chi2 (lmfit's default
   WOULD rescale); scaling is always the analyst's later decision.
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
from pathlib import Path

from calibrationnet.calibration import fit_calibration, plot_calibration
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import (ADCPeak, Calibration, CalibrationPoint,
                                   Run, RunPixel, SpectrumFit,
                                   TrapFilterOutput)

KEV_POLICY = ("simulation kev_peaks rows only (detected-energy frame; "
              "NNDC never mixed in): most specific of source+detector > "
              "detector-only, exact simulation-HV match preferred, "
              "newest last; target = value + (row_HV - run_HV) keV in "
              "HV magnitudes, run HV rounded to integer")


def choose_kev(line, source_id, detector, run_hv):
    """The simulation keV row for this decay line, plus the run-HV
    shift in keV to add to its value.

    Candidates are simulation rows never bound to a DIFFERENT source
    and never to the OTHER detector. The most specific wins: bound to
    this pixel's source and detector > detector-only; an exact
    simulation-HV match beats canonical-HV-plus-shift; newest row
    last. shift = row_HV - run_HV in magnitudes (sign validated
    2026-08-14: 9409 [HV 0] vs 9469 [HV 27] same-pixel displacement,
    +27.4 keV on UDET, ~0 on LDET). A line with no simulation row is
    a HARD ERROR (AS ruling: cannot happen — all Bi-207 lines are
    seeded per detector; provision only if this ever fires)."""
    sims = [p for p in line.kev_peaks
            if p.origin == "simulation"
            and p.source_id in (None, source_id)
            and p.detector in (None, detector)]
    if not sims:
        raise SystemExit(
            f"ERROR: no simulated keV value for decay line "
            f"{line.label!r} (detector {detector}) — calibration "
            "targets must all come from the simulation frame. Seed "
            "them first: python scripts/seed_decay_energies.py "
            "<csv> --version <family>.")
    best = max(sims, key=lambda p: (
        source_id is not None and p.source_id == source_id,
        p.detector == detector,
        p.hv_kv == run_hv,
        p.created_at))
    shift = (best.hv_kv - run_hv) if best.hv_kv is not None else 0
    return best, shift


def store(session, rp, tfo, label, cal_type, result, pairs, make_current,
          min_points, extra_config=None):
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
                             "gain refined once",
                **(extra_config or {})},
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
    parser.add_argument("--label", default="simulation",
                        help='calibration attempt name (default '
                             '"simulation" — targets are simulation-'
                             'frame values with the run-HV shift)')
    parser.add_argument("--recipes", nargs="+", default=None,
                        metavar="FIT_LABEL",
                        help="use only peaks from these fits (e.g. "
                             "ce-6peak for a CE-only calibration); "
                             "default: every fit's peaks. Pair with "
                             "--label and --no-current to store such "
                             "variants alongside the full calibration")
    parser.add_argument("--min-points", type=int, default=3,
                        help="fewest matched points that still make a "
                             "calibration (default 3)")
    parser.add_argument("--no-current", action="store_true",
                        help="store without blessing as is_current")
    parser.add_argument("--plot", type=Path, default=None, metavar="DIR",
                        help="save a QA figure (points, fits, residuals) "
                             "per pixel into this directory")
    args = parser.parse_args()
    if args.plot is not None:
        args.plot.mkdir(parents=True, exist_ok=True)

    made = skipped = 0
    with get_session() as session:
        # The run's HV, for the target shift: magnitude (readback
        # convention: reported +27 means -27 kV) rounded to an integer
        # (27.03 is readback jitter, not physics) — AS, 2026-08-14.
        run = session.get(Run, args.run)
        if run is None or run.hv is None:
            raise SystemExit(f"ERROR: run {args.run} has no HV on "
                             "runs.hv — the calibration targets are "
                             "HV-dependent; seed the run's HV first.")
        run_hv = int(round(abs(run.hv)))
        print(f"run {args.run}: HV {run_hv} kV "
              f"(runs.hv={run.hv:+.2f}, magnitude rounded)")

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
            detector = "upper" if rp.pixel_number < 1000 else "lower"
            # runs.hv is the MAIN (UDET-side) HV. LDET floats at its
            # own small bias and does NOT follow it — validated
            # 2026-08-14: same-pixel LDET centroids are identical
            # between HV-0 and HV-27 runs. LDET HV has never been
            # powered, so its effective HV is 0 until it ever is
            # (then it needs its own runs column/slow-controls read).
            # (Bug fixed 2026-08-14: applying the main HV to LDET
            # targets shifted every HV-on LDET offset to ~-26 keV.)
            pixel_hv = run_hv if detector == "upper" else 0
            peaks_stmt = (
                select(ADCPeak)
                .join(SpectrumFit,
                      ADCPeak.spectrum_fit_id == SpectrumFit.id)
                .where(SpectrumFit.trap_filter_output_id == tfo.id,
                       ADCPeak.isotope_decay_energy_id.is_not(None))
            )
            if args.recipes:
                peaks_stmt = peaks_stmt.where(
                    SpectrumFit.label.in_(args.recipes))
            peaks = session.scalars(peaks_stmt).all()
            if not peaks:
                continue

            points, pairs, dropped, shifts, families = [], [], [], set(), set()
            for peak in sorted(peaks, key=lambda q: q.centroid_adc):
                kev_row, shift = choose_kev(peak.isotope_decay_energy,
                                            rp.source_id, detector,
                                            pixel_hv)
                if peak.centroid_error_adc is None:
                    dropped.append(peak.isotope_decay_energy.label)
                    continue
                shifts.add(shift)
                if kev_row.version:
                    families.add(kev_row.version)
                points.append((peak.centroid_adc, peak.centroid_error_adc,
                               kev_row.energy_kev + shift,
                               kev_row.energy_error_kev))
                pairs.append((peak, kev_row))
            if dropped:
                print(f"pixel {rp.pixel_number}: dropped "
                      f"{', '.join(dropped)} (no centroid error — "
                      "cannot be weighted)")
            if len(points) < args.min_points:
                print(f"pixel {rp.pixel_number}: skipped — only "
                      f"{len(points)} usable point(s), fewer than "
                      f"--min-points {args.min_points}")
                skipped += 1
                continue

            lines = ", ".join(p.isotope_decay_energy.label for p, _ in pairs)
            print(f"pixel {rp.pixel_number}: {len(points)} points ({lines})")
            results = {}
            for cal_type in ("linear", "quadratic"):
                quadratic = cal_type == "quadratic"
                if len(points) < (4 if quadratic else 3):
                    print(f"  {cal_type}: skipped (needs at least "
                          f"{4 if quadratic else 3} points for ndf >= 1)")
                    continue
                result = fit_calibration(points, quadratic)
                results[cal_type] = result
                store(session, rp, tfo, args.label, cal_type, result,
                      pairs, not args.no_current, args.min_points,
                      extra_config={
                          "detector": detector,
                          "run_hv_kv": run_hv,
                          "detector_hv_kv": pixel_hv,
                          "hv_shift_kev": sorted(shifts),
                          "kev_family": sorted(families),
                          "recipes": args.recipes or "all",
                      })
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
            if args.plot is not None and results:
                plot_calibration(points, results, rp, args.label,
                                 args.plot)
            session.commit()  # per pixel

    print(f"\n{made} calibration(s) stored, {skipped} pixel(s) below "
          "the point minimum")


if __name__ == "__main__":
    main()
