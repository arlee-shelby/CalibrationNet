"""Diagnose pixels the pipeline cannot calibrate: how far the gain
moved, how bad the resolution got, and WHY no calibration can be
stored — the numbers behind "this pixel is out", for the group meeting
and the hardware report (AS request, 2026-10-18).

The pipeline's 6-peak CE fit needs every Bi-207 conversion line
resolved: the 554/566 and 1048/1060 L/M doublets are 12 keV apart. A
pixel whose gain has collapsed (its resolution in keV worsens with it)
blurs each doublet into one bump, the 6-peak model goes degenerate,
and blending the lines is ruled out (AS group ruling 2026-08-10,
fit_recipes.py: every peak fitted individually, same fit function as
the simulation). Such a pixel has no stored fit and no calibration —
but the two strong K lines (482 and 976 keV) are still single,
well-separated peaks, and THEY can be measured:

1. each K line is fitted INDIVIDUALLY — one peak in its own narrow
   window — with the same frozen model and retry driver the pipeline
   uses (calibrationnet/fitting.run_recipe), so the ruling holds. When
   even that fit is rejected, the line's position is read off the
   smoothed histogram (half-bin uncertainty) and flagged as such;
2. the two positions give gain and offset with propagated
   uncertainties, against the SAME target energies calibrate.py uses
   (its choose_kev policy: simulation kev_peaks rows plus the run-HV
   shift) so they compare directly with calibration_summary, and
   against the NNDC energies;
3. the 976 line's width gives the resolution (sigma in ADC and keV)
   and whether the 12 keV doublets could be resolved at all;
4. for a MERGED segment (negative index, scripts/merge_segments.py)
   the 976 line is located dwell by dwell: a merge assumes the gain
   was constant across its dwells, and a pixel whose gain stepped
   mid-run shows every line twice (run 10075 pixel 41: 3.5%);
5. the pixel's most recent stored calibration is printed beside the
   estimate — the "impact" column of the story.

The verdict names every reason the pipeline cannot store a calibration
for the pixel. NOTHING here is written to the database: a two-line
estimate has two points, below calibrate.py's three-point minimum —
it is a diagnosis, not a calibration.

    python scripts/low_gain_diagnosis.py --run 10075 --segment -1 \\
        --tf-label nabpy-standard-raster --pixels 41 86 96 99
    python scripts/low_gain_diagnosis.py --run 10075 --segment -1 \\
        --tf-label nabpy-standard-raster     # every uncalibrated pixel

Outputs (default fit_plots/low_gain_diagnosis/): one CSV per run
segment using calibration_summary's column names wherever the meaning
matches (gain_kev_per_adc, gain_error, constant_kev, ce976_sigma_kev,
...) so the rows drop into the same trend plot as the stored
calibrations; the single-line fit figures; and, for a merged segment,
a 976-position-by-dwell figure per pixel.
"""
import argparse
import csv
import re
from pathlib import Path

import numpy as np
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.fit_recipes import (NOMINAL_RELATION, RECIPES,
                                        SCOUT_ANCHORS, STATS_GATE)
from calibrationnet.fitting import (gain_scout, pixel_relation,
                                    plot_failed_spectrum, run_recipe)
from calibrationnet.models import (Calibration, Isotope,
                                   IsotopeDecayEnergy, Run, RunPixel,
                                   RunSegment, SpectrumFit,
                                   TrapFilterOutput)
from calibrationnet.queries import line_energies
# The calibration-target energy policy (which simulation row a line
# gets, plus the run-HV shift) lives in scripts/calibrate.py::choose_kev
# and is imported from there so the two scripts can never disagree
# about a line's keV. `python scripts/<script>.py` puts scripts/ on
# sys.path, which is what makes this import resolve.
from calibrate import choose_kev

# Single-line fit window around each K line, in keV: room for the
# low-energy tail below and flat background above, ending before the
# next line (the same shell's L line sits 72 keV up).
LINE_WINDOW_KEV = (-50.0, 45.0)
# One-peak plan for a K line (same keys as the Bi-207 recipes). The
# starting width is in ADC: the noise width stays ~4-6 ADC however far
# the gain drops, so the same guesses serve every pixel.
LINE_PLAN = dict(n_peaks=1,
                 peak_finder=(5, None, 20, 15, 1, None, 0.5, None),
                 widths={"sig1": 4},
                 retry_widths=("measured", "measured x 2", {"sig1": 8}))
