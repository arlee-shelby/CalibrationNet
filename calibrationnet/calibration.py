"""Calibration fit math: weighted keV-vs-ADC polynomial fits and
their QA figure. NOTHING here touches the database — shared by
scripts/calibrate.py (the database pipeline) and scripts/offline/
(files only). CONVENTION: lmfit with scale_covar=False, like every
fit in this project (docs/fit_storage.md).
"""

from lmfit import Minimizer, Parameters


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

        # scale_covar=False is the database-wide convention: store raw
        # weighted uncertainties, never redchi-rescaled (lmfit default).
        result = Minimizer(residual, params, scale_covar=False).minimize()
        gain = result.params["linear"].value
    return result


def plot_calibration(points, results, rp, label, out_dir):
    """QA figure: points with error bars, both fit curves, and per-type
    residuals in keV."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    adc = np.array([p[0] for p in points])
    kev = np.array([p[2] for p in points])
    gain0 = next(iter(results.values())).params["linear"].value
    yerr = np.sqrt((gain0 * np.array([p[1] for p in points])) ** 2
                   + np.array([p[3] or 0.0 for p in points]) ** 2)
    grid = np.linspace(adc.min() * 0.9, adc.max() * 1.05, 200)

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(8, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]}, constrained_layout=True)
    top.errorbar(adc, kev, yerr=yerr, fmt="o", ms=4, capsize=2,
                 alpha=0.75, label="matched peaks")
    for cal_type, result in results.items():
        p = result.params

        def model(x, p=p):
            y = p["constant"].value + p["linear"].value * x
            if "quadratic" in p:
                y = y + p["quadratic"].value * x * x
            return y

        top.plot(grid, model(grid), alpha=0.75,
                 label=f"{cal_type} (reduced $\\chi^2$="
                       f"{result.redchi:.2f})")
        bottom.errorbar(adc, model(adc) - kev, yerr=yerr, fmt="o", ms=4,
                        capsize=2, alpha=0.75, label=cal_type)
    bottom.axhline(0, color="grey", lw=0.8)
    top.set_ylabel("Energy (keV)")
    bottom.set_ylabel("Residual (keV)")
    bottom.set_xlabel("Centroid (ADC)")
    top.legend()
    bottom.legend(fontsize=8)
    top.set_title(f"Run {rp.run_number} seg {rp.segment_index} "
                  f"pixel {rp.pixel_number} — calibration '{label}'")
    out = out_dir / (f"Run{rp.run_number}_seg{rp.segment_index}"
                     f"_pix{rp.pixel_number}_{label}_calibration.png")
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
