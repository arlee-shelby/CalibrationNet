"""A minimal source-position plan: the fewest stage positions that put a
source usefully over as many pixels as possible.

Fits the same readback -> frame-position trend that source assignment
learns from the scan data (so the plan improves automatically as more
scanned segments are ingested), then grid-searches the physically
allowed readback range — taken from the positions actually used in the
data, the only range known to be reachable — for positions where holder
SLOTS land near pixel centers. Slots are what matter, never the sources
sitting in them today: sources get swapped, the tray geometry doesn't.

Two thresholds grade each (position, pixel) pairing by the predicted
offset of the slot center from the pixel center (center-to-corner is
5.2 mm, the boundary to a neighboring pixel ~4.5 mm):

  --tolerance-mm (default 2.6)  "well centered" — comfortably inside
  --boundary-mm  (default 4.5)  "inside the pixel" — usable at all

The plan is built in two passes so no pixel falls through the cracks:
first the fewest positions that WELL-CENTER every pixel that can be
well-centered anywhere in the allowed range, then extra positions so
every remaining pixel at least gets the source inside it. Pixels still
uncovered are reported with their best achievable offset, so a white
pixel on the map is always explained, never silently dropped.

Outputs (all sharing one file stem):
  <stem>.csv            one row per position/detector/slot -> pixel
  <stem>_positions.csv  just the positions to visit, for automation
  <stem>_summary.txt    the printed plan + coverage summary
  <stem>_{det}.png      per-detector coverage map, colored by offset

    python scripts/optimal_positions.py                  # current holder
    python scripts/optimal_positions.py --holder 5-slot  # legacy tray
    python scripts/optimal_positions.py --tolerance-mm 1.5
    # what a wider horizontal scan would buy (for planning the scan):
    python scripts/optimal_positions.py --assume-horizontal -0.75 0.75

Full description: docs/position_planning.md.
"""

import argparse
import csv
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import RegularPolygon
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.geometry import (
    HEX_ORIENTATION,
    HEX_RADIUS,
    physical_position,
    pixel_positions,
    mirrored_x,
)
from calibrationnet.models import RunSegment, SourceInstallation
from calibrationnet.pipeline.source_assignment import (
    compute_baselines,
    excess_map,
    fetch_all_counts,
    installation_for,
    locate_all_frames,
    refine_slot_offsets,
    slot_offsets,
)

OUT_TEMPLATE = "optimal_positions_plan_{holder}_{convention}"
MM_PER_HEX = 5.2  # pixel center-to-corner is 5.2 mm = 1 geometry unit


def compress(numbers) -> str:
    """[1,2,3,7,8,12] -> '1-3,7-8,12'."""
    spans, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            spans.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ",".join(spans)


def candidate_coverage(trend, offsets, lins, hors, boundary_hex):
    """For every candidate readback position: which pixel each slot puts
    the source inside, per detector. Returns (cover, best_offset):
    cover[i] = {(det, slot): (pixel, offset_hex)} aligned with lins/hors;
    best_offset[det][pixel] = the smallest offset achievable ANYWHERE on
    the grid, whatever the thresholds — this is what explains uncovered
    pixels."""
    cover = [{} for _ in range(len(lins))]
    best_offset = {}
    for det in ("upper", "lower"):
        cx, cy = trend[det]
        frame_x = cx[0] * lins + cx[1] * hors + cx[2]
        frame_y = cy[0] * lins + cy[1] * hors + cy[2]
        base = 1000 if det == "lower" else 0
        stored = np.arange(1, 128) + base
        centers = np.array([physical_position(int(p), det) for p in stored])
        best = np.full(len(stored), np.inf)
        for slot, (ox, oy) in offsets[det].items():
            dist = np.hypot(
                (frame_x + ox)[:, None] - centers[None, :, 0],
                (frame_y + oy)[:, None] - centers[None, :, 1],
            )
            best = np.minimum(best, dist.min(axis=0))
            nearest = dist.argmin(axis=1)
            closest = dist[np.arange(len(lins)), nearest]
            for i in np.nonzero(closest <= boundary_hex)[0]:
                cover[i][(det, slot)] = (int(stored[nearest[i]]),
                                         float(closest[i]))
        best_offset[det] = {int(p): float(b) for p, b in zip(stored, best)}
    return cover, best_offset