# The 6-peak fit resolves an L/M doublet when its 12 keV separation is
# at least this many FWHM of the 976 line: pixel 96's August 2026 fits
# converged at 1.5 (FWHM 8 keV); pixels 86/99 in October are degenerate
# at 0.8-0.9 (FWHM 14-15 keV).
DOUBLET_RESOLVED_FWHM = 1.5
FWHM_PER_SIGMA = 2.3548
# A dwell contributes a 976 position when its CE window holds this much
# signal (one raster dwell is ~1/80 of the merge).
DWELL_MIN_COUNTS = 300
DWELL_MIN_HEIGHT = 15
# The gain "moved during the run" when the 976 position spread across
# dwells exceeds this many sigma of the line itself — the merged line
# is then visibly wider than the line.
MOVED_SIGMA = 1.0
HALF_BIN_ADC = 0.5


def nndc_energy(line) -> float:
    """The line's current generic NNDC keV value (mirrors
    scripts/extract_adc_peaks.py), or None if none is seeded."""
    rows = [p for p in line.kev_peaks
            if p.source_id is None and p.origin == "nndc"]
    energy = (max(rows, key=lambda p: p.created_at).energy_kev
              if rows else None)
    return energy


def target_energies(session, isotope_name, detector, source_id,
                    pixel_hv) -> dict:
    """{"energies": {NNDC keV: calibration-target keV}, "convention":
    text} for the isotope's CE lines under the SAME policy calibrate.py
    applies (choose_kev: simulation rows, most specific first, plus the
    HV shift), so gain and offset here compare directly with the stored
    calibrations."""
    lines = session.execute(
        select(IsotopeDecayEnergy)
        .join(Isotope, IsotopeDecayEnergy.isotope_id == Isotope.id)
        .where(Isotope.name == isotope_name)
    ).scalars().all()
    energies, notes = {}, set()
    for line in lines:
        nndc = nndc_energy(line)
        if not line.label.startswith("CE") or nndc is None:
            continue
        row, shift = choose_kev(line, source_id, detector, pixel_hv)
        energies[nndc] = row.energy_kev + shift
        notes.add(f"{row.origin} {row.version}, HV shift {shift:+g} keV")
    targets = {"energies": energies,
               "convention": "; ".join(sorted(notes)) or "none"}
    return targets


def doublet_separation_kev(session, isotope_name, anchor_hi) -> float:
    """Separation of the L/M pair just above the high K anchor (1048 and
    1060 for Bi-207), from the seeded NNDC lines."""
    above = [e for e in line_energies(session, isotope_name).get("CE", [])
             if e > anchor_hi + 1.0]
    separation = above[1] - above[0] if len(above) >= 2 else float("nan")
    return separation


def histogram(data) -> tuple:
    """(1-ADC histogram, 5-bin smoothed copy) — the pipeline's binning."""
    hist, _ = np.histogram(data, bins=np.arange(0, 4500))
    smoothed = np.convolve(hist, np.ones(5) / 5, mode="same")
    return hist, smoothed


def gate_report(data, hist, smoothed, isotope_name) -> dict:
    """The pipeline's statistics-gate numbers for this spectrum (mirrors
    scripts/fit_spectra.py: the first recipe's CE window, scaled by the
    gain scout outside its 5% dead band)."""
    anchor = SCOUT_ANCHORS[isotope_name]
    scout_ratio = gain_scout(data, anchor)
    window_ratio = 1.0 if abs(scout_ratio - 1.0) <= 0.05 else scout_ratio
    lo, hi = (int(b * window_ratio)
              for b in RECIPES[isotope_name][0]["bounds"])
    window = hist[lo:hi]
    counts = int(window.sum())
    height = float(smoothed[lo:hi].max() - np.median(window))
    bar = STATS_GATE[isotope_name]
    report = {"scout_ratio": scout_ratio, "ce_window": (lo, hi),
              "ce_window_counts": counts, "ce_peak_height": height,
              "passes": (counts >= bar["min_window_counts"]
                         and height >= bar["min_peak_height"]),
              "bar": bar}
    return report


