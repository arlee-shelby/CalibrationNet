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
from calibrationnet.geometry import (
    HEX_ORIENTATION,
    HEX_RADIUS,
    mirrored_x,
    pixel_positions,
)
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


def draw(run_number: int, segment_index: int, detector: str, counts: dict,
         out_dir: Path, vmax: float = None) -> Path:
    # Geometry, hexagon parameters, and styling follow nabPy's
    # nabPlot.detectorFigure (size=1, cividis) so these maps look like the
    # collaboration's standard hit maps.
    positions = pixel_positions()
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    if vmax is None:
        # Cap the scale below the hottest (e.g. 109Cd) pixels so the rest
        # of the map isn't crushed — like nabPy's forceMax.
        ordered = sorted(counts.values())
        vmax = ordered[int(0.93 * (len(ordered) - 1))] or 1
    cmap = plt.get_cmap("cividis")

    for pixel_number, (x, y) in positions.items():
        stored = pixel_number + (1000 if detector == "lower" else 0)
        if detector == "lower":
            # The detectors face each other, so the lower detector is
            # drawn mirrored across the vertical center line (pixel 1001
            # sits where upper pixel 121 is): both maps then show the
            # same physical location at the same spot.
            x = mirrored_x(x)
        count = counts.get(stored)
        color = cmap(min(count, vmax) / vmax) if count is not None else "white"
        ax.add_patch(RegularPolygon(
            (x, y), numVertices=6, radius=HEX_RADIUS,
            orientation=HEX_ORIENTATION,
            facecolor=color, edgecolor="black",
        ))
        ax.text(x, y, str(pixel_number), ha="center", va="center",
                fontsize=7,
                color="white" if count and count / vmax > 0.5 else "black")

    ax.set_xlim(-13, 13)
    ax.set_ylim(-13, 13)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"Run {run_number} segment {segment_index} — {detector} "
                 f"detector (waveforms per pixel, capped at {int(vmax)})")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, vmax))
    fig.colorbar(sm, ax=ax, shrink=0.8)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (f"Run{run_number}_seg{segment_index}_"
                     f"{detector}_hitmap.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


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
