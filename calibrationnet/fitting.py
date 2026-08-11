"""The spectrum-fitting engine: everything between a trap filter
output (a numpy array of energies) and an accepted-or-rejected lmfit
result — the retry ladder, the quality gate hooks, the fill-in, the
predicted-start rescue, the predicted-window pass, and the figures.

NOTHING in this module touches the database: it is shared by
scripts/fit_spectra.py (the database pipeline) and scripts/offline/
(the same pipeline run from files only, e.g. at NERSC while the GT
database is unreachable). The frozen fit model itself lives in
calibrationnet/fit_functions.py and is never modified — this module
only varies its INPUTS (docs/pipeline_roadmap.md has the policy).
"""

import csv

import numpy as np

import calibrationnet.fit_functions as fit_functions
from calibrationnet.fit_recipes import (ERROR_THRESHOLDS,
                                        find_peaks_with_escalation,
                                        fit_attempts, fit_is_good,
                                        scale_widths)

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


def fit_seeded(data, bounds, n_peaks, seeds, widths, tag):
    """Fit with GIVEN starting centroids: amplitudes read off the
    smoothed histogram at each seed, then the exact frozen model via
    add_parameters + do_fit (the same core the predicted-start rescue
    uses). Returns the lmfit result, or None with the reason printed.

    Every peak is always fitted individually and completely free —
    NO blend/tied-peak fitting of any kind (AS group ruling,
    2026-08-10): these fits must use the same fit function as the
    future SIMULATION fits, and in simulation every peak is resolved
    and fitted individually."""
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
    5. the predicted-start rescue (plain, then conditioned): every
       peak seeded where the known lines are predicted to sit;
    6. when the predicted line positions do NOT all fit inside the
       recipe window (short-trap Auger offsets, low gain), everything
       once more on the PREDICTED window — built around where the
       pixel's own relation puts the lines (predicted_window above);
    7. if the gain scout had scaled the windows, everything once more
       at the nominal windows (the scout can only ADD successes).

    Every peak is fitted individually and free — NO blend/tied-peak
    fitting of any kind (AS group ruling, 2026-08-10: same fit
    function as the future simulation fits, where all peaks resolve).

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
                # Every figure states HOW its fit was made (AS request).
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

