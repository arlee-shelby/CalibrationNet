"""Fit the peaks in stored trap filter outputs and save every GOOD fit
to spectrum_fits.

For each requested run pixel this pulls the trap filter output's
energies from the database, histograms and fits them with the SAME
fitting code the physics was developed with (calibrationnet/
fit_functions.py — never modified), and stores each accepted fit via
SpectrumFit.from_lmfit: parameters, errors, var_names + covariance,
chi2/ndf/reduced chi2, success, and the fit inputs (config). What each
column holds is documented in docs/fit_storage.md.

Which fits to run comes from the pixel's assigned source: each isotope
has a recipe list (calibrationnet/fit_recipes.py — ADC window, number
of peaks, peak-finder settings, starting widths). Every fit attempt
must pass the quality check (fit_is_good: converged, uncertainties
present and within thresholds, reduced chi2 within the cap); a fit
that fails it is retried with the recipe's retry starting widths and
gentler peak-finder settings (fit_attempts), then the predicted-start
rescue. If EVERY attempt fails, nothing is stored — a junk fit never
enters the database — and any previously stored fit with the same
(output, label) is removed. Re-running REPLACES a fit with the same
(output, label), so the table holds one current fit per label.

Failed-but-interesting pixels are collected for later review: pixels
that passed the statistics gate yet failed every fit attempt go to
<plot dir>/fit_failures_summary.csv (one file across all runs fitted);
--failures-detail additionally writes every non-fitted pixel of the
run, including the statistics-gate skips, to
<plot dir>/Run<run>_seg<segment>_failures.csv.

    python scripts/fit_spectra.py --run 8622 --pixels 60
    python scripts/fit_spectra.py --run 9327                # every pixel
    python scripts/fit_spectra.py --run 8622 --pixels 60 --plot fit_plots/
"""

import argparse
import csv
from pathlib import Path

import numpy as np
from sqlalchemy import select

import calibrationnet.fit_functions as fit_functions
from calibrationnet.db import get_session
from calibrationnet.fit_recipes import (ERROR_THRESHOLDS, NOMINAL_RELATION,
                                        RECIPES, SCOUT_ANCHORS, STATS_GATE,
                                        find_peaks_with_escalation,
                                        fit_attempts, fit_is_good,
                                        scale_widths)
from calibrationnet.models import RunPixel, SpectrumFit, TrapFilterOutput
from calibrationnet.queries import line_energies
from sqlalchemy.exc import IntegrityError

# Which decay-line group a fit recipe targets, by its label prefix
# ("ce-6peak" -> the CE lines): used to predict starting peak positions.
LINE_GROUP_OF = {"ce": "CE", "auger": "Auger"}

# AS ruling (2026-08-05): UDET is never calibrated with the short trap
# filter, so UDET pixels are not fitted at these labels at all.
LDET_ONLY_TF_LABELS = {"short-trap-Fall2025"}


def gain_scout(data, anchor):
    """Ratio of this pixel's gain to nominal: locate the strongest peak
    above the threshold region in the full histogram and compare with
    where the isotope's strongest line sits at nominal gain."""
    hist, _ = np.histogram(data, bins=np.arange(0, 4500))
    smoothed = np.convolve(hist, np.ones(5) / 5, mode="same")
    lo = anchor["search_from"]
    return (lo + int(np.argmax(smoothed[lo:]))) / anchor["nominal_adc"]


def pixel_relation(data, anchor):
    """C-1: this pixel's own keV<->ADC relation from its two strongest
    well-separated CE peaks (482 K and 976 K for Bi-207) — gain AND
    offset, instead of one scale factor through zero. Returns
    {"gain_kev_per_adc", "constant_kev", "anchors_adc"} or None when
    the anchors can't be identified (caller falls back to the nominal
    relation x scout ratio)."""
    if "anchor_energies" not in anchor:
        return None
    e_lo, e_hi = anchor["anchor_energies"]
    hist, _ = np.histogram(data, bins=np.arange(0, 4500))
    smoothed = np.convolve(hist, np.ones(5) / 5, mode="same")
    start = anchor["search_from"]
    adc_hi = start + int(np.argmax(smoothed[start:]))
    frac_lo, frac_hi = anchor["second_anchor_window"]
    s_lo, s_hi = int(frac_lo * adc_hi), int(frac_hi * adc_hi)
    if s_hi - s_lo < 10:
        return None
    adc_lo = s_lo + int(np.argmax(smoothed[s_lo:s_hi]))
    if adc_hi <= adc_lo:
        return None
    gain = (e_hi - e_lo) / (adc_hi - adc_lo)
    if not 0.15 < gain < 1.5:
        return None
    return {"gain_kev_per_adc": gain,
            "constant_kev": e_lo - gain * adc_lo,
            "anchors_adc": (adc_lo, adc_hi)}


