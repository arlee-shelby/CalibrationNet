"""Draw a detector hit map for a run from the database: waveform counts
per pixel from the stored trap filter outputs (array lengths only — the
energy arrays themselves are not pulled).

    python scripts/show_hitmap.py 8622 --det lower
    python scripts/show_hitmap.py 9369 --segment 12 --det upper
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import RegularPolygon
from sqlalchemy import func, select

from calibrationnet.db import get_session
from calibrationnet.hitmap import draw
from calibrationnet.models import Pixel, RunPixel, TrapFilterOutput


def fetch_counts(run_number: int, segment_index: int, detector: str,
                 label: str) -> dict:
    """{pixel_number: waveform count} for one run segment / detector."""
    with get_session() as session:
        rows = session.execute(
            select(Pixel.pixel_number,
                   func.array_length(TrapFilterOutput.energies, 1))
            .join(TrapFilterOutput.run_pixel)
            .join(RunPixel.pixel)
            .where(RunPixel.run_number == run_number,
                   RunPixel.segment_index == segment_index,
                   Pixel.detector == detector,
                   TrapFilterOutput.label == label)
        ).all()
    return {p: (c or 0) for p, c in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_number", type=int)
    parser.add_argument("--det", choices=["upper", "lower"], default="upper")
    parser.add_argument("--segment", type=int, default=0,
                        help="run segment index (default 0)")
    parser.add_argument("--label", default="nabpy-standard")
    parser.add_argument("--out", type=Path, default=Path("hitmaps"))
    parser.add_argument("--vmax", type=float, default=None,
                        help="color scale cap (default: 93rd percentile)")
    args = parser.parse_args()

    counts = fetch_counts(args.run_number, args.segment, args.det, args.label)
    if not counts:
        raise SystemExit(f"No '{args.label}' filter outputs stored for run "
                         f"{args.run_number} segment {args.segment} "
                         f"({args.det}).")
    out = draw(args.run_number, args.segment, args.det, counts, args.out,
               args.vmax)
    print(f"wrote {out} ({len(counts)} pixels)")


if __name__ == "__main__":
    main()