def window_relation(data, isotope_name, scout_ratio) -> dict:
    """This pixel's keV<->ADC relation for placing the line windows: its
    own two-anchor relation when the anchors are found, else the
    nominal relation at the scout's gain ratio."""
    relation = pixel_relation(data, SCOUT_ANCHORS[isotope_name])
    if relation is None:
        nominal = NOMINAL_RELATION[isotope_name]
        relation = {"gain_kev_per_adc":
                        nominal["gain_kev_per_adc"] / scout_ratio,
                    "constant_kev": nominal["constant_kev"],
                    "anchors_adc": None}
    return relation


def line_window(energy, relation) -> tuple:
    """ADC window around one line from the relation and LINE_WINDOW_KEV."""
    gain, constant = relation["gain_kev_per_adc"], relation["constant_kev"]
    window = (int((energy + LINE_WINDOW_KEV[0] - constant) / gain),
              int((energy + LINE_WINDOW_KEV[1] - constant) / gain))
    return window


def peak_finder_estimate(hist, smoothed, window) -> dict:
    """Fallback when the single-line fit is rejected: the line's position
    from the smoothed histogram (half-bin uncertainty) and its width from
    the half-maximum crossings — flagged, never mistaken for a fit."""
    lo, hi = window
    excess = smoothed[lo:hi] - float(np.median(hist[lo:hi]))
    peak = int(np.argmax(excess))
    half = excess[peak] / 2
    left = peak
    while left > 0 and excess[left] > half:
        left -= 1
    right = peak
    while right < len(excess) - 1 and excess[right] > half:
        right += 1
    estimate = {"centroid": float(lo + peak), "centroid_err": HALF_BIN_ADC,
                "sigma": (right - left) / FWHM_PER_SIGMA, "sigma_err": None,
                "amplitude": float(excess[peak]), "redchi": None,
                "method": "peak finder (single-line fit rejected)"}
    return estimate


def fit_line(data, energy, window, plot_path) -> dict:
    """One K line fitted alone with the frozen model through the
    pipeline's retry driver. None when every attempt is rejected."""
    plan = dict(LINE_PLAN, label=f"k{energy:.0f}-1peak", bounds=window)
    result, _bounds, config = run_recipe(data, plan, 1.0, plot_path, None)
    if result is None:
        if plot_path is not None:
            try:
                plot_failed_spectrum(
                    data, plan, 1.0, None, plot_path,
                    note="single-line fit rejected — peak-finder "
                         "estimate used")
            except Exception as exc:        # a figure never stops the report
                print(f"    (no failure figure: {exc})")
        estimate = None
    else:
        pars = result.params
        estimate = {"centroid": pars["cen1"].value,
                    "centroid_err": pars["cen1"].stderr,
                    "sigma": pars["sig1"].value,
                    "sigma_err": pars["sig1"].stderr,
                    "amplitude": pars["amp1"].value,
                    "redchi": result.redchi,
                    "method": f"fit ({config['attempt']}, "
                              f"reduced chi2 {result.redchi:.2f})"}
    return estimate


def two_line_relation(estimate_lo, estimate_hi, energy_lo, energy_hi) -> dict:
    """Gain and offset through the two K lines, centroid uncertainties
    propagated (the two fits are independent)."""
    adc_lo, adc_hi = estimate_lo["centroid"], estimate_hi["centroid"]
    gain = (energy_hi - energy_lo) / (adc_hi - adc_lo)
    gain_error = (gain * np.hypot(estimate_hi["centroid_err"],
                                  estimate_lo["centroid_err"])
                  / (adc_hi - adc_lo))
    constant = energy_lo - gain * adc_lo
    constant_error = float(np.hypot(gain * estimate_lo["centroid_err"],
                                    adc_lo * gain_error))
    relation = {"gain_kev_per_adc": gain, "gain_error": float(gain_error),
                "constant_kev": constant, "constant_error": constant_error}
    return relation


