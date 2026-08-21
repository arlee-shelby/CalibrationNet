"""Break stored spectrum fits into adc_peaks rows, matched to isotope
decay lines — the ADC side of every future calibration point.

For each run pixel with stored fits (scripts/fit_spectra.py):

1. **Order matching.** A fit's window was built for a specific group of
   lines (the "ce-6peak" fit for the isotope's CE lines, "auger-2peak"
   for the Augers), and a stored fit always carries exactly n_peaks
   centroids — so fitted centroids in ascending ADC order pair with the
   group's line energies in ascending order.
2. **Two-anchor validation.** From the CE fit, the lowest-energy line
   and the highest-intensity line (Bi-207: CE 482 and CE 976) define a
   per-pixel two-point ADC->keV line. Every other matched peak's
   implied energy must agree with its line within --tolerance-kev, or
   the match is refused: the peak is stored with NO line (NULL) and
   flagged — a calibration will then simply not use it. Per-pixel
   anchoring also makes low-gain pixels match correctly.
3. **Sanity checks.** The strongest fitted CE peak must be the one
   matched to the highest-intensity line (warned otherwise); implied
   Auger energies come from extrapolating the CE anchor line, so their
   residuals also gauge low-ADC nonlinearity.

Writes one adc_peaks row per fitted peak (centroid/sigma/amplitude with
errors, matched isotope_decay_energy or NULL). Re-running REPLACES a
fit's peaks — unless a calibration already references them, in which
case the pixel is refused (the freeze semantics of docs/fit_storage.md):
delete or rebuild the calibration (scripts/calibrate.py) to proceed.

Unresolved blends (Cd-109 87/88, Ce-139 164/166) are NOT handled yet —
this matcher requires one fitted peak per line, which holds for the
Bi-207 recipes; blend strategies are staged in docs/pipeline_roadmap.md.

    python scripts/extract_adc_peaks.py --run 8622 --pixels 60
    python scripts/extract_adc_peaks.py --run 8622
"""

import argparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from calibrationnet.db import get_session
from calibrationnet.models import (ADCPeak, CalibrationPoint, Isotope,
                                   IsotopeDecayEnergy, RunPixel,
                                   SpectrumFit, TrapFilterOutput)

LINE_GROUPS = {"ce": "CE", "auger": "Auger"}


def nndc_energy(line: IsotopeDecayEnergy):
    """The line's current generic literature keV value (newest NNDC row
    with no source binding), or None if none is seeded."""
    rows = [p for p in line.kev_peaks
            if p.source_id is None and p.origin == "nndc"]
    if not rows:
        return None
    return max(rows, key=lambda p: p.created_at).energy_kev


def fitted_peaks(fit: SpectrumFit) -> list:
    """The fit's peaks as dicts, sorted by centroid (find_peaks yields
    them in ascending order, but the minimizer is free to move them)."""
    peaks = []
    for i in range(1, (fit.n_peaks or 0) + 1):
        peaks.append({
            "centroid": fit.pars[f"cen{i}"],
            "centroid_err": fit.par_errors.get(f"cen{i}"),
            "sigma": fit.pars[f"sig{i}"],
            "sigma_err": fit.par_errors.get(f"sig{i}"),
            "amplitude": fit.pars[f"amp{i}"],
            "amplitude_err": fit.par_errors.get(f"amp{i}"),
        })
    return sorted(peaks, key=lambda p: p["centroid"])