def greedy_plan(cover, tol_hex, max_positions=None) -> list:
    """Fewest candidate indices so that every pixel that CAN be
    well-centered is (pass 1), and every other coverable pixel at least
    gets the source inside it (pass 2). Greedy: repeatedly take the
    candidate adding the most uncovered pixels, ties broken by better
    centering."""
    tight = [frozenset((det, pixel)
                       for (det, _), (pixel, off) in c.items()
                       if off <= tol_hex) for c in cover]
    loose = [frozenset((det, pixel) for (det, _), (pixel, _) in c.items())
             for c in cover]
    chosen, covered = [], set()
    for pixel_sets in (tight, loose):
        if pixel_sets is loose:
            # Positions already chosen visit their loosely-covered pixels
            # for free — only pixels no chosen position reaches need more.
            for i in chosen:
                covered |= loose[i]
        while max_positions is None or len(chosen) < max_positions:
            best, best_gain, best_offset = None, 0, None
            for i, pixels in enumerate(pixel_sets):
                gain = len(pixels - covered)
                if gain == 0:
                    continue
                offset = (sum(off for _, off in cover[i].values())
                          / len(cover[i]))
                if gain > best_gain or (gain == best_gain
                                        and offset < best_offset):
                    best, best_gain, best_offset = i, gain, offset
            if best is None:
                break
            chosen.append(best)
            covered |= pixel_sets[best]
    return chosen