def resolution(estimate_hi, gain, doublet_kev) -> dict:
    """The 976 line's width in ADC and keV, and the doublet separation
    in FWHM units — can the 6-peak model see two lines there?"""
    sigma_kev = estimate_hi["sigma"] * gain
    fwhm_kev = FWHM_PER_SIGMA * sigma_kev
    sigma_err = estimate_hi["sigma_err"]
    report = {"ce976_sigma_adc": estimate_hi["sigma"],
              "ce976_sigma_error_adc": sigma_err,
              "ce976_sigma_kev": sigma_kev,
              "ce976_sigma_error_kev": (sigma_err * gain
                                        if sigma_err is not None else None),
              "ce976_fwhm_kev": fwhm_kev,
              "doublet_over_fwhm": doublet_kev / fwhm_kev,
              "doublets_resolved":
                  doublet_kev / fwhm_kev >= DOUBLET_RESOLVED_FWHM}
    return report


def member_label(tfo, fallback) -> str:
    """The dwell trap label a merged output was built from (recorded in
    its source_file by merge_segments.py), else the fallback."""
    match = re.search(r"label (\S+)$", tfo.source_file or "")
    label = match.group(1) if match else fallback
    return label


def dwell_positions(session, run, pixel, label, isotope_name) -> list:
    """The 976 K position dwell by dwell (recorded dwells only), for the
    dwells with enough CE signal: [{segment, start_time, waveforms,
    counts, position_adc}] in time order."""
    anchor = SCOUT_ANCHORS[isotope_name]
    bounds = RECIPES[isotope_name][0]["bounds"]
    rows = session.execute(
        select(RunPixel.segment_index, RunSegment.start_time,
               TrapFilterOutput.energies)
        .join(TrapFilterOutput, TrapFilterOutput.run_pixel_id == RunPixel.id)
        .join(RunSegment,
              (RunSegment.run_number == RunPixel.run_number)
              & (RunSegment.segment_index == RunPixel.segment_index))
        .where(RunPixel.run_number == run, RunPixel.pixel_number == pixel,
               RunPixel.segment_index >= 0, TrapFilterOutput.label == label)
        .order_by(RunSegment.start_time)
    ).all()
    positions = []
    for segment, start_time, energies in rows:
        data = np.asarray(energies)
        hist, smoothed = histogram(data)
        ratio = gain_scout(data, anchor)
        lo, hi = (int(b * ratio) for b in bounds)
        counts = int(hist[lo:hi].sum())
        height = float(smoothed[lo:hi].max() - np.median(hist[lo:hi]))
        if counts >= DWELL_MIN_COUNTS and height >= DWELL_MIN_HEIGHT:
            positions.append({"segment": segment, "start_time": start_time,
                              "waveforms": len(data), "counts": counts,
                              "position_adc": ratio * anchor["nominal_adc"]})
    return positions


def stability(positions, sigma_adc) -> dict:
    """Spread of the 976 position across dwells, in ADC and in units of
    the line's own width; 'moved' when it exceeds MOVED_SIGMA."""
    if len(positions) < 2:
        report = {"n_dwells": len(positions), "spread_adc": None,
                  "spread_sigma": None, "moved": False}
    else:
        values = [p["position_adc"] for p in positions]
        spread = max(values) - min(values)
        spread_sigma = spread / sigma_adc if sigma_adc else None
        report = {"n_dwells": len(positions), "spread_adc": spread,
                  "spread_sigma": spread_sigma,
                  "moved": (spread_sigma is not None
                            and spread_sigma > MOVED_SIGMA)}
    return report


def last_calibration(session, pixel, label) -> dict:
    """The pixel's most recent stored linear calibration of this label
    (by segment start), with its CE fit's 976 width — or None."""
    row = session.execute(
        select(RunPixel.run_number, RunPixel.segment_index,
               RunSegment.start_time, Calibration.linear_term,
               Calibration.linear_error, Calibration.constant_term,
               Calibration.constant_error, SpectrumFit.pars)
        .join(Calibration, Calibration.run_pixel_id == RunPixel.id)
        .join(RunSegment,
              (RunSegment.run_number == RunPixel.run_number)
              & (RunSegment.segment_index == RunPixel.segment_index))
        .outerjoin(SpectrumFit,
                   (SpectrumFit.trap_filter_output_id
                    == Calibration.trap_filter_output_id)
                   & (SpectrumFit.label == "ce-6peak"))
        .where(RunPixel.pixel_number == pixel,
               Calibration.calibration_type == "linear",
               Calibration.label == label)
        .order_by(RunSegment.start_time.desc())
        .limit(1)
    ).first()
    if row is None:
        calibration = None
    else:
        (run, segment, start_time, gain, gain_err, constant, constant_err,
         pars) = row
        sigma = (pars or {}).get("sig4")
        calibration = {"run": run, "segment": segment,
                       "start_time": start_time,
                       "gain_kev_per_adc": gain, "gain_error": gain_err,
                       "constant_kev": constant,
                       "constant_error": constant_err,
                       "ce976_sigma_kev": (sigma * gain
                                           if sigma and gain else None)}
    return calibration