def extract_fits(session, fits, groups, implied, tolerance_kev) -> tuple:
    """Match and stage one pixel's fits. Returns (stored, flagged).
    May raise IntegrityError at flush when an existing calibration
    freezes the peaks being replaced."""
    stored = flagged = 0
    for fit in fits:
        if fit.success is False:
            print(f"  {fit.label}: skipped (fit did not converge — "
                  "success=False)")
            continue
        prefix = LINE_GROUPS.get((fit.label or "").split("-")[0])
        group = groups.get(prefix, [])
        peaks = fitted_peaks(fit)
        if len(peaks) != len(group):
            print(f"  {fit.label}: skipped ({len(peaks)} peaks vs "
                  f"{len(group)} {prefix} lines — blend/partial "
                  "matching not implemented yet)")
            continue
        # SKIP-FROZEN per fit (AS ruling 2026-08-20, mirroring the
        # fit driver): peaks a calibration references are KEPT — the
        # fit's siblings still extract. Before this, one frozen CE
        # aborted the whole pixel and newly fitted Augers never got
        # peaks.
        frozen = session.execute(
            select(CalibrationPoint.id)
            .join(ADCPeak, CalibrationPoint.adc_peak_id == ADCPeak.id)
            .where(ADCPeak.spectrum_fit_id == fit.id)
            .limit(1)).first() is not None
        if frozen:
            print(f"  {fit.label}: kept (frozen — its peaks are "
                  "referenced by a calibration)")
            continue
        # Replace this fit's peaks.
        for old in session.scalars(
                select(ADCPeak)
                .where(ADCPeak.spectrum_fit_id == fit.id)):
            session.delete(old)
        for peak, (line, energy) in zip(peaks, group):
            resid = implied(peak["centroid"]) - energy
            matched = abs(resid) <= tolerance_kev
            session.add(ADCPeak(
                spectrum_fit=fit,
                isotope_decay_energy=line if matched else None,
                centroid_adc=peak["centroid"],
                centroid_error_adc=peak["centroid_err"],
                sigma_adc=peak["sigma"],
                sigma_error_adc=peak["sigma_err"],
                amplitude=peak["amplitude"],
                amplitude_error=peak["amplitude_err"],
            ))
            stored += 1
            flag = ""
            if not matched:
                flagged += 1
                flag = "  <-- NOT MATCHED (stored with no line)"
            print(f"  {fit.label}: {peak['centroid']:8.1f} ADC -> "
                  f"{line.label:>9} ({energy:.3f} keV, residual "
                  f"{resid:+.2f} keV){flag}")
    return stored, flagged


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--pixels", type=int, nargs="+", default=None)
    parser.add_argument("--tf-label", default="nabpy-standard",
                        help="which trap filter outputs' fits to extract")
    parser.add_argument("--tolerance-kev", type=float, default=5.0,
                        help="max |implied - known| energy for a peak to "
                             "be matched to a line (default 5 keV; the "
                             "closest line pairs are ~12 keV apart)")
    args = parser.parse_args()

    stored = flagged = 0
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
            fits = session.scalars(
                select(SpectrumFit)
                .where(SpectrumFit.trap_filter_output_id == tfo.id)
            ).all()
            if not fits:
                continue
            # GATE-ONLY SELECTION, extraction side (AS ruling
            # 2026-08-15/20): the isotope whose lines these fits
            # target comes from the FIT ITSELF (config recipe_isotope
            # — recorded by fit_spectra.py since gate-only fitting),
            # so extraction never depends on the source assignment.
            # Fits predating that key always had an assigned source,
            # which remains the fallback. (Before this fix, unassigned
            # pixels were fitted but silently skipped here — fits with
            # zero adc_peaks and no calibration, found 2026-08-20.)
            isotope = None
            for f in fits:
                name = (f.config or {}).get("recipe_isotope")
                if name:
                    isotope = session.scalars(
                        select(Isotope).where(Isotope.name == name)
                    ).first()
                    break
            if isotope is None:
                isotope = rp.source and rp.source.isotope
            if isotope is None:
                print(f"pixel {rp.pixel_number}: skipped (fits record "
                      "no isotope and no source is assigned)")
                continue
            groups = {}
            for line in isotope.decay_energies:
                energy = nndc_energy(line)
                if energy is None:
                    continue
                for prefix in LINE_GROUPS.values():
                    if line.label.startswith(prefix):
                        groups.setdefault(prefix, []).append((line, energy))
            for group in groups.values():
                group.sort(key=lambda le: le[1])

            # The CE fit provides the anchors for everything else.
            ce_fit = next((f for f in fits
                           if f.label and f.label.startswith("ce")), None)
            ce_lines = groups.get("CE", [])
            if ce_fit is None or len(fitted_peaks(ce_fit)) != len(ce_lines):
                print(f"pixel {rp.pixel_number}: skipped (no CE fit "
                      f"matching the {len(ce_lines)} CE lines — anchors "
                      "unavailable)")
                continue
            ce_peaks = fitted_peaks(ce_fit)
            anchor_lo = 0                                  # lowest energy
            anchor_hi = max(range(len(ce_lines)),
                            key=lambda i: ce_lines[i][0].intensity or 0)
            adc_lo, kev_lo = ce_peaks[anchor_lo]["centroid"], ce_lines[anchor_lo][1]
            adc_hi, kev_hi = ce_peaks[anchor_hi]["centroid"], ce_lines[anchor_hi][1]
            slope = (kev_hi - kev_lo) / (adc_hi - adc_lo)

            def implied(adc, kev_lo=kev_lo, adc_lo=adc_lo, slope=slope):
                return kev_lo + slope * (adc - adc_lo)

            strongest = max(range(len(ce_peaks)),
                            key=lambda i: ce_peaks[i]["amplitude"])
            print(f"pixel {rp.pixel_number} ({isotope.name}): anchor gain "
                  f"{slope:.4f} keV/ADC")
            if strongest != anchor_hi:
                print(f"  WARNING: strongest CE peak (#{strongest + 1}) is "
                      f"not the highest-intensity line "
                      f"{ce_lines[anchor_hi][0].label} — check this pixel")

            try:
                n_stored, n_flagged = extract_fits(
                    session, fits, groups, implied, args.tolerance_kev)
                session.commit()  # per pixel
                stored += n_stored
                flagged += n_flagged
            except IntegrityError:
                session.rollback()
                print(f"  pixel {rp.pixel_number}: REFUSED — its peaks "
                      "are referenced by a calibration (frozen). Delete "
                      "or rebuild that calibration (scripts/calibrate.py) "
                      "to re-extract.")

    print(f"\n{stored} adc_peaks stored, {flagged} left unmatched")


if __name__ == "__main__":
    main()
