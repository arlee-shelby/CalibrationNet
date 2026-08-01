"""Fit the peaks in stored trap filter outputs and save every fit to
spectrum_fits.

For each requested run pixel this pulls the trap filter output's
energies from the database, histograms and fits them with the SAME
fitting code the physics was developed with (calibrationnet/
fit_functions.py — never modified), and stores each fit via
SpectrumFit.from_lmfit: parameters, errors, var_names + covariance,
chi2/ndf/reduced chi2, success, and the fit inputs (config). What each
column holds is documented in docs/fit_storage.md.

Which fits to run comes from the pixel's assigned source: each isotope
has a recipe list (ADC window, number of peaks, peak-finder settings,
initial widths). One output therefore usually produces several
spectrum_fits rows — e.g. Bi-207 gets the 6-peak conversion-electron
fit and the 2-peak Auger fit. Re-running REPLACES a fit with the same
(output, label), so the table holds one current fit per label.

    python scripts/fit_spectra.py --run 8622 --pixels 60
    python scripts/fit_spectra.py --run 9327                # every pixel
    python scripts/fit_spectra.py --run 8622 --pixels 60 --plot fit_plots/
"""

import argparse
from pathlib import Path

import numpy as np
from sqlalchemy import select

import calibrationnet.fit_functions as fit_functions
from calibrationnet.db import get_session
from calibrationnet.fit_recipes import RECIPES
from calibrationnet.models import RunPixel, SpectrumFit, TrapFilterOutput


def run_recipe(data, recipe, plot_path=None):
    """One fit, optionally saving a QA plot. Returns the MinimizerResult."""
    axis = None
    if plot_path is not None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axis = plt.subplots(figsize=(10, 6))
    result = fit_functions.get_fit(
        data, recipe["bounds"][0], recipe["bounds"][1],
        recipe["peak_finder"], recipe["n_peaks"], recipe["widths"],
        plot=plot_path is not None, axis=axis,
    )
    if plot_path is not None:
        axis.set_title(plot_path.stem)
        fig.savefig(plot_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
    return result


# A fit is flagged CHECK when any centroid error exceeds 5% of its
# value, or any width error exceeds 50% (widths carry intrinsically
# larger errors — at 5% even known-good fits flag). Missing errors flag.
ERROR_THRESHOLDS = {"cen": 0.05, "sig": 0.50}


def centroid_report(result):
    """'cen1=1330.5+-0.1 ...' — the numbers that feed calibrations."""
    lines, suspicious = [], False
    for prefix, threshold in ERROR_THRESHOLDS.items():
        parts = []
        for name in sorted(p for p in result.params
                           if p.startswith(prefix)):
            value = result.params[name].value
            stderr = result.params[name].stderr
            if stderr is None or stderr > threshold * abs(value):
                suspicious = True
            err = "?" if stderr is None else f"{stderr:.2f}"
            parts.append(f"{name}={value:.1f}+-{err}")
        lines.append("  ".join(parts))
    return lines, suspicious


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
    parser.add_argument("--plot", type=Path, default=None, metavar="DIR",
                        help="save a QA plot per fit into this directory")
    args = parser.parse_args()

    if args.plot is not None:
        args.plot.mkdir(parents=True, exist_ok=True)

    fitted = failed = skipped = 0
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
        if not pairs:
            raise SystemExit(
                f"no '{args.tf_label}' trap filter outputs for run "
                f"{args.run} segment {args.segment}"
                f"{f' pixels {args.pixels}' if args.pixels else ''}."
            )

        for rp, tfo in pairs:
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
            for recipe in recipes:
                plot_path = None
                if args.plot is not None:
                    plot_path = args.plot / (
                        f"Run{args.run}_seg{args.segment}"
                        f"_pix{rp.pixel_number}_{recipe['label']}.png")
                try:
                    result = run_recipe(data, recipe, plot_path)
                except Exception as exc:
                    print(f"  {recipe['label']}: FAILED ({exc})")
                    failed += 1
                    continue

                # One current fit per (output, label): replace, not pile up.
                for old in session.scalars(
                    select(SpectrumFit)
                    .where(SpectrumFit.trap_filter_output_id == tfo.id,
                           SpectrumFit.label == recipe["label"])
                ):
                    session.delete(old)
                fit = SpectrumFit.from_lmfit(
                    result,
                    trap_filter_output=tfo,
                    label=recipe["label"],
                    fit_range=recipe["bounds"],
                    config={
                        "peak_finder_parameters": list(recipe["peak_finder"]),
                        "initial_peak_width_guess": recipe["widths"],
                    },
                )
                session.add(fit)
                fitted += 1

                report_lines, suspicious = centroid_report(result)
                flag = "  <-- CHECK errors" if suspicious else ""
                print(f"  {recipe['label']}: reduced_chi2="
                      f"{result.redchi:.2f} success={result.success}{flag}")
                for line in report_lines:
                    print(f"    {line}")
            session.commit()  # per pixel: an interruption loses nothing

    print(f"\n{fitted} fit(s) stored, {failed} failed, "
          f"{skipped} pixel(s) skipped")


if __name__ == "__main__":
    main()