def verdict(gate, resolved, stable, doublet_kev) -> tuple:
    """(verdict, reasons): every reason the pipeline cannot store a
    calibration for this pixel, or how it could."""
    reasons = []
    bar = gate["bar"]
    if not gate["passes"]:
        reasons.append(
            f"statistics gate: CE-window counts {gate['ce_window_counts']} "
            f"(needs >= {bar['min_window_counts']}), peak height "
            f"{gate['ce_peak_height']:.0f} (needs >= "
            f"{bar['min_peak_height']})")
    if not resolved["doublets_resolved"]:
        reasons.append(
            f"L/M doublets unresolved: {doublet_kev:.1f} keV apart = "
            f"{resolved['doublet_over_fwhm']:.2f} FWHM of the 976 line "
            f"(sigma {resolved['ce976_sigma_kev']:.2f} keV; the 6-peak fit "
            f"needs >= {DOUBLET_RESOLVED_FWHM}) — the 6-peak model is "
            "degenerate and blending is ruled out")
    if stable["moved"]:
        reasons.append(
            f"gain moved during the run: the 976 line spans "
            f"{stable['spread_adc']:.0f} ADC across {stable['n_dwells']} "
            f"dwells ({stable['spread_sigma']:.1f} sigma of the line) — the "
            "merged spectrum shows every line twice")
    if not reasons:
        text = ("no obstacle found here — the pipeline should fit this "
                "pixel; see its fit log")
    elif len(reasons) == 1 and not gate["passes"]:
        text = ("calibratable with a gate override (fit_spectra.py "
                "--min-window-counts / --min-peak-height)")
    else:
        text = "not calibratable by the pipeline"
    return text, reasons


