"""Per-isotope spectrum-fit recipes: which fits an isotope's spectrum
takes (ADC window, number of peaks, peak-finder settings, initial width
guesses), plus the retry attempt sequence and the quality check every
fit must pass before it is stored. Shared by scripts/fit_spectra.py
(production fitting) and scripts/benchmark_fits.py (live-vs-reference
comparisons) so both always run exactly the same fits.

Windows are ADC histogram bins; the peak finder settings and initial
width guesses are the developed-and-trusted values from the physics
workflow (see docs/pipeline_roadmap.md for the change policy). The fit
MODEL itself lives in calibrationnet/fit_functions.py and is never
changed — everything in this file only varies get_fit's INPUTS.
"""

from scipy.signal import find_peaks

RECIPES = {
    "Bi-207": [
        # Upper bound 3300 -> 3400 (AS ruling 2026-08-13): the 2026
        # gain puts peak 6 at ~3255-3290 ADC and 3300 was chopping it
        # (batch-1 review: 1023 cen6=3289 with sig6 blown to 26).
        dict(label="ce-6peak", bounds=(1200, 3400), n_peaks=6,
             peak_finder=(5, None, 20, 15, 1, None, 0.5, None),
             widths={"sig1": 3, "sig2": 3, "sig3": 3,
                     "sig4": 5, "sig5": 5, "sig6": 5},
             # Retry starting widths, tried in order when an attempt
             # fails the quality check (values are AS's to tune):
             # each peak's own measured width from the data, then twice
             # that — for blended peaks, whose half-height width
             # measures too narrow.
             retry_widths=("measured", "measured x 2"),
             # retry_beta=(...) is also available: extra starting
             # values for the tail decay length, tried by the SEEDED
             # attempts (fill-in and rescue). Deliberately NOT set
             # (AS, 2026-08-12): every accepted 2026 fit converges to
             # its detector's beta (LDET ~30-37, UDET ~8) from the
             # default start of 10 on its own — add it back only when
             # a pixel in the failure file demonstrably needs it.
             # Peak-spacing check anchors (1-based peak numbers): the
             # two strongest CE lines, 482 K and 976 K — the same
             # anchors extraction uses. The other four peaks must sit
             # where the line energies place them between these two.
             anchor_peaks=(1, 4)),
        # Window (20, 180) -> (100, 250) (AS rulings 2026-08-13/14).
        # The 2026 runs put the 56/68 keV pair at ~141-201 ADC at
        # nominal gain: the old window missed the upper peak entirely
        # AND contained the curved Compton shoulder (dies out by ~100
        # ADC), which the linear background cannot represent. The
        # bottom is measured, not guessed: batch-1's accepted Auger
        # fits ran on per-pixel windows with bottoms 91-107, and a
        # bottom scan showed no single value suits every pixel (110
        # loses 1052/1053/106; 95 recovers 1052 but breaks 1010).
        # 100 recovers the most on its own, and the pixel-to-pixel
        # spread is absorbed by the predicted-window pass, which since
        # 2026-08-14 always runs after the recipe window fails (see
        # fitting.py::predicted_window). Old-style data with the pair
        # at ~82/120 ADC lands on that same pass (~(25, 177) — in
        # effect the old window, now the fallback, not the default).
        dict(label="auger-2peak", bounds=(100, 250), n_peaks=2,
             peak_finder=(5, None, 20, 15, 1, None, 0.5, None),
             widths={"sig1": 3, "sig2": 3},
             # Measured first as a trial — compare it against the
             # explicit values after it. Auger peaks want LARGER
             # starting widths: 5 is a good lower end, then go up
             # (AS, 2026-08-05).
             retry_widths=("measured",
                           {"sig1": 5, "sig2": 5},
                           {"sig1": 8, "sig2": 8}),
             # Low-statistics window sitting on the threshold tail:
             # honest centroid errors run well past the default 5% bar
             # (AS, 2026-08-04), so the quality-check bar is 25% here.
             # (Briefly 75%/150% during 2026-08 blend development —
             # reverted with the blends: AS group ruling 2026-08-10,
             # NO blend fitting ever, all peaks fit individually, same
             # fit function as the future simulation fits.)
             error_thresholds={"cen": 0.25, "sig": 0.50}),
    ],
}

# Quality-check limits (per-recipe overrides: error_thresholds,
# max_redchi, spacing_tolerance). A fit is accepted only when every
# centroid error is within 5% of its value and every width error within
# 50% (widths carry intrinsically larger errors — at 5% even known-good
# fits fail), the reduced chi2 is at or below MAX_REDCHI (the same bar
# AS's original scripts used to trigger a retry), and the fitted peaks
# pass the peak-spacing check: a fitted peak may sit at most this
# fraction of the smallest neighbouring line gap away from where the
# known line energies place it. Chosen from the reference pixels
# (2026-08-05): correct fits sit within 26% of a gap, wrong peaks at
# 40% and beyond (8637 p77 peak 6, 8718 p99's 566, 8622 p1051's merged
# 554/566 pair).
ERROR_THRESHOLDS = {"cen": 0.05, "sig": 0.50}
MAX_REDCHI = 10
SPACING_TOLERANCE = 0.35

