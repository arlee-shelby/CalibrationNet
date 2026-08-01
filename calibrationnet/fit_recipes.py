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
             widths={"sig1": 3, "sig2": 3, "sig3": 5}),
    ],
}
