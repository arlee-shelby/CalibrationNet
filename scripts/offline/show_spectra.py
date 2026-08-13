"""Plot raw per-pixel spectra straight from a filter-output CSV — for
eyeballing what a pixel actually saw, before or instead of fitting
(no database, no fit code involved).

One figure per pixel: the full histogram plus a zoom panel (default:
the Bi-207 CE window). Use it whenever the fit results are confusing —
the spectrum says what the fitter was actually given.

    python scripts/offline/show_spectra.py <filter CSV> --pixels 12 13 49 \\
        --out offline_output/review/UDET_review
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from calibrationnet.pipeline.trap_filter import parse_filter_filename


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="filter-output CSV")
    parser.add_argument("--pixels", type=int, nargs="+", required=True,
                        help="stored pixel numbers (1-127 upper, "
                             "1001-1127 lower)")
    parser.add_argument("--zoom", type=int, nargs=2, default=(1200, 3300),
                        metavar=("LO", "HI"),
                        help="ADC range of the zoom panel (default: the "
                             "Bi-207 CE window 1200 3300)")
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/spectra"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    meta = parse_filter_filename(args.input)
    run = meta.get("run_number", 0)
    segment = meta.get("segment_index", 0)

    wanted = set(args.pixels)
    per_pixel = defaultdict(list)
    with open(args.input, newline="") as fh:
        for row in csv.DictReader(fh):
            pixel = int(row["pixel"])
            if pixel in wanted and row["energy"]:
                per_pixel[pixel].append(float(row["energy"]))

    for pixel in args.pixels:
        energies = np.asarray(per_pixel.get(pixel, []))
        if len(energies) == 0:
            print(f"pixel {pixel}: no waveforms in this CSV")
            continue
        hist, edges = np.histogram(energies, bins=np.arange(0, 4500))
        fig, (full, zoom) = plt.subplots(1, 2, figsize=(14, 5))
        full.stairs(hist, edges)
        full.set_title(f"full spectrum ({len(energies)} waveforms)")
        lo, hi = args.zoom
        zoom.stairs(hist[lo:hi], edges[lo:hi + 1])
        zoom.set_title(f"zoom {lo}..{hi} ADC "
                       f"({int(hist[lo:hi].sum())} counts)")
        for ax in (full, zoom):
            ax.set_xlabel("Energy (ADC)")
            ax.set_ylabel("Counts")
        fig.suptitle(f"Run {run} seg {segment} pixel {pixel} — raw data")
        out = args.out / f"Run{run}_seg{segment}_pix{pixel}_spectrum.png"
        fig.savefig(out, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"pixel {pixel}: {len(energies)} waveforms -> {out.name}")


if __name__ == "__main__":
    main()