def fit_from_predicted_start(data, bounds, n_peaks, widths, energies,
                             pred_ratio, relation, plot_path, title,
                             thresholds, conditioned=False):
    """Second-chance fit with COMPUTED starting guesses.

    find_peaks needs distinct bumps to build the initial parameters;
    when it can't supply them the fit never starts. Here each peak is
    instead seeded where its decay line MUST sit — the nominal keV<->ADC
    relation scaled by this pixel's gain ratio — with the amplitude read
    off the histogram right there and the recipe's width guesses. Then
    the EXACT same frozen model runs via add_parameters + do_fit.

    conditioned=True is the escalation used only after the plain
    version was rejected: a weak peak (a few dozen counts) cannot
    determine its own tail shape or roam the window without going
    degenerate, so each centroid is bounded to its prediction plus or
    minus half the gap to the neighbouring prediction (peaks cannot
    swap or collapse onto each other) and the weak peaks' tail-shape
    parameters (n, h) are held at the values strong peaks converge to,
    leaving amplitude/centroid/width free. Only parameter bounds and
    initial values change — the frozen model is untouched.

    The result is kept only if healthy (converged, all centroid/width
    errors finite and inside the quality thresholds); otherwise None,
    and the failure stands — a junk fit is never stored."""
    from lmfit import Parameters

    tag = "conditioned predicted-start" if conditioned else "predicted-start"

    hist = np.histogram(data, bins=np.arange(0, 4500))
    ydata, xdata = hist[0], hist[1]
    yunc = fit_functions.get_histogram_data_uncertainty(ydata)

    gain = relation["gain_kev_per_adc"]
    constant = relation["constant_kev"]
    smoothed = np.convolve(ydata, np.ones(5) / 5, mode="same")
    preds = [pred_ratio * (energy - constant) / gain
             for energy in energies]
    # The predictions seed ONLY the peaks. The fit window is the
    # recipe's own (scaled) window: narrowing it to the predictions
    # starves the background terms of context and makes the fit
    # degenerate (measured on 8631 p21 — cen error 5230 narrow vs
    # 3.0 on the recipe window, same starting values).
    for energy, adc in zip(energies, preds):
        if not bounds[0] + 5 < adc < bounds[1] - 5:
            print(f"    ({tag} not applicable: {energy:.0f} keV "
                  f"predicted at {adc:.0f} ADC, outside the window)")
            return None
    fx = xdata[bounds[0]:bounds[1]]
    fy = ydata[bounds[0]:bounds[1]]
    fu = yunc[bounds[0]:bounds[1]]

    init = {}
    for i, (energy, adc) in enumerate(zip(energies, preds), start=1):
        init[f"cen{i}"] = adc
        init[f"sig{i}"] = widths[f"sig{i}"]
        init[f"amp{i}"] = max(float(smoothed[int(round(adc))]), 5.0)

    params = Parameters()
    params.add("num_peaks", value=n_peaks, vary=False)
    fit_functions.add_parameters(params, init)
    if conditioned:
        amps = [init[f"amp{i}"] for i in range(1, n_peaks + 1)]
        for i, adc in enumerate(preds, start=1):
            gaps = []
            if i > 1:
                gaps.append((preds[i - 1] - preds[i - 2]) / 2)
            if i < n_peaks:
                gaps.append((preds[i] - preds[i - 1]) / 2)
            half = max(10.0, min(gaps)) if gaps else 10.0
            params[f"cen{i}"].min = adc - half
            params[f"cen{i}"].max = adc + half
        for i in range(1, n_peaks + 1):
            if amps[i - 1] < 0.15 * max(amps):
                params[f"n{i}"].value = 0.2
                params[f"n{i}"].vary = False
                params[f"h{i}"].value = 0.01
                params[f"h{i}"].vary = False
    try:
        evaluated, result = fit_functions.do_fit(params, fx, fy, fu)
    except Exception as exc:
        print(f"    ({tag} rejected: fit raised {exc})")
        return None
    if not result.success:
        print(f"    ({tag} rejected: did not converge)")
        return None
    # (health gate below; returns (result, bounds) on success)
    for prefix, threshold in thresholds.items():
        for name, par in result.params.items():
            if (name.startswith(prefix)
                    and (par.stderr is None
                         or par.stderr > threshold * abs(par.value))):
                err = ("missing" if par.stderr is None
                       else f"{par.stderr:.1f}")
                print(f"    ({tag} rejected: {name} error "
                      f"{err} fails the health gate)")
                return None

    if plot_path is not None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axis = plt.subplots(figsize=(10, 6))
        axis.plot(fx, fy)
        axis.plot(fx, evaluated,
                  label=f"Reduced $\\chi$: {result.redchi:.2f} "
                        f"({tag})")
        axis.set_ylabel("Counts")
        axis.set_xlabel("Energy (ADC)")
        axis.legend()
        axis.set_title(title)
        fig.savefig(plot_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
    return result, bounds


def predicted_window(prediction, recipe, primary_bounds):
    """The window rescue: when the predicted line positions do NOT all
    fit inside the recipe's window (short-trap offsets push the 68 keV
    Auger line past the (20, 180) window; low gain can pull lines
    below it), build the window around where the pixel's own relation
    says the lines are: first line minus 1.5x the first line gap, last
    line plus 1.5x the last gap — the same margins the trusted recipe
    windows have at standard settings. Clamped to (20, 4490); the low
    clamp keeps the hardware threshold region out.

    Returns (lo, hi), or None when every line already fits the recipe
    window (then the extra pass would just repeat the same attempts)."""
    energies, relation, pred_ratio, _tag = prediction
    n = recipe["n_peaks"]
    if len(energies) != n or n < 2:
        return None
    preds = [pred_ratio * (e - relation["constant_kev"])
             / relation["gain_kev_per_adc"] for e in energies]
    if all(primary_bounds[0] + 5 < p < primary_bounds[1] - 5
           for p in preds):
        return None
    lo = preds[0] - 1.5 * (preds[1] - preds[0])
    hi = preds[-1] + 1.5 * (preds[-1] - preds[-2])
    lo = max(20, int(round(lo)))
    hi = min(4490, int(round(hi)))
    if hi - lo < 10 * n:
        return None
    return lo, hi


def fill_in_seeds(recipe, prediction, bounds, fit_ydata, peak_finder):
    """AS-1 fill-in (docs/initial_guess_plan.md): when find_peaks found
    SOME of the peaks but not all (noisy windows make its
    raise-prominence loop step over the wanted count — 8622 p109's
    Auger goes 5 peaks -> 3 -> 1), KEEP the found peaks and construct
    starting positions only for the missing ones: each found peak is
    matched to the nearest predicted line position, the predictions
    are shifted onto the found peaks (their average offset), and the
    missing peaks are seeded at the shifted predictions. Local
    information from the data beats a pure prediction.

    Returns (seeds, found_sigmas, n_found) or None when fill-in does
    not apply (nothing found, everything found, or no predictions).
    seeds = one starting centroid per peak, in order; found_sigmas =
    {peak number: measured sigma} for the peaks find_peaks measured."""
    energies, relation, pred_ratio, _tag = prediction
    n = recipe["n_peaks"]
    if len(energies) != n:
        return None
    peaks, props = find_peaks_with_escalation(fit_ydata, peak_finder, n)
    if not 0 < len(peaks) < n:
        return None
    found = [bounds[0] + int(p) for p in peaks]
    predicted = [pred_ratio * (e - relation["constant_kev"])
                 / relation["gain_kev_per_adc"] for e in energies]

    # Match found peaks to lines: the assignment (in energy order) that
    # puts each found peak nearest its predicted position.
    from itertools import combinations
    best = None
    for combo in combinations(range(n), len(found)):
        cost = sum(abs(found[j] - predicted[i])
                   for j, i in enumerate(combo))
        if best is None or cost < best[0]:
            best = (cost, combo)
    matched = best[1]

    shift = sum(found[j] - predicted[i]
                for j, i in enumerate(matched)) / len(found)
    seeds = list(predicted)
    for j, i in enumerate(matched):
        seeds[i] = found[j]
    for i in range(n):
        if i not in matched:
            seeds[i] = predicted[i] + shift

    found_sigmas = {}
    if "widths" in props:
        for j, i in enumerate(matched):
            found_sigmas[i + 1] = max(1.0, props["widths"][j] / 2.355)
    return seeds, found_sigmas, len(found)


def fill_in_width_options(recipe, found_sigmas, scout_ratio):
    """The width guesses the fill-in tries, in order — the same idea as
    the normal retries: recipe widths first, then each retry_widths
    entry. For "measured" entries the found peaks use their own
    measured widths and the missing peaks the found peaks' average.
    Yields (widths, note)."""
    n = recipe["n_peaks"]
    yield scale_widths(recipe["widths"], scout_ratio), ""
    mean_found = (sum(found_sigmas.values()) / len(found_sigmas)
                  if found_sigmas else None)
    for entry in recipe.get("retry_widths", ()):
        if isinstance(entry, str):
            if mean_found is None:
                continue
            factor = 1.0
            rest = entry[len("measured"):].strip()
            if rest:
                factor = float(rest.lstrip("x").strip())
            widths = {}
            for i in range(1, n + 1):
                sigma = found_sigmas.get(i, mean_found)
                widths[f"sig{i}"] = max(1.0, sigma * factor)
            yield widths, f"widths: {entry}"
        else:
            widths = scale_widths(entry, scout_ratio)
            note = ",".join(f"{key}={value:g}"
                            for key, value in widths.items())
            yield widths, f"widths: {note}"


def fit_seeded(data, bounds, n_peaks, seeds, widths, tag,
               pair_separation=None):
    """Fit with GIVEN starting centroids: amplitudes read off the
    smoothed histogram at each seed, then the exact frozen model via
    add_parameters + do_fit (the same core the predicted-start rescue
    uses). Returns the lmfit result, or None with the reason printed.

    pair_separation (2-peak fits only) turns this into the BLEND model
    (AS ruling 2026-08-05: the unresolved Auger structure is a blur of
    the two Auger lines ONLY — no Pb X-rays): the pair is fitted as one
    unit with the same width and its separation FIXED by the pixel's
    own keV<->ADC relation, so the fit can no longer slide one peak
    around inside the blur (that slide is what made the free pair's
    centroid errors meaningless). Constraints are layered AFTER
    add_parameters — the frozen model is untouched (roadmap 4.4,
    strategy B). Amplitudes stay free: NNDC reports no Auger split."""
    from lmfit import Parameters

    hist = np.histogram(data, bins=np.arange(0, 4500))
    ydata, xdata = hist[0], hist[1]
    yunc = fit_functions.get_histogram_data_uncertainty(ydata)
    smoothed = np.convolve(ydata, np.ones(5) / 5, mode="same")
    for cen in seeds:
        if not bounds[0] + 5 < cen < bounds[1] - 5:
            print(f"    ({tag} not applicable: seed at {cen:.0f} ADC, "
                  "outside the window)")
            return None
    fx = xdata[bounds[0]:bounds[1]]
    fy = ydata[bounds[0]:bounds[1]]
    fu = yunc[bounds[0]:bounds[1]]

    init = {}
    for i, cen in enumerate(seeds, start=1):
        init[f"cen{i}"] = cen
        init[f"sig{i}"] = widths[f"sig{i}"]
        init[f"amp{i}"] = max(float(smoothed[int(round(cen))]), 5.0)

    params = Parameters()
    params.add("num_peaks", value=n_peaks, vary=False)
    fit_functions.add_parameters(params, init)
    if pair_separation is not None:
        params["cen2"].expr = f"cen1 + {pair_separation:.4f}"
        params["sig2"].expr = "sig1"
    try:
        _evaluated, result = fit_functions.do_fit(params, fx, fy, fu)
    except Exception as exc:
        print(f"    ({tag} rejected: fit raised {exc})")
        return None
    return result


def save_fit_figure(data, bounds, result, plot_path, note=None):
    """The figure for an ACCEPTED fit: the windowed data plus the
    fitted curve (the frozen model evaluated at the fitted parameters —
    identical to what the fit itself saw). One code path draws every
    accepted fit, and it only runs after the quality check passed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    hist, edges = np.histogram(data, bins=np.arange(0, 4500))
    fx = edges[bounds[0]:bounds[1]]
    fy = hist[bounds[0]:bounds[1]]
    evaluated = fit_functions.fit_model(result.params, fx)
    label = f"Reduced $\\chi$: {result.redchi:.2f}"
    if note:
        label += f" ({note})"
    fig, axis = plt.subplots(figsize=(10, 6))
    axis.plot(fx, fy)
    axis.plot(fx, evaluated, label=label)
    axis.set_ylabel("Counts")
    axis.set_xlabel("Energy (ADC)")
    axis.legend()
    axis.set_title(plot_path.stem)
    fig.savefig(plot_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_failed_spectrum(data, recipe, scout_ratio, prediction, plot_path,
                         note="ALL FITS FAILED (data only)", best=None):
    """Every failure still gets a figure: the raw windowed spectrum with
    the predicted line positions marked, so WHY a fit failed can be
    judged by eye (AS policy: every fit attempt must be reviewable).
    best=(result, bounds, attempt note) draws the closest-miss attempt
    dashed on top, when one converged at all."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    hist, edges = np.histogram(data, bins=np.arange(0, 4500))
    # Draw the window the best rejected attempt actually saw (the
    # predicted-window pass can sit far from the recipe window); only
    # without one, fall back to the recipe window. Never union with
    # the recipe window: at short trap that pulls in the threshold
    # tail, which dominates the y-scale (AS ruling, twice now).
    if best is not None:
        lo, hi = best[1]
    else:
        lo = int(round(recipe["bounds"][0] * scout_ratio))
        hi = int(round(recipe["bounds"][1] * scout_ratio))
    if prediction is not None:
        energies, relation, pred_ratio, _tag = prediction
        preds = [pred_ratio * (e - relation["constant_kev"])
                 / relation["gain_kev_per_adc"] for e in energies]
        lo = min(lo, max(20, int(min(preds)) - 20))
        hi = max(hi, min(4490, int(max(preds)) + 20))
    fig, axis = plt.subplots(figsize=(10, 6))
    # Fit window only: plotting from 0 let the threshold peak dominate
    # the y-scale and hid the fit region (reverted per AS 2026-08-05).
    axis.stairs(hist[lo:hi], edges[lo:hi + 1])
    if prediction is not None:
        energies, relation, pred_ratio, _tag = prediction
        for energy in energies:
            adc = (pred_ratio * (energy - relation["constant_kev"])
                   / relation["gain_kev_per_adc"])
            if lo < adc < hi:
                axis.axvline(adc, ls="--", color="grey", alpha=0.7)
        axis.plot([], [], ls="--", color="grey",
                  label="predicted line positions")
    if best is not None:
        b_result, b_bounds, b_note = best
        bx = edges[b_bounds[0]:b_bounds[1]]
        axis.plot(bx, fit_functions.fit_model(b_result.params, bx),
                  ls="--", alpha=0.9,
                  label=f"best rejected attempt: {b_note} "
                        f"(reduced $\\chi$ {b_result.redchi:.1f})")
    if prediction is not None or best is not None:
        axis.legend()
    axis.set_ylabel("Counts")
    axis.set_xlabel("Energy (ADC)")
    axis.set_title(plot_path.stem + " — " + note)
    fig.savefig(plot_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def run_recipe(data, recipe, scout_ratio=1.0, plot_path=None,
               prediction=None):
    """Try fit attempts in order until one passes the quality check.

    Returns (result, bounds, config): the accepted fit, the ADC window
    it used, and the settings that produced it. When EVERY attempt
    fails, result and bounds are None and config holds the attempt
    count and the closest miss — the caller stores nothing.

    The attempt order (see fit_attempts in fit_recipes.py):
    1. the recipe exactly as written — a pixel whose fit is healthy
       today is untouched;
    2. the recipe's retry starting widths (measured from the data,
       then the explicit sets) — widths vary first because they re-fit
       the SAME found peaks, the proven lever;
    3. the same width options at progressively gentler peak-finder
       settings — these change WHICH bumps seed the fit;
    4. the fill-in (AS-1): find_peaks found SOME peaks — keep them,
       seed only the missing ones from the line predictions shifted
       onto the found peaks, and try the same width options;
    5. for 2-peak recipes, the CONSTRAINED PAIR (the blend model):
       the two lines fitted as one blurred unit — same width,
       separation fixed by the pixel's own relation (fit_seeded);
    6. the predicted-start rescue (plain, then conditioned): every
       peak seeded where the known lines are predicted to sit;
    7. when the predicted line positions do NOT all fit inside the
       recipe window (short-trap Auger offsets, low gain), everything
       once more on the PREDICTED window — built around where the
       pixel's own relation puts the lines (predicted_window above);
    8. if the gain scout had scaled the windows, everything once more
       at the nominal windows (the scout can only ADD successes).

    Attempts whose starting inputs (found peaks + width guesses) are
    identical to an earlier attempt are skipped — gentler peak-finder
    settings often find exactly the same peaks, and refitting the
    same inputs can only give the same answer.
    """
    hist, _ = np.histogram(data, bins=np.arange(0, 4500))
    best = None        # (redchi, result, bounds, note) — the closest miss
    n_attempts = 0
    tried_inputs = set()   # (window, found peaks, widths) already fitted

    def remember_rejected(result, bounds, note):
        # Only a converged fit with uncertainties can be the closest
        # miss (anything else has no meaningful curve to draw).
        nonlocal best
        if not result.success or any(
                result.params[name].stderr is None
                for name in result.var_names):
            return
        if best is None or result.redchi < best[0]:
            best = (result.redchi, result, bounds, note)

    primary = (int(round(recipe["bounds"][0] * scout_ratio)),
               int(round(recipe["bounds"][1] * scout_ratio)))
    # Each pass = (window, width/finder scale, name). The recipe window
    # first; the predicted window only when the lines don't fit the
    # recipe window; the nominal window only when the scout had scaled.
    passes = [(primary, scout_ratio, "recipe window")]
    if prediction is not None:
        shifted = predicted_window(prediction, recipe, primary)
        if shifted is not None:
            passes.append((shifted, scout_ratio, "predicted window"))
    if scout_ratio != 1.0:
        passes.append(((recipe["bounds"][0], recipe["bounds"][1]),
                       1.0, "nominal window"))

    for bounds, window_scale, window_tag in passes:
        if window_tag != "recipe window":
            print(f"    (previous window exhausted every attempt — "
                  f"trying the {window_tag} {bounds})")
        fit_ydata = hist[bounds[0]:bounds[1]]

        # ---- normal attempts: find peaks in the data, then fit ----
        for peak_finder, widths, note in fit_attempts(recipe, fit_ydata,
                                                      window_scale):
            # Same found peaks + same width guesses = the same fit:
            # skip it (gentler finder settings often land on exactly
            # the same peaks, and refitting identical inputs can only
            # repeat the same rejection).
            found, _props = find_peaks_with_escalation(
                fit_ydata, peak_finder, recipe["n_peaks"])
            inputs = (bounds, tuple(int(p) for p in found),
                      tuple(sorted(widths.items())))
            if inputs in tried_inputs:
                print(f"    ({note}: skipped — same starting inputs as "
                      "an earlier attempt)")
                continue
            tried_inputs.add(inputs)
            n_attempts += 1
            try:
                result = fit_functions.get_fit(
                    data, bounds[0], bounds[1], peak_finder,
                    recipe["n_peaks"], widths,
                    threshold_params=recipe.get("threshold_params", {}),
                )
            except Exception as exc:
                print(f"    ({note}: fit not started — "
                      f"{type(exc).__name__} {exc})")
                continue
            ok, reason = fit_is_good(result, recipe, prediction)
            if not ok:
                print(f"    ({note}: rejected — {reason})")
                remember_rejected(result, bounds, note)
                continue
            fig_note = (note if window_tag == "recipe window"
                        else f"{note}, {window_tag}")
            if note != "recipe" or window_tag != "recipe window":
                print(f"    (accepted on retry: {fig_note})")
            if plot_path is not None:
                # Every figure states HOW its fit was made (AS request:
                # blend fits must be recognizable on the plot).
                save_fit_figure(data, bounds, result, plot_path,
                                note=fig_note)
            config = {
                "init": "find_peaks",
                "attempt": note,
                "window": window_tag,
                "peak_finder_parameters": list(peak_finder),
                "initial_peak_width_guess": widths,
                "scout_ratio": window_scale,
            }
            return result, bounds, config

        # ---- fill-in attempts (AS-1): find_peaks found SOME of the
        # ---- peaks — keep them, seed only the missing ones ----
        if prediction is not None:
            base_finder = recipe["peak_finder"]
            if window_scale != 1.0:
                scaled = list(base_finder)
                scaled[2] = max(3, int(round(scaled[2] * window_scale)))
                base_finder = tuple(scaled)
            filled = fill_in_seeds(recipe, prediction, bounds, fit_ydata,
                                   base_finder)
            if filled is not None:
                seeds, found_sigmas, n_found = filled
                for widths, width_note in fill_in_width_options(
                        recipe, found_sigmas, window_scale):
                    n_attempts += 1
                    tag = f"fill-in {n_found}/{recipe['n_peaks']} found"
                    note = f"{tag}, {width_note}" if width_note else tag
                    result = fit_seeded(data, bounds, recipe["n_peaks"],
                                        seeds, widths, note)
                    if result is None:
                        continue      # reason already printed
                    ok, reason = fit_is_good(result, recipe, prediction)
                    if not ok:
                        print(f"    ({note}: rejected — {reason})")
                        remember_rejected(result, bounds, note)
                        continue
                    fig_note = (note if window_tag == "recipe window"
                                else f"{note}, {window_tag}")
                    print(f"    (accepted via {fig_note})")
                    if plot_path is not None:
                        save_fit_figure(data, bounds, result, plot_path,
                                        note=fig_note)
                    config = {
                        "init": "fill-in",
                        "attempt": note,
                        "window": window_tag,
                        "fill_in_seeds": [round(s, 1) for s in seeds],
                        "initial_peak_width_guess": widths,
                        "prediction_relation": prediction[1],
                        "prediction_relation_source": prediction[3],
                        "prediction_ratio": prediction[2],
                        "scout_ratio": window_scale,
                    }
                    return result, bounds, config

        # ---- constrained pair (2-peak recipes): the blend model ----
        # AS ruling 2026-08-05: an unresolved Auger structure is a blur
        # of the two Auger lines ONLY (no Pb X-rays). Same width, the
        # separation fixed by the pixel's own relation — removes the
        # slide-inside-the-blur degeneracy that made the free pair's
        # centroid errors meaningless. Tried after the fill-in so a
        # genuinely resolved pair still gets free peak positions first.
        if prediction is not None and recipe["n_peaks"] == 2:
            energies, relation, pred_ratio, rel_tag = prediction
            preds = [pred_ratio * (e - relation["constant_kev"])
                     / relation["gain_kev_per_adc"] for e in energies]
            separation = preds[1] - preds[0]
            if (separation > 0
                    and bounds[0] + 5 < preds[0]
                    and preds[1] < bounds[1] - 5):
                for widths, width_note in fill_in_width_options(
                        recipe, {}, window_scale):
                    n_attempts += 1
                    note = ("constrained pair"
                            + (f", {width_note}" if width_note else ""))
                    result = fit_seeded(data, bounds, 2, preds, widths,
                                        note, pair_separation=separation)
                    if result is None:
                        continue      # reason already printed
                    ok, reason = fit_is_good(result, recipe, prediction)
                    if not ok:
                        print(f"    ({note}: rejected — {reason})")
                        remember_rejected(result, bounds, note)
                        continue
                    fig_note = (note if window_tag == "recipe window"
                                else f"{note}, {window_tag}")
                    print(f"    (accepted via {fig_note})")
                    if plot_path is not None:
                        save_fit_figure(data, bounds, result, plot_path,
                                        note=fig_note)
                    config = {
                        "init": "constrained-pair",
                        "attempt": note,
                        "window": window_tag,
                        "pair_separation_adc": round(separation, 4),
                        "initial_peak_width_guess": widths,
                        "prediction_relation": relation,
                        "prediction_relation_source": rel_tag,
                        "prediction_ratio": pred_ratio,
                        "scout_ratio": window_scale,
                    }
                    return result, bounds, config

        # ---- rescue attempts: peaks seeded at predicted positions ----
        if prediction is not None:
            energies, relation, pred_ratio, rel_tag = prediction
            rescue_widths = scale_widths(recipe["widths"], window_scale)
            for conditioned in (False, True):
                n_attempts += 1
                outcome = fit_from_predicted_start(
                    data, bounds, recipe["n_peaks"], rescue_widths,
                    energies, pred_ratio, relation, None, recipe["label"],
                    recipe.get("error_thresholds", ERROR_THRESHOLDS),
                    conditioned=conditioned)
                if outcome is None:
                    continue          # rejection reason already printed
                result, pred_bounds = outcome
                mode = ("predicted-start-conditioned" if conditioned
                        else "predicted-start")
                ok, reason = fit_is_good(result, recipe, prediction)
                if not ok:
                    print(f"    ({mode}: rejected — {reason})")
                    remember_rejected(result, pred_bounds, mode)
                    continue
                fig_note = (mode if window_tag == "recipe window"
                            else f"{mode}, {window_tag}")
                print(f"    (accepted via {fig_note}, {rel_tag} relation)")
                if plot_path is not None:
                    save_fit_figure(data, pred_bounds, result, plot_path,
                                    note=fig_note)
                config = {
                    "init": mode,
                    "attempt": mode,
                    "window": window_tag,
                    "initial_peak_width_guess": rescue_widths,
                    "prediction_relation": relation,
                    "prediction_relation_source": rel_tag,
                    "prediction_ratio": pred_ratio,
                    "scout_ratio": window_scale,
                }
                return result, pred_bounds, config

    # Nothing passed. Never store a junk fit.
    print(f"    (all {n_attempts} attempts failed the quality check — "
          "nothing stored)")
    if plot_path is not None:
        plot_failed_spectrum(
            data, recipe, scout_ratio, prediction, plot_path,
            note="ALL ATTEMPTS FAILED THE QUALITY CHECK",
            best=best[1:] if best is not None else None)
    return None, None, {
        "attempts": n_attempts,
        "best_redchi": best[0] if best is not None else None,
    }


def centroid_report(result):
    """'cen1=1330.5+-0.1 ...' — the numbers that feed calibrations."""
    lines = []
    for prefix in ("cen", "sig"):
        parts = []
        for name in sorted(p for p in result.params
                           if p.startswith(prefix)):
            value = result.params[name].value
            stderr = result.params[name].stderr
            err = "?" if stderr is None else f"{stderr:.2f}"
            parts.append(f"{name}={value:.1f}+-{err}")
        lines.append("  ".join(parts))
    return lines


# One row per pixel that ended WITHOUT a stored fit. The summary file
# keeps only the interesting stage ("all attempts failed": the pixel
# had the statistics but no fit passed); the per-run detail file keeps
# every stage.
FAILURE_FIELDS = ["run", "segment", "pixel", "tf_label", "recipe", "stage",
                  "ce_window_counts", "ce_peak_height", "attempts",
                  "best_redchi", "figure"]


def update_failure_csv(path, rows, processed_keys, stages=None):
    """Rewrite a failure CSV: keep rows from other runs/pixels, replace
    the rows of every pixel processed in THIS invocation (so a pixel
    that now fits drops out of the file), append the new failures."""
    kept = []
    if path.exists():
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row["run"], row["segment"], row["tf_label"],
                       row["pixel"])
                if key not in processed_keys:
                    kept.append(row)
    if stages is not None:
        rows = [row for row in rows if row["stage"] in stages]
    merged = kept + rows
    merged.sort(key=lambda row: (int(row["run"]), int(row["segment"]),
                                 int(row["pixel"]), row["recipe"]))
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FAILURE_FIELDS)
        writer.writeheader()
        writer.writerows(merged)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--pixels", type=int, nargs="+", default=None,
                        help="stored pixel numbers (1-127 upper, "
                             "1001-1127 lower); default: every pixel of "
                             "the segment with a trap filter output")
    parser.add_argument("--tf-label", default="nabpy-standard",
                        help="which trap filter outputs to fit")
    parser.add_argument("--isotope", default=None,
                        help="force this isotope's recipes instead of "
                             "using each pixel's assigned source")
    parser.add_argument("--plot", type=Path, default=Path("fit_plots"),
                        metavar="DIR",
                        help="save a figure per fit here (default "
                             "fit_plots/ — development policy: every fit "
                             "gets a plot for visual verification)")
    parser.add_argument("--no-plot", action="store_true",
                        help="skip the figures (also skips the failure "
                             "review CSVs, which live in the plot dir)")
    parser.add_argument("--failures-detail", action="store_true",
                        help="also write every non-fitted pixel of this "
                             "run (excluded / statistics gate / all "
                             "attempts failed) to Run<run>_seg<segment>"
                             "_failures.csv in the plot dir")
    parser.add_argument("--dev", action="store_true",
                        help="fit only the development shortlist in "
                             "data/dev_pixels.csv (one representative "
                             "pixel per known class, per trap label) — "
                             "a fast cycle for developing the fit "
                             "routine instead of whole runs")
    args = parser.parse_args()

    if args.dev:
        with open("data/dev_pixels.csv", newline="") as fh:
            dev = [int(r["pixel"]) for r in csv.DictReader(fh)
                   if r["tf_label"] == args.tf_label]
        if not dev:
            raise SystemExit(f"data/dev_pixels.csv has no pixels for "
                             f"tf_label {args.tf_label}")
        args.pixels = sorted(set(dev) & set(args.pixels)
                             if args.pixels else dev)
        print(f"--dev: fitting pixels {args.pixels} ({args.tf_label})")

    if args.no_plot:
        args.plot = None
    if args.plot is not None:
        args.plot.mkdir(parents=True, exist_ok=True)

    fitted = failed = skipped = 0
    failure_rows = []      # rows for the failure review CSVs
    processed_keys = set() # every (run, seg, tf, pixel) this invocation saw

    def record_failure(pixel, recipe_label, stage, gate_numbers=None,
                       attempts="", best_redchi="", figure=""):
        failure_rows.append({
            "run": str(args.run), "segment": str(args.segment),
            "pixel": str(pixel), "tf_label": args.tf_label,
            "recipe": recipe_label, "stage": stage,
            "ce_window_counts": ("" if gate_numbers is None
                                 else str(gate_numbers[0])),
            "ce_peak_height": ("" if gate_numbers is None
                               else f"{gate_numbers[1]:.0f}"),
            "attempts": str(attempts), "best_redchi":
                ("" if best_redchi in ("", None) else f"{best_redchi:.2f}"),
            "figure": figure,
        })

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
        pairs = session.execute(query).all()
        lines_by_isotope = {}
        if not pairs:
            raise SystemExit(
                f"no '{args.tf_label}' trap filter outputs for run "
                f"{args.run} segment {args.segment}"
                f"{f' pixels {args.pixels}' if args.pixels else ''}."
            )

        with open("data/excluded_pixels.csv", newline="") as fh:
            excluded_pixels = {int(r["pixel"]): r["reason"]
                               for r in csv.DictReader(fh)}

        for rp, tfo in pairs:
            processed_keys.add((str(args.run), str(args.segment),
                                args.tf_label, str(rp.pixel_number)))
            if (args.tf_label in LDET_ONLY_TF_LABELS
                    and rp.pixel_number < 1000):
                print(f"pixel {rp.pixel_number}: skipped (UDET is not "
                      f"fitted at {args.tf_label} — LDET only, AS ruling)")
                skipped += 1
                continue
            if rp.pixel_number in excluded_pixels:
                print(f"pixel {rp.pixel_number}: EXCLUDED — "
                      f"{excluded_pixels[rp.pixel_number]}")
                record_failure(rp.pixel_number, "", "excluded")
                continue
            isotope = args.isotope or (rp.source and rp.source.isotope.name)
            recipes = RECIPES.get(isotope)
            if recipes is None:
                reason = ("no source assigned" if isotope is None
                          else f"no recipe for {isotope}")
                print(f"pixel {rp.pixel_number}: skipped ({reason})")
                skipped += 1
                continue

            data = np.asarray(tfo.energies)
            print(f"pixel {rp.pixel_number} ({isotope}, "
                  f"{len(data)} waveforms):")

            # C-3 statistics gate (AS rule, 2026-08-05): the CE window
            # is the gatekeeper — its counts and strongest peak height
            # decide whether the pixel is worth fitting at all. The
            # Auger window's own statistics are never used: a huge
            # Auger signal can mean a WORSE pixel (the unexplained
            # ~62 ADC line dominates dead pixels).
            gate = STATS_GATE.get(isotope)
            gate_numbers = None
            if gate is not None and recipes:
                hist_all, _ = np.histogram(data, bins=np.arange(0, 4500))
                sm_all = np.convolve(hist_all, np.ones(5) / 5, mode="same")
                anchor0 = SCOUT_ANCHORS.get(isotope)
                ratio0 = 1.0
                if anchor0 is not None:
                    ratio0 = gain_scout(data, anchor0)
                    if abs(ratio0 - 1.0) <= 0.05:
                        ratio0 = 1.0
                g_lo = int(recipes[0]["bounds"][0] * ratio0)
                g_hi = int(recipes[0]["bounds"][1] * ratio0)
                window = hist_all[g_lo:g_hi]
                bg = float(np.median(window))
                peak_height = float(sm_all[g_lo:g_hi].max() - bg)
                gate_numbers = (int(window.sum()), peak_height)
                if (window.sum() < gate["min_window_counts"]
                        or peak_height < gate["min_peak_height"]):
                    print(f"  skipped: insufficient statistics "
                          f"(CE window counts={int(window.sum())}, "
                          f"peak height={peak_height:.0f}; gate needs "
                          f">={gate['min_window_counts']} and "
                          f">={gate['min_peak_height']})")
                    figure = ""
                    if args.plot is not None:
                        fig_path = args.plot / (
                            f"Run{args.run}_seg{args.segment}"
                            f"_pix{rp.pixel_number}"
                            f"_{recipes[0]['label']}.png")
                        plot_failed_spectrum(
                            data, recipes[0], ratio0, None, fig_path,
                            note="SKIPPED: insufficient statistics "
                                 "(data only)")
                        figure = fig_path.name
                    record_failure(rp.pixel_number, recipes[0]["label"],
                                   "statistics gate", gate_numbers,
                                   figure=figure)
                    skipped += 1
                    continue
                a_lo = int(recipes[-1]["bounds"][0] * ratio0) or 5
                a_hi = int(recipes[-1]["bounds"][1] * ratio0)
                auger_peak = float(sm_all[a_lo:a_hi].max()
                                   - np.median(hist_all[a_lo:a_hi]))
                if len(recipes) > 1 and auger_peak > peak_height:
                    print(f"  SUSPECT: low-energy window peak "
                          f"({auger_peak:.0f}) exceeds the strongest CE "
                          f"peak ({peak_height:.0f}) — not Auger physics")

            # Estimate this pixel's gain relative to nominal and scale
            # the recipe windows to match (within 5% = use the recipe
            # exactly, so healthy pixels fit identically to before).
            scout_ratio = 1.0
            anchor = SCOUT_ANCHORS.get(isotope)
            if anchor is not None:
                scout_ratio = gain_scout(data, anchor)
                if abs(scout_ratio - 1.0) <= 0.05:
                    scout_ratio = 1.0
                else:
                    print(f"  gain scout: strongest peak at "
                          f"{scout_ratio:.3f}x nominal — windows scaled")

            pixel_fitted = 0
            # Line energies for this isotope (once): they let the
            # second-chance fit compute starting peak positions.
            if isotope not in lines_by_isotope:
                lines_by_isotope[isotope] = line_energies(session, isotope)
            relation = NOMINAL_RELATION.get(isotope)
            # C-1: prefer this pixel's own two-anchor relation.
            relation_pix = (pixel_relation(data, anchor)
                            if anchor is not None else None)
            if relation_pix is not None:
                print(f"  pixel relation (two-anchor): "
                      f"{relation_pix['gain_kev_per_adc']:.4f} keV/ADC, "
                      f"offset {relation_pix['constant_kev']:+.1f} keV")

            anchor_fit_ok = False   # the CE recipe anchors the rest
            try:
                for recipe_index, recipe in enumerate(recipes):
                    if recipe_index > 0 and not anchor_fit_ok:
                        print(f"  {recipe['label']}: skipped (the CE fit "
                              "did not succeed — it provides the anchors)")
                        record_failure(rp.pixel_number, recipe["label"],
                                       "skipped (CE fit failed)",
                                       gate_numbers)
                        continue
                    group = LINE_GROUP_OF.get(recipe["label"].split("-")[0])
                    energies = lines_by_isotope[isotope].get(group, [])
                    prediction = None
                    if relation_pix is not None and len(energies) == recipe["n_peaks"]:
                        prediction = (energies, relation_pix, 1.0,
                                      "two-anchor")
                    elif relation is not None and len(energies) == recipe["n_peaks"]:
                        prediction = (energies, relation, scout_ratio,
                                      "nominal-scaled")
                    plot_path = None
                    if args.plot is not None:
                        plot_path = args.plot / (
                            f"Run{args.run}_seg{args.segment}"
                            f"_pix{rp.pixel_number}_{recipe['label']}.png")
                    try:
                        result, bounds, config = run_recipe(
                            data, recipe, scout_ratio, plot_path,
                            prediction)
                    except Exception as exc:
                        print(f"  {recipe['label']}: FAILED with an "
                              f"unexpected error ({exc})")
                        result, bounds = None, None
                        config = {"attempts": "", "best_redchi": None,
                                  "stage": f"error: {exc}"}

                    # One current fit per (output, label): whether this
                    # attempt succeeded or failed, any previously stored
                    # fit with the same label is stale — remove it.
                    stale = list(session.scalars(
                        select(SpectrumFit)
                        .where(SpectrumFit.trap_filter_output_id == tfo.id,
                               SpectrumFit.label == recipe["label"])
                    ))
                    for old in stale:
                        session.delete(old)

                    if result is None:
                        if stale:
                            print(f"  {recipe['label']}: previously "
                                  "stored fit removed (junk is never "
                                  "kept)")
                        print(f"  {recipe['label']}: FAILED — no attempt "
                              "passed the quality check")
                        record_failure(
                            rp.pixel_number, recipe["label"],
                            config.get("stage", "all attempts failed"),
                            gate_numbers,
                            attempts=config.get("attempts", ""),
                            best_redchi=config.get("best_redchi"),
                            figure=plot_path.name if plot_path else "")
                        failed += 1
                        continue

                    fit = SpectrumFit.from_lmfit(
                        result,
                        trap_filter_output=tfo,
                        label=recipe["label"],
                        fit_range=bounds,
                        config=config,
                    )
                    session.add(fit)
                    pixel_fitted += 1
                    if recipe_index == 0:
                        anchor_fit_ok = True

                    flag = ""
                    if scout_ratio != 1.0:
                        # Low gain is not stationary (pixels drift in and
                        # out of it), so every scout-scaled fit is flagged
                        # for human verification regardless of quality.
                        flag = (f"  <-- LOW GAIN ({scout_ratio:.2f}x) "
                                "— verify")
                    accepted = config["attempt"]
                    if config.get("window", "recipe window") != "recipe window":
                        accepted += f" ({config['window']})"
                    print(f"  {recipe['label']}: reduced_chi2="
                          f"{result.redchi:.2f} "
                          f"accepted={accepted}{flag}")
                    for line in centroid_report(result):
                        print(f"    {line}")
                session.commit()  # per pixel: an interruption loses nothing
                fitted += pixel_fitted
            except IntegrityError:
                session.rollback()
                print(f"  pixel {rp.pixel_number}: REFUSED — its fits' "
                      "peaks are referenced by a calibration (frozen). "
                      "Delete or rebuild that calibration to refit.")

    if args.plot is not None:
        update_failure_csv(args.plot / "fit_failures_summary.csv",
                           failure_rows, processed_keys,
                           stages={"all attempts failed"})
        if args.failures_detail:
            update_failure_csv(
                args.plot / f"Run{args.run}_seg{args.segment}_failures.csv",
                failure_rows, processed_keys)

    print(f"\n{fitted} fit(s) stored, {failed} failed, "
          f"{skipped} pixel(s) skipped")


if __name__ == "__main__":
    main()