# Degenerate-covariance hardening (AS ruling 2026-08-13, from the
# batch-1 false passes). A real peak is never narrower than 2 ADC bins
# (the same credibility floor measure_peak_widths uses) — below that
# the "peak" is a spike riding another structure (9409s2 p1023's CE
# sig3=1.4). And a width KNOWN to better than 0.1% is not precision,
# it is a collapsed covariance direction: the best genuine fits in the
# 23-segment batch carry sig errors down to ~0.8% (high-stat CE
# peaks), while the false passes sit at 0.027% (9415s0 p1041) and
# 0.0035% (p1044) — 0.1% splits the two populations with ~8x margin on
# the good side. (Those two are NOT stderr==0 exactly; the exact-zero
# check below catches the fully-singular flavor of the same disease.)
MIN_PEAK_WIDTH = 2.0
MIN_SIG_RELATIVE_ERROR = 0.001

# Nominal ADC<->keV relation at standard trap settings, from the gold
# standard calibration (run 8622 pixel 60, docs/example_outputs.md):
# keV = constant + gain*ADC. Used ONLY to PREDICT initial peak
# positions for the rescue initializer (scaled by the pixel's scout
# ratio) — never as a calibration.
NOMINAL_RELATION = {
    "Bi-207": {"constant_kev": 28.98, "gain_kev_per_adc": 0.32809},
}

# Gain scout: where the isotope's strongest line sits at NOMINAL gain,
# so a pixel's actual gain ratio can be estimated from its histogram
# before fitting and the recipe windows scaled to match (low-gain pixels
# like UDET 95/96 in the 2025 data have their whole spectrum compressed
# to lower ADC). search_from excludes the threshold/noise region. The
# scout only changes get_fit's INPUTS — the fit code itself never sees
# any of this.
SCOUT_ANCHORS = {
    "Bi-207": {
        "nominal_adc": 2885,            # CE 976 K line at nominal gain
        "search_from": 400,             # skip the threshold/noise region
        # C-1 (docs/initial_guess_plan.md): the per-pixel keV<->ADC
        # relation comes from the two strongest well-separated CE peaks
        # (482 K and 976 K); the second anchor is searched in this
        # fraction band of the first anchor's position.
        "anchor_energies": (481.6935, 975.651),
        "second_anchor_window": (0.35, 0.62),
    },
}

# Retry ladder for the peak finder: progressively gentler prominence
# (index 3) and, last, height (index 0). The recipe's own settings are
# always attempt 1, so healthy pixels are fitted exactly as before.
def peak_finder_ladder(peak_finder):
    yield peak_finder, "recipe"
    for prominence in (10, 7, 5):
        variant = list(peak_finder)
        variant[3] = prominence
        note = f"prominence={prominence}"
        if prominence == 5:
            variant[0] = 3
            note += ",height=3"
        yield tuple(variant), note


# C-3 statistics gate (AS, 2026-08-05): a pixel is fitted only when its
# CE window carries enough signal. Chosen from the reference-pixel
# numbers: includes 1017 (24k/308) and 1031 (66k/211), excludes 1018
# (10k/40) and — deliberately, for now — 1030 (49k/177).
STATS_GATE = {
    "Bi-207": {"min_window_counts": 20000, "min_peak_height": 200},
}


def scale_widths(widths, ratio):
    """Scout-scaling of width guesses happens here and nowhere else:
    at non-nominal gain everything in ADC shrinks together, so the
    starting widths shrink with the windows (floor of 1 bin). Returns
    a copy; unchanged values at ratio 1.0."""
    if ratio == 1.0:
        return dict(widths)
    return {key: max(1.0, value * ratio) for key, value in widths.items()}


def find_peaks_with_escalation(fit_ydata, peak_finder, n_peaks):
    """Run find_peaks exactly the way the fitting code does (same
    settings, same raise-prominence-by-10 loop when too many peaks are
    found — see get_initial_peak_parameters). Returns (peaks, props)
    with AT MOST n_peaks peaks — possibly fewer, when the escalation
    steps over the wanted count (e.g. 3 peaks at one prominence, 1 at
    the next: noisy windows like 8622 p109's Auger)."""
    (height, threshold, distance, prominence,
     width, wlen, rel_height, plateau_size) = peak_finder
    while True:
        peaks, props = find_peaks(
            fit_ydata, height=height, threshold=threshold,
            distance=distance, prominence=prominence, width=width,
            wlen=wlen, rel_height=rel_height, plateau_size=plateau_size)
        if len(peaks) <= n_peaks:
            return peaks, props
        prominence += 10


