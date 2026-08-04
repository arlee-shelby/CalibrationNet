"""Per-isotope spectrum-fit recipes: which fits an isotope's spectrum
takes (ADC window, number of peaks, peak-finder settings, initial width
guesses). Shared by scripts/fit_spectra.py (production fitting) and
scripts/benchmark_fits.py (live-vs-reference comparisons) so both always
run exactly the same fits.

Windows are ADC histogram bins; the peak finder settings and initial
width guesses are the developed-and-trusted values from the physics
workflow (see docs/pipeline_roadmap.md for the change policy).
"""

RECIPES = {
    "Bi-207": [
        dict(label="ce-6peak", bounds=(1200, 3300), n_peaks=6,
             peak_finder=(5, None, 20, 15, 1, None, 0.5, None),
             widths={"sig1": 3, "sig2": 3, "sig3": 3,
                     "sig4": 5, "sig5": 5, "sig6": 5}),
        dict(label="auger-2peak", bounds=(20, 180), n_peaks=2,
             peak_finder=(5, None, 20, 15, 1, None, 0.5, None),
             widths={"sig1": 3, "sig2": 3, "sig3": 5},
             # Low-statistics window sitting on the threshold tail:
             # honest centroid errors run well past the default 5% bar
             # (AS, 2026-08-04), so the health/CHECK bar is 25% here.
             error_thresholds={"cen": 0.25, "sig": 0.50}),
    ],
}

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
    "Bi-207": {"nominal_adc": 2885, "search_from": 400},  # CE 976 K line
}

# Retry ladder for the peak finder when a fit fails outright (find_peaks
# returning fewer peaks than the recipe needs): progressively gentler
# prominence (index 3) and, last, height (index 0). The recipe's own
# settings are always attempt 1, so healthy pixels are fitted exactly
# as before; whatever attempt succeeds is recorded in the fit's config.
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
