"""Redraw QA figures for STORED fits — no refitting.

Skip-frozen means a re-run of an already-calibrated run never re-fits
(and so never re-plots) its good fits: fresh figures only appear for
pixels that actually enter the ladder. This script renders the figure
for every fit already in spectrum_fits — the frozen model evaluated
at the stored parameters over the stored window, drawn by the same
save_fit_figure() code path fit_spectra.py uses, so the output is
identical to the fit-time figure (plus a "stored" tag in the legend).

    python scripts/plot_stored_fits.py --run 8622
    python scripts/plot_stored_fits.py --run 8622 --segment 0 \
        --detector ldet --pixels 1048 1052

Figures land in fit_plots/run_<run>/ (git-ignored) by default, named
exactly like fit_spectra.py's: Run<r>_seg<s>_pix<p>_<recipe>.png.
"""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from lmfit import Parameters
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.fitting import save_fit_figure
from calibrationnet.models import RunPixel, SpectrumFit, TrapFilterOutput


def stored_params(fit: SpectrumFit) -> Parameters:
    """The frozen model's Parameters at the stored values — the same
    reconstruction calibrationnet.queries.stored_fit_curve uses."""
    params = Parameters()
    if "num_peaks" not in (fit.pars or {}):
        params.add("num_peaks", value=fit.n_peaks, vary=False)
    for name, value in fit.pars.items():
        params.add(name, value=value, vary=False)
    return params


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=None,
                        help="one segment (default: every segment "
                             "with stored fits)")
    parser.add_argument("--tf-label", default="nabpy-standard")
    parser.add_argument("--pixels", type=int, nargs="+", default=None)
    parser.add_argument("--detector", choices=("udet", "ldet"),
                        default=None,
                        help="only this detector (udet = pixels "
                             "< 1000, ldet = pixels >= 1000)")
    parser.add_argument("--out", type=Path, default=None, metavar="DIR",
                        help="figure directory (default: "
                             "fit_plots/run_<run>/)")
    args = parser.parse_args()
    out = args.out or Path("fit_plots") / f"run_{args.run}"
    out.mkdir(parents=True, exist_ok=True)

    query = (
        select(SpectrumFit, RunPixel)
        .join(TrapFilterOutput,
              SpectrumFit.trap_filter_output_id == TrapFilterOutput.id)
        .join(RunPixel, TrapFilterOutput.run_pixel_id == RunPixel.id)
        .where(RunPixel.run_number == args.run,
               TrapFilterOutput.label == args.tf_label)
        .order_by(RunPixel.segment_index, RunPixel.pixel_number)
    )
    if args.segment is not None:
        query = query.where(RunPixel.segment_index == args.segment)
    if args.pixels:
        query = query.where(RunPixel.pixel_number.in_(args.pixels))
    if args.detector == "udet":
        query = query.where(RunPixel.pixel_number < 1000)
    elif args.detector == "ldet":
        query = query.where(RunPixel.pixel_number >= 1000)

    drawn = 0
    with get_session() as session:
        for fit, rp in session.execute(query).all():
            data = np.asarray(fit.trap_filter_output.energies)
            shim = SimpleNamespace(params=stored_params(fit),
                                   redchi=fit.reduced_chi2)
            path = out / (f"Run{args.run}_seg{rp.segment_index}"
                          f"_pix{rp.pixel_number}_{fit.label}.png")
            save_fit_figure(data, (fit.fit_range_low, fit.fit_range_high),
                            shim, path, note="stored")
            drawn += 1
    if drawn == 0:
        raise SystemExit(f"no stored fits for run {args.run} at "
                         f"{args.tf_label!r} (with the given filters) — "
                         "queries.fit_overview() lists what exists")
    print(f"{drawn} figure(s) redrawn from stored fits -> {out}")


if __name__ == "__main__":
    main()