def draw_coverage(det, assigned, tol_mm, boundary_mm, holder, convention,
                  n_positions, out_path):
    """Coverage map: each covered pixel colored by its predicted offset
    (bright = well centered) and labeled with the plan position that
    best centers it; uncovered pixels stay white."""
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    # Bright = small offset, matching "the pixel lights up" on the
    # standard hit maps; cividis is the collaboration's map and is
    # perceptually uniform / CVD-safe.
    cmap = plt.get_cmap("cividis_r")
    centered = sum(off * MM_PER_HEX <= tol_mm for _, off in assigned.values())

    for pixel_number, (x, y) in pixel_positions().items():
        stored = pixel_number + (1000 if det == "lower" else 0)
        if det == "lower":
            x = mirrored_x(x)  # facing detectors: same physical spot
        entry = assigned.get(stored)
        frac = entry[1] * MM_PER_HEX / boundary_mm if entry else None
        ax.add_patch(RegularPolygon(
            (x, y), numVertices=6, radius=HEX_RADIUS,
            orientation=HEX_ORIENTATION,
            facecolor=cmap(frac) if entry else "white",
            edgecolor="black",
        ))
        ink = "white" if entry and frac > 0.6 else "black"
        ax.text(x, y + 0.42, str(pixel_number), ha="center", va="center",
                fontsize=5, color=ink if entry else "grey")
        if entry:
            ax.text(x, y - 0.18, f"P{entry[0]}", ha="center", va="center",
                    fontsize=7, fontweight="bold", color=ink)

    ax.set_xlim(-13, 13)
    ax.set_ylim(-13, 13)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{holder} ({convention}) position plan — {det} detector\n"
                 f"{len(assigned)}/127 pixels get a position "
                 f"({centered} well-centered at <={tol_mm} mm) "
                 f"from {n_positions} positions (P#)")
    mappable = plt.cm.ScalarMappable(cmap=cmap,
                                     norm=plt.Normalize(0, boundary_mm))
    fig.colorbar(mappable, ax=ax, shrink=0.8,
                 label="predicted offset from pixel center (mm)")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="nabpy-standard")
    parser.add_argument("--holder", default=None,
                        help="which tray (default: the current installation)")
    parser.add_argument("--tolerance-mm", type=float, default=2.6,
                        help="predicted offset for 'well centered' "
                             "(default 2.6 mm = half the 5.2 mm "
                             "center-to-corner)")
    parser.add_argument("--boundary-mm", type=float, default=4.5,
                        help="max predicted offset for a pixel to count as "
                             "covered at all (default 4.5 mm — beyond that "
                             "the source center sits in a neighboring pixel)")
    parser.add_argument("--step", type=float, default=0.05,
                        help="readback grid step for the position search "
                             "(default 0.05, in each axis' own units)")
    parser.add_argument("--max-positions", type=int, default=None,
                        help="cap the plan at this many positions (the plan "
                             "is ordered by coverage gain, so this keeps "
                             "the most valuable ones)")
    parser.add_argument("--assume-linear", type=float, nargs=2,
                        metavar=("LO", "HI"), default=None,
                        help="WHAT-IF: pretend this linear range is "
                             "reachable, to plan the next scan; the trend "
                             "is extrapolated and outputs get a _whatif "
                             "suffix — never feed them to the automation")
    parser.add_argument("--assume-horizontal", type=float, nargs=2,
                        metavar=("LO", "HI"), default=None,
                        help="WHAT-IF: pretend this horizontal range is "
                             "reachable (see --assume-linear)")
    parser.add_argument("--no-refine", action="store_true",
                        help="use the raw anchor-derived slot offsets "
                             "instead of refining them against all "
                             "scanned segments")
    args = parser.parse_args()

    # Default to the tray that is physically installed right now
    # (removed_on IS NULL), not to whichever holder has the most scan
    # data — otherwise a well-scanned old tray silently wins.
    if args.holder is None:
        with get_session() as session:
            installed = sorted(set(session.scalars(
                select(SourceInstallation.holder)
                .where(SourceInstallation.removed_on.is_(None),
                       SourceInstallation.holder.is_not(None))
            )))
        if len(installed) == 1:
            args.holder = installed[0]
            print(f"current installation: {args.holder}")
        else:
            raise SystemExit(
                f"cannot determine the current holder (installations with "
                f"removed_on NULL name {installed or 'no'} holders) — pass "
                f"--holder explicitly."
            )

    counts = fetch_all_counts(args.label)
    with get_session() as session:
        segments = {(s.run_number, s.segment_index): s
                    for s in session.scalars(select(RunSegment))}
        meta = {k: installation_for(session, segments[k]) for k in counts}
        key_positions = {
            k: (segments[k].linear_position, segments[k].horizontal_position)
            for k in counts}
        conventions = {k: segments[k].position_convention for k in counts}

    holders = {k: meta[k][1] for k in counts}
    slot_maps = {k: meta[k][0] for k in counts}
    keys = [k for k in sorted(counts)
            if None not in key_positions[k] and holders[k] is not None]

    # Which (holder, convention) to plan for: the requested holder's spec
    # with the most scanned segments.
    specs = {}
    for k in keys:
        specs.setdefault((holders[k], conventions[k]), []).append(k)
    candidates = {s: ks for s, ks in specs.items() if s[0] == args.holder}
    if not candidates:
        raise SystemExit(f"no scanned segments for holder {args.holder!r}; "
                         f"have {sorted(specs)}")
    spec = max(candidates, key=lambda s: len(candidates[s]))
    holder, convention = spec
    spec_keys = candidates[spec]

    lines = []  # everything printed is also saved to <stem>_summary.txt

    def report(text=""):
        print(text)
        lines.append(text)

    report(f"planning {holder} under {convention} "
           f"({len(spec_keys)} scanned segments)")

    # Same evidence preparation as assignment, restricted to this spec so
    # baselines stay within one bias/threshold epoch.
    subset = {k: counts[k] for k in spec_keys}
    baselines = compute_baselines(subset)
    excesses = {k: {det: excess_map(counts[k][det], baselines[det])
                    for det in ("upper", "lower")} for k in spec_keys}
    offsets_by_det = {
        det: slot_offsets(holder, convention, det, slot_maps[spec_keys[0]])
        for det in ("upper", "lower")
    }

    # The anchor snaps each verified source to its pixel's CENTER, so the
    # raw inter-slot spacings carry up to a pixel of quantization error.
    # Refine them against every scanned segment (measured count
    # centroids), then relocate the frames with the corrected geometry.
    rounds = 0 if args.no_refine else 2
    for _ in range(rounds):
        offsets_for_locate = {spec + (det,): offsets_by_det[det]
                              for det in ("upper", "lower")}
        frames, trends = locate_all_frames(
            excesses, key_positions, conventions, holders,
            offsets_for_locate)
        offsets_by_det, refine_report = refine_slot_offsets(
            excesses, frames, offsets_by_det, spec_keys)
    offsets_for_locate = {spec + (det,): offsets_by_det[det]
                          for det in ("upper", "lower")}
    _frames, trends = locate_all_frames(
        excesses, key_positions, conventions, holders, offsets_for_locate)
    trend = trends[spec]

    if rounds:
        report("  slot offsets refined against the scanned segments "
               "(total correction vs anchor, hex):")
        raw = {det: slot_offsets(holder, convention, det,
                                 slot_maps[spec_keys[0]])
               for det in ("upper", "lower")}
        for det in ("upper", "lower"):
            parts = []
            for slot in sorted(offsets_by_det[det]):
                dx = offsets_by_det[det][slot][0] - raw[det][slot][0]
                dy = offsets_by_det[det][slot][1] - raw[det][slot][1]
                n = refine_report[det][slot][2]
                parts.append(f"{slot} ({dx:+.2f},{dy:+.2f},n={n})")
            report(f"    {det}: " + "  ".join(parts))

    for det in ("upper", "lower"):
        cx, cy = trend[det]
        report(f"  {det}: d(x)/d(linear) = {cx[0]:+.2f} hex/inch, "
               f"d(y)/d(horizontal) = {cy[1]:+.2f} hex/inch "
               f"(cross terms {cx[1]:+.2f}, {cy[0]:+.2f})")

    # The physically allowed readback range: exactly what the scan data
    # actually used — anything beyond it is not known to be reachable, so
    # the plan never proposes it.
    lin_lo = min(key_positions[k][0] for k in spec_keys)
    lin_hi = max(key_positions[k][0] for k in spec_keys)
    hor_lo = min(key_positions[k][1] for k in spec_keys)
    hor_hi = max(key_positions[k][1] for k in spec_keys)
    report(f"  allowed range (from the data): "
           f"linear {lin_lo:.2f}..{lin_hi:.2f}, "
           f"horizontal {hor_lo:+.2f}..{hor_hi:+.2f}")
    whatif = bool(args.assume_linear or args.assume_horizontal)
    if args.assume_linear:
        lin_lo, lin_hi = args.assume_linear
    if args.assume_horizontal:
        hor_lo, hor_hi = args.assume_horizontal
    if whatif:
        report(f"  WHAT-IF range (assumed, extrapolating the trend): "
               f"linear {lin_lo:.2f}..{lin_hi:.2f}, "
               f"horizontal {hor_lo:+.2f}..{hor_hi:+.2f} — for planning "
               f"the next scan only, NOT for the automation")

    lin_grid = np.arange(lin_lo, lin_hi + 1e-9, args.step)
    hor_grid = np.arange(hor_lo, hor_hi + 1e-9, args.step)
    lins, hors = (a.ravel() for a in np.meshgrid(lin_grid, hor_grid))
    tol_hex = args.tolerance_mm / MM_PER_HEX
    boundary_hex = args.boundary_mm / MM_PER_HEX

    cover, best_offset = candidate_coverage(
        trend, offsets_by_det, lins, hors, boundary_hex)
    chosen = greedy_plan(cover, tol_hex, args.max_positions)
    if not chosen:
        raise SystemExit(
            "no position in the allowed range puts a slot within "
            f"{args.boundary_mm} mm of any pixel — check the trend/data."
        )

    # Report + CSV: every pixel a position covers (not only newly-added
    # ones) — at that dwell they all take usable data simultaneously.
    report(f"\nplan: {len(chosen)} positions "
           f"(well-centered <= {args.tolerance_mm} mm, "
           f"covered <= {args.boundary_mm} mm, grid step {args.step})")
    csv_rows, assigned = [], {"upper": {}, "lower": {}}
    seen = {"upper": set(), "lower": set()}
    for n, i in enumerate(chosen, start=1):
        new = {det: [] for det in ("upper", "lower")}
        for (det, slot), (pixel, off) in sorted(cover[i].items()):
            if pixel not in seen[det]:
                seen[det].add(pixel)
                new[det].append(pixel)
            old = assigned[det].get(pixel)
            if old is None or off < old[1]:
                assigned[det][pixel] = (n, off)
            csv_rows.append({
                "position": n,
                "linear_position": round(float(lins[i]), 3),
                "horizontal_position": round(float(hors[i]), 3),
                "detector": det, "slot": slot, "pixel_number": pixel,
                "predicted_offset_mm": round(off * MM_PER_HEX, 2),
                "well_centered": off <= tol_hex,
            })
        report(f"  P{n}: linear={lins[i]:.2f} horizontal={hors[i]:+.2f}"
               f"  (+{len(new['upper'])} upper, +{len(new['lower'])} lower)")
        for det in ("upper", "lower"):
            parts = [f"{slot}->{pixel} ({off * MM_PER_HEX:.1f}mm)"
                     for (d, slot), (pixel, off) in sorted(cover[i].items())
                     if d == det]
            if parts:
                report(f"      {det}: " + "  ".join(parts))

    stem = OUT_TEMPLATE.format(holder=holder, convention=convention)
    if whatif:
        stem += "_whatif"
    with open(f"{stem}.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    with open(f"{stem}_positions.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["position", "linear_position",
                         "horizontal_position",
                         "pixels_upper", "pixels_lower"])
        for n, i in enumerate(chosen, start=1):
            writer.writerow([
                n, round(float(lins[i]), 3), round(float(hors[i]), 3),
                sum(1 for (d, _) in cover[i] if d == "upper"),
                sum(1 for (d, _) in cover[i] if d == "lower"),
            ])
    report(f"\nwrote {stem}.csv ({len(csv_rows)} rows) and "
           f"{stem}_positions.csv ({len(chosen)} positions)")

    report("coverage summary:")
    for det in ("upper", "lower"):
        centered = sum(off <= tol_hex for _, off in assigned[det].values())
        report(f"  {det}: {len(assigned[det])}/127 pixels get a position "
               f"({centered} well-centered at <={args.tolerance_mm} mm, "
               f"{len(assigned[det]) - centered} at "
               f"{args.tolerance_mm}-{args.boundary_mm} mm)")
        base = 1000 if det == "lower" else 0
        uncovered = [p for p in range(1, 128)
                     if p + base not in assigned[det]]
        if uncovered:
            detail = ", ".join(
                f"{p} (best {best_offset[det][p + base] * MM_PER_HEX:.1f}mm)"
                for p in uncovered)
            report(f"    no position within {args.boundary_mm} mm — "
                   f"best achievable offset shown: {detail}")
        draw_coverage(det, assigned[det], args.tolerance_mm,
                      args.boundary_mm, holder, convention, len(chosen),
                      f"{stem}_{det}.png")
        report(f"    map: {stem}_{det}.png")

    with open(f"{stem}_summary.txt", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"summary saved to {stem}_summary.txt")


if __name__ == "__main__":
    main()