def measure_peak_widths(fit_ydata, peak_finder, n_peaks):
    """Measure each found peak's starting width from the data itself.

    The recipes set width=1 and rel_height=0.5, so find_peaks reports
    every peak's full width at half height; starting sigma = that
    width / 2.355 (FWHM -> sigma), one value per peak. Returns
    {"sig1": ..., ...} or None when exactly n_peaks are not found
    (the caller skips this width option)."""
    peaks, props = find_peaks_with_escalation(fit_ydata, peak_finder,
                                              n_peaks)
    if len(peaks) != n_peaks or "widths" not in props:
        return None
    sigmas = [max(1.0, w / 2.355) for w in props["widths"]]
    # find_peaks measures at half PROMINENCE, so a weak peak riding a
    # neighbour's tail gets an absurdly narrow width (~1 bin — seen on
    # 2026 LDET: sig3=1.0 next to sig1=11.5). Such values are
    # artifacts, not measurements: replace them with the median of the
    # credible ones. If NOTHING measured credibly, the measurement
    # failed — return None so the option is skipped.
    credible = [s for s in sigmas if s >= 2.0]
    if not credible:
        return None
    fallback = sorted(credible)[len(credible) // 2]
    return {f"sig{i}": (s if s >= 2.0 else fallback)
            for i, s in enumerate(sigmas, start=1)}


def _width_options(recipe, fit_ydata, peak_finder, scout_ratio):
    """The width guesses to try at one peak-finder rung, in order:
    the recipe's own widths first (so attempt 1 is exactly today's
    fit), then each retry_widths entry — either "measured" (optionally
    "measured x N") or an explicit width set. Measured widths are never
    scout-scaled: they come from the pixel's own data at its actual
    gain. Yields (widths, note)."""
    yield scale_widths(recipe["widths"], scout_ratio), ""
    for entry in recipe.get("retry_widths", ()):
        if isinstance(entry, str):
            factor = 1.0
            rest = entry[len("measured"):].strip()
            if rest:
                factor = float(rest.lstrip("x").strip())
            measured = measure_peak_widths(fit_ydata, peak_finder,
                                           recipe["n_peaks"])
            if measured is None:
                continue
            widths = {key: max(1.0, value * factor)
                      for key, value in measured.items()}
            yield widths, f"widths: {entry}"
        else:
            widths = scale_widths(entry, scout_ratio)
            note = ",".join(f"{key}={value:g}"
                            for key, value in widths.items())
            yield widths, f"widths: {note}"


def fit_attempts(recipe, fit_ydata, scout_ratio=1.0):
    """Yield the recipe's whole attempt sequence, in order, as
    (peak_finder, widths, note). The order is: for each peak-finder
    rung (recipe settings first, then progressively gentler), every
    width option (recipe widths first, then the retries). So attempt 1
    is the recipe exactly as written — a pixel whose fit is healthy
    today is untouched — and starting widths vary before the finder
    does, because a width retry re-fits the SAME found peaks (the
    proven lever) while gentler finder settings change WHICH bumps
    seed the fit. fit_ydata is the windowed histogram the fit will
    see, used for the measured-width options."""
    base_finder = recipe["peak_finder"]
    if scout_ratio != 1.0:
        # The finder's minimum peak separation (distance, index 2)
        # shrinks with the windows, exactly as before.
        scaled = list(base_finder)
        scaled[2] = max(3, int(round(scaled[2] * scout_ratio)))
        base_finder = tuple(scaled)
    for peak_finder, finder_note in peak_finder_ladder(base_finder):
        for widths, width_note in _width_options(recipe, fit_ydata,
                                                 peak_finder, scout_ratio):
            if finder_note == "recipe":
                note = width_note or "recipe"
            else:
                note = (f"{finder_note}, {width_note}" if width_note
                        else finder_note)
            yield peak_finder, widths, note


def peak_spacing_check(result, recipe, prediction):
    """The fitted peaks must sit where the known line energies say the
    lines are, relative to each other — a fit can have small errors and
    a fine chi2 yet have grabbed the wrong structures (a threshold
    shoulder, a background hump). Returns (True, "") or (False, reason).

    Fits with 3 or more peaks are checked WITHOUT knowing the pixel's
    gain: a straight line through the two anchor peaks (recipe key
    anchor_peaks — the strongest lines) predicts where every other
    peak must sit; each may be off by at most spacing_tolerance of the
    smallest gap to its neighbouring predicted peaks. 2-peak fits use
    the pixel's own keV<->ADC relation (the same one that seeds the
    predicted-start rescue) and each peak must sit within
    spacing_tolerance of the pair's predicted separation. When no
    prediction is available the check is skipped."""
    if prediction is None:
        return True, ""
    energies, relation, pred_ratio, _tag = prediction
    n = recipe["n_peaks"]
    if len(energies) != n:
        return True, ""
    tolerance = recipe.get("spacing_tolerance", SPACING_TOLERANCE)
    cens = [result.params[f"cen{i}"].value for i in range(1, n + 1)]

    if n >= 3:
        a, b = recipe.get("anchor_peaks", (1, n))
        cen_a, cen_b = cens[a - 1], cens[b - 1]
        e_a, e_b = energies[a - 1], energies[b - 1]
        if cen_b <= cen_a:
            return False, (f"peak spacing wrong: anchor peaks out of "
                           f"order (cen{a}={cen_a:.1f}, cen{b}={cen_b:.1f})")
        adc_per_kev = (cen_b - cen_a) / (e_b - e_a)
        predicted = [cen_a + (e - e_a) * adc_per_kev for e in energies]
        for i, (cen, pred) in enumerate(zip(cens, predicted), start=1):
            if i in (a, b):
                continue
            gaps = []
            if i > 1:
                gaps.append(abs(predicted[i - 1] - predicted[i - 2]))
            if i < n:
                gaps.append(abs(predicted[i] - predicted[i - 1]))
            allowed = tolerance * min(gaps)
            if abs(cen - pred) > allowed:
                return False, (f"peak spacing wrong: cen{i}={cen:.1f} but "
                               f"the line pattern puts it at {pred:.1f} "
                               f"(off {abs(cen - pred):.1f} ADC, allowed "
                               f"{allowed:.1f})")
    elif n == 2 and relation is not None:
        gain = relation["gain_kev_per_adc"]
        constant = relation["constant_kev"]
        predicted = [pred_ratio * (e - constant) / gain for e in energies]
        allowed = tolerance * abs(predicted[1] - predicted[0])
        for i, (cen, pred) in enumerate(zip(cens, predicted), start=1):
            if abs(cen - pred) > allowed:
                return False, (f"peak spacing wrong: cen{i}={cen:.1f} but "
                               f"the pixel relation puts this line at "
                               f"{pred:.1f} (off {abs(cen - pred):.1f} ADC, "
                               f"allowed {allowed:.1f})")
    return True, ""


def fit_is_good(result, recipe, prediction=None):
    """The quality check every fit attempt must pass before it is
    stored (AS policy: a fit that fails it is retried, and if every
    attempt fails, nothing is stored). Returns (True, "") or
    (False, reason). Checks, in order: the fit converged; every fitted
    parameter has an uncertainty and none is exactly zero; every
    fitted width is a credible peak width with a credible uncertainty
    (MIN_PEAK_WIDTH / MIN_SIG_RELATIVE_ERROR above); centroid and
    width errors are within the recipe's thresholds; reduced chi2 is
    at or below the cap; the fitted peaks sit where the known line
    energies place them (the peak-spacing check above)."""
    if not result.success:
        return False, "did not converge"
    for name in result.var_names:
        par = result.params[name]
        if par.stderr is None:
            return False, "no uncertainties (singular covariance)"
        if par.stderr == 0:
            return False, (f"{name} error is exactly 0 "
                           "(degenerate covariance)")
        if (name.startswith("sig") and abs(par.value) > 0
                and par.stderr < MIN_SIG_RELATIVE_ERROR * abs(par.value)):
            return False, (f"{name} error {par.stderr:.4f} on value "
                           f"{par.value:.1f} is impossibly precise "
                           "(degenerate covariance)")
        if name.startswith("sig") and par.value < MIN_PEAK_WIDTH:
            return False, (f"{name} {par.value:.2f} is below the "
                           f"{MIN_PEAK_WIDTH:g} ADC credible peak width")
    thresholds = recipe.get("error_thresholds", ERROR_THRESHOLDS)
    for prefix, limit in thresholds.items():
        for name in sorted(result.params):
            if not name.startswith(prefix):
                continue
            par = result.params[name]
            if not par.vary:
                continue
            if abs(par.value) == 0 or par.stderr > limit * abs(par.value):
                return False, (f"{name} error {par.stderr:.2f} on value "
                               f"{par.value:.1f} exceeds the "
                               f"{limit:.0%} limit")
    max_redchi = recipe.get("max_redchi", MAX_REDCHI)
    if result.redchi > max_redchi:
        return False, f"reduced chi2 {result.redchi:.2f} > {max_redchi}"
    return peak_spacing_check(result, recipe, prediction)