def plot_dwell_positions(positions, estimate_hi, pixel, path) -> None:
    """976 position by dwell start time, with the merged-segment line
    position and its width band."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.plot([p["start_time"] for p in positions],
            [p["position_adc"] for p in positions], "o", label="dwell")
    centroid, sigma = estimate_hi["centroid"], estimate_hi["sigma"]
    ax.axhline(centroid, color="C1", ls="--", label="merged segment")
    ax.axhspan(centroid - sigma, centroid + sigma, color="C1", alpha=0.15,
               label="± 1 sigma of the line")
    ax.set_ylabel("976 K line position (ADC)")
    ax.set_title(f"pixel {pixel}: 976 K position dwell by dwell")
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def pixel_targets(session, detector, source_id, ctx) -> dict:
    """The calibration-target energies for a pixel (cached per detector
    and source), or none when the run has no HV to shift them with."""
    if ctx["run_hv"] is None:
        targets = {"energies": {}, "convention": "none"}
    else:
        key = (detector, source_id)
        if key not in ctx["targets"]:
            # runs.hv is the MAIN (UDET-side) HV; LDET floats at its own
            # bias and its effective HV is 0 — the same rule as
            # scripts/calibrate.py (validated 2026-08-14).
            pixel_hv = ctx["run_hv"] if detector == "upper" else 0
            ctx["targets"][key] = target_energies(
                session, ctx["isotope"], detector, source_id, pixel_hv)
        targets = ctx["targets"][key]
    return targets


def diagnose_pixel(session, run, segment, pixel, source_id, tfo, ctx) -> dict:
    """Everything above for one pixel's output; prints the block and
    returns the CSV row."""
    isotope_name = ctx["isotope"]
    detector = "upper" if pixel < 1000 else "lower"
    data = np.asarray(tfo.energies)
    hist, smoothed = histogram(data)
    gate = gate_report(data, hist, smoothed, isotope_name)
    relation = window_relation(data, isotope_name, gate["scout_ratio"])
    energy_lo, energy_hi = SCOUT_ANCHORS[isotope_name]["anchor_energies"]
    print(f"\npixel {pixel} ({detector}, {len(data)} waveforms): gain scout "
          f"{gate['scout_ratio']:.3f}x nominal ADC scale; CE window "
          f"{gate['ce_window']} counts {gate['ce_window_counts']}, peak "
          f"height {gate['ce_peak_height']:.0f} -> gate "
          f"{'passes' if gate['passes'] else 'FAILS'}")

    estimates = {}
    for energy in (energy_lo, energy_hi):
        window = line_window(energy, relation)
        plot_path = (ctx["out"] / f"Run{run}_seg{segment}_pix{pixel}"
                     f"_k{energy:.0f}.png" if ctx["out"] else None)
        estimate = fit_line(data, energy, window, plot_path)
        if estimate is None:
            estimate = peak_finder_estimate(hist, smoothed, window)
        estimates[energy] = estimate
        sigma_err = estimate["sigma_err"]
        print(f"  {energy:.0f} keV line: window {window}, position "
              f"{estimate['centroid']:.2f} ± {estimate['centroid_err']:.2f} "
              f"ADC, sigma {estimate['sigma']:.2f}"
              f"{f' ± {sigma_err:.2f}' if sigma_err is not None else ''} "
              f"ADC — {estimate['method']}")

    nndc = two_line_relation(estimates[energy_lo], estimates[energy_hi],
                             energy_lo, energy_hi)
    targets = pixel_targets(session, detector, source_id, ctx)
    energies = targets["energies"]
    comparable = energy_lo in energies and energy_hi in energies
    if comparable:
        target = two_line_relation(estimates[energy_lo], estimates[energy_hi],
                                   energies[energy_lo], energies[energy_hi])
        convention = f"calibration-target energies: {targets['convention']}"
    else:
        target = nndc
        convention = "NNDC energies (no calibration-target energies available)"
    nominal_gain = NOMINAL_RELATION[isotope_name]["gain_kev_per_adc"]
    gain_ratio = nominal_gain / target["gain_kev_per_adc"]
    resolved = resolution(estimates[energy_hi], target["gain_kev_per_adc"],
                          ctx["doublet_kev"])
    print(f"  gain {target['gain_kev_per_adc']:.5f} ± "
          f"{target['gain_error']:.5f} keV/ADC = {gain_ratio:.3f}x nominal "
          f"ADC scale; offset {target['constant_kev']:+.2f} ± "
          f"{target['constant_error']:.2f} keV [{convention}]; "
          f"NNDC energies: gain {nndc['gain_kev_per_adc']:.5f}, offset "
          f"{nndc['constant_kev']:+.2f} keV")
    sigma_err_kev = resolved["ce976_sigma_error_kev"]
    print(f"  resolution at 976 keV: sigma {resolved['ce976_sigma_kev']:.2f}"
          f"{f' ± {sigma_err_kev:.2f}' if sigma_err_kev is not None else ''}"
          f" keV (FWHM {resolved['ce976_fwhm_kev']:.1f} keV); L/M doublets "
          f"{ctx['doublet_kev']:.1f} keV apart = "
          f"{resolved['doublet_over_fwhm']:.2f} FWHM -> "
          f"{'resolvable' if resolved['doublets_resolved'] else 'NOT resolvable'}")

    positions = []
    stable = stability(positions, None)
    if segment < 0:
        label = member_label(tfo, ctx["member_label"])
        positions = dwell_positions(session, run, pixel, label, isotope_name)
        stable = stability(positions, estimates[energy_hi]["sigma"])
        print(f"  976 position dwell by dwell ({label}, {len(positions)} "
              f"dwells with enough signal):")
        for p in positions:
            print(f"    seg {p['segment']:>3} {p['start_time']:%m-%d %H:%M} "
                  f"wf={p['waveforms']:>6} CE counts={p['counts']:>5} "
                  f"976 K at {p['position_adc']:6.0f} ADC")
        if stable["spread_adc"] is not None:
            print(f"    spread {stable['spread_adc']:.0f} ADC = "
                  f"{stable['spread_sigma']:.1f} sigma of the line -> "
                  f"{'MOVED during the run' if stable['moved'] else 'stable'}")
        if stable["moved"]:
            print("  CAUTION: the gain, offset and resolution above are NOT "
                  "meaningful — every line is double in the merge. "
                  "Diagnose the dwells separately, ex: --segment "
                  f"{positions[0]['segment']} --tf-label {label} --pixels "
                  f"{pixel}")
        if ctx["out"] and positions:
            plot_dwell_positions(
                positions, estimates[energy_hi], pixel,
                ctx["out"] / f"Run{run}_seg{segment}_pix{pixel}"
                             "_976_by_dwell.png")

    last = last_calibration(session, pixel, ctx["calibration_label"])
    if last is None:
        print(f"  no stored '{ctx['calibration_label']}' linear calibration "
              "for this pixel")
    else:
        sigma_text = (f", sigma976 {last['ce976_sigma_kev']:.2f} keV"
                      if last["ce976_sigma_kev"] else "")
        print(f"  last stored calibration: run {last['run']} seg "
              f"{last['segment']} ({last['start_time']:%Y-%m-%d}): gain "
              f"{last['gain_kev_per_adc']:.5f} ± {last['gain_error']:.5f}, "
              f"offset {last['constant_kev']:+.2f} ± "
              f"{last['constant_error']:.2f} keV{sigma_text}")
        if comparable and not stable["moved"]:
            resolution_change = (
                f", resolution x{resolved['ce976_sigma_kev'] / last['ce976_sigma_kev']:.1f}"
                if last["ce976_sigma_kev"] else "")
            print(f"  change since then: gain x"
                  f"{target['gain_kev_per_adc'] / last['gain_kev_per_adc']:.3f} "
                  f"(keV/ADC), offset "
                  f"{target['constant_kev'] - last['constant_kev']:+.2f} keV"
                  f"{resolution_change}")

    text, reasons = verdict(gate, resolved, stable, ctx["doublet_kev"])
    print(f"  VERDICT: {text}")
    for reason in reasons:
        print(f"    - {reason}")
    print("    (this two-line estimate is a diagnosis, not a calibration: "
          "2 points < calibrate.py's 3-point minimum, nothing stored)")

    row = {
        "run": run, "segment": segment, "pixel": pixel,
        "detector": detector, "tf_label": tfo.label,
        "waveforms": len(data),
        "scout_ratio": round(gate["scout_ratio"], 4),
        "ce_window_counts": gate["ce_window_counts"],
        "ce_peak_height": round(gate["ce_peak_height"]),
        "gate_passes": gate["passes"],
        "k482_adc": estimates[energy_lo]["centroid"],
        "k482_error_adc": estimates[energy_lo]["centroid_err"],
        "k482_method": estimates[energy_lo]["method"],
        "k976_adc": estimates[energy_hi]["centroid"],
        "k976_error_adc": estimates[energy_hi]["centroid_err"],
        "k976_method": estimates[energy_hi]["method"],
        "energy_convention": convention,
        "estimate_reliable": not stable["moved"],
        "gain_kev_per_adc": target["gain_kev_per_adc"],
        "gain_error": target["gain_error"],
        "constant_kev": target["constant_kev"],
        "constant_error": target["constant_error"],
        "gain_nndc_kev_per_adc": nndc["gain_kev_per_adc"],
        "constant_nndc_kev": nndc["constant_kev"],
        "gain_ratio_adc_scale": gain_ratio,
        "ce976_sigma_adc": resolved["ce976_sigma_adc"],
        "ce976_sigma_error_adc": resolved["ce976_sigma_error_adc"],
        "ce976_sigma_kev": resolved["ce976_sigma_kev"],
        "ce976_sigma_error_kev": resolved["ce976_sigma_error_kev"],
        "ce976_fwhm_kev": resolved["ce976_fwhm_kev"],
        "doublet_over_fwhm": resolved["doublet_over_fwhm"],
        "n_dwells": stable["n_dwells"],
        "dwell_976_spread_adc": stable["spread_adc"],
        "dwell_976_spread_sigma": stable["spread_sigma"],
        "last_cal_run": last["run"] if last else None,
        "last_cal_segment": last["segment"] if last else None,
        "last_cal_gain_kev_per_adc": last["gain_kev_per_adc"] if last else None,
        "last_cal_constant_kev": last["constant_kev"] if last else None,
        "last_cal_ce976_sigma_kev": last["ce976_sigma_kev"] if last else None,
        "verdict": text,
        "reasons": " | ".join(reasons),
    }
    return row


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=0,
                        help="segment index; a merged segment is negative "
                             "(default 0)")
    parser.add_argument("--pixels", type=int, nargs="+", default=None,
                        help="pixels to diagnose; default: every pixel of "
                             "the segment whose output has NO calibration")
    parser.add_argument("--tf-label", default="nabpy-standard",
                        help="trap filter outputs to read (a merged "
                             "segment's own label, ex: "
                             "nabpy-standard-raster)")
    parser.add_argument("--member-label", default="nabpy-standard",
                        help="dwell trap label for the dwell-by-dwell "
                             "check when the merged output does not "
                             "record it")
    parser.add_argument("--isotope", default="Bi-207",
                        help="only isotopes with gain-scout anchors "
                             "(fit_recipes.SCOUT_ANCHORS) can be diagnosed")
    parser.add_argument("--calibration-label", default="jin2026a",
                        help="calibration label for the 'last stored "
                             "calibration' comparison")
    parser.add_argument("--out", type=Path,
                        default=Path("fit_plots/low_gain_diagnosis"),
                        metavar="DIR",
                        help="CSV + figures go here (default "
                             "fit_plots/low_gain_diagnosis/)")
    parser.add_argument("--no-plot", action="store_true",
                        help="skip the figures (the CSV is still written "
                             "to --out)")
    args = parser.parse_args()

    if args.isotope not in SCOUT_ANCHORS:
        raise SystemExit(f"no gain-scout anchors for {args.isotope} — only "
                         f"{sorted(SCOUT_ANCHORS)} can be diagnosed")
    args.out.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        outputs = session.execute(
            select(RunPixel.pixel_number, RunPixel.source_id,
                   TrapFilterOutput.id)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == args.run,
                   RunPixel.segment_index == args.segment,
                   TrapFilterOutput.label == args.tf_label)
            .order_by(RunPixel.pixel_number)
        ).all()
        if args.pixels:
            outputs = [o for o in outputs if o.pixel_number in args.pixels]
        else:
            calibrated = set(session.scalars(
                select(Calibration.trap_filter_output_id)
                .where(Calibration.trap_filter_output_id.in_(
                    [o.id for o in outputs]))).all())
            outputs = [o for o in outputs if o.id not in calibrated]
        if not outputs:
            raise SystemExit(
                f"nothing to diagnose: no '{args.tf_label}' outputs for run "
                f"{args.run} segment {args.segment}"
                + (f" pixels {args.pixels}" if args.pixels
                   else " without a calibration"))

        # The run's HV, exactly as calibrate.py takes it: magnitude,
        # rounded (readback +27 means -27 kV; 27.03 is jitter).
        run = session.get(Run, args.run)
        run_hv = (int(round(abs(run.hv)))
                  if run is not None and run.hv is not None else None)
        if run_hv is None:
            print(f"WARNING: run {args.run} has no HV on runs.hv — the "
                  "calibration-target energies cannot be HV-shifted, so "
                  "gain and offset are reported against NNDC energies only")

        energy_hi = SCOUT_ANCHORS[args.isotope]["anchor_energies"][1]
        ctx = {
            "isotope": args.isotope,
            "run_hv": run_hv,
            "targets": {},          # filled per (detector, source)
            "doublet_kev": doublet_separation_kev(session, args.isotope,
                                                  energy_hi),
            "member_label": args.member_label,
            "calibration_label": args.calibration_label,
            "out": None if args.no_plot else args.out,
        }
        print(f"run {args.run} segment {args.segment} ({args.tf_label}"
              f"{f', HV {run_hv} kV' if run_hv is not None else ''}): "
              f"diagnosing {len(outputs)} pixel(s) — K lines fitted "
              f"individually, nothing stored")

        rows = []
        for pixel, source_id, tfo_id in outputs:
            tfo = session.get(TrapFilterOutput, tfo_id)
            row = diagnose_pixel(session, args.run, args.segment, pixel,
                                 source_id, tfo, ctx)
            rows.append(row)

    csv_path = args.out / (f"Run{args.run}_seg{args.segment}"
                           "_low_gain_diagnosis.csv")
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} pixel(s) diagnosed -> {csv_path}"
          + ("" if args.no_plot else f" (figures in {args.out}/)"))


if __name__ == "__main__":
    main()
