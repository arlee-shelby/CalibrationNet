"""Draw detector hit maps straight from filter-output CSVs — the
offline counterpart of scripts/show_hitmap.py, for seeing where the
sources actually sat with no database anywhere.

Counts are simply waveforms per pixel (CSV rows), drawn with the same
shared hex-map code the database version uses, so the maps are
directly comparable. One figure per detector per CSV.

    python scripts/offline/show_hitmap.py offline_output/filter/Run9409_seg0_*.csv
    python scripts/offline/show_hitmap.py offline_output/filter          # all of them
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

from calibrationnet.hitmap import draw
from calibrationnet.pipeline.trap_filter import parse_filter_filename


def count_pixels(path):
    """{stored pixel number: waveform count} — just row counts, the
    energies themselves are not parsed."""
    counts = Counter()
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            counts[int(row["pixel"])] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+",
                        help="filter-output CSV file(s) and/or folder(s)")
    parser.add_argument("--det", choices=["upper", "lower", "both"],
                        default="both")
    parser.add_argument("--vmax", type=float, default=None,
                        help="color scale cap (default: 93rd percentile, "
                             "like the database version)")
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/hitmaps"))
    args = parser.parse_args()

    files = []
    for item in args.inputs:
        p = Path(item)
        files += sorted(p.glob("*filter_output*.csv")) if p.is_dir() else [p]
    if not files:
        raise SystemExit("no filter-output CSVs found.")

    detectors = ["upper", "lower"] if args.det == "both" else [args.det]
    for path in files:
        meta = parse_filter_filename(path)
        run = meta.get("run_number", 0)
        segment = meta.get("segment_index", 0)
        counts = count_pixels(path)
        for det in detectors:
            base = 1000 if det == "lower" else 0
            det_counts = {p: c for p, c in counts.items()
                          if (p >= 1000) == (det == "lower")}
            if not det_counts:
                print(f"{path.name} {det}: no pixels")
                continue
            out = draw(run, segment, det, det_counts, args.out,
                       vmax=args.vmax)
            print(f"{path.name} {det}: {len(det_counts)} pixels -> {out}")


if __name__ == "__main__":
    main()
