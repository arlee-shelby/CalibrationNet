"""For every pixel: the stage readback that would center a source on it.

This inverts the same readback -> frame-position trend that source
assignment fits from the scan data (calibrationnet.pipeline.
source_assignment.locate_all_frames), so it improves automatically as
more scanned segments are ingested. That is the point of the rastered
grid runs: they were taken to LEARN this mapping, not to sit on pixel
centers themselves.

For each detector and each slot of the holder, solves

    trend(linear, horizontal) + slot_offset = pixel_center

for (linear, horizontal). Positions outside the scanned readback range
are extrapolations and are marked as such — trust them less.

    python scripts/optimal_positions.py                    # current holder
    python scripts/optimal_positions.py --pixels 63 1063   # just these
"""

import argparse
import csv

import numpy as np
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.geometry import physical_position
from calibrationnet.models import RunSegment
from calibrationnet.pipeline.source_assignment import (
    compute_baselines,
    excess_map,
    fetch_all_counts,
    installation_for,
    locate_all_frames,
    slot_offsets,
)

OUT_TEMPLATE = "optimal_positions_{holder}_{convention}.csv"


def invert_trend(trend_det, target) -> tuple:
    """Solve trend(linear, horizontal) = target for the readback."""
    cx, cy = trend_det
    matrix = np.array([[cx[0], cx[1]], [cy[0], cy[1]]])
    rhs = np.array([target[0] - cx[2], target[1] - cy[2]])
    return tuple(np.linalg.solve(matrix, rhs))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="nabpy-standard")
    parser.add_argument("--holder", default=None,
                        help="which tray (default: the current installation)")
    parser.add_argument("--pixels", type=int, nargs="*",
                        help="limit output to these pixel numbers")
    args = parser.parse_args()

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

    # Which (holder, convention) to invert: the requested holder's spec
    # with the most scanned segments.
    specs = {}
    for k in keys:
        specs.setdefault((holders[k], conventions[k]), []).append(k)
    candidates = {s: ks for s, ks in specs.items()
                  if args.holder is None or s[0] == args.holder}
    if not candidates:
        raise SystemExit(f"no scanned segments for holder {args.holder!r}; "
                         f"have {sorted(specs)}")
    spec = max(candidates, key=lambda s: len(candidates[s]))
    holder, convention = spec
    spec_keys = candidates[spec]
    print(f"inverting {holder} under {convention} "
          f"({len(spec_keys)} scanned segments)")

    # Same evidence preparation as assignment, restricted to this spec so
    # baselines stay within one bias/threshold epoch.
    subset = {k: counts[k] for k in spec_keys}
    baselines = compute_baselines(subset)
    excesses = {k: {det: excess_map(counts[k][det], baselines[det])
                    for det in ("upper", "lower")} for k in spec_keys}
    offsets = {}
    for det in ("upper", "lower"):
        offsets[spec + (det,)] = slot_offsets(
            holder, convention, det, slot_maps[spec_keys[0]])
    _frames, trends = locate_all_frames(
        excesses, key_positions, conventions, holders, offsets)
    trend = trends[spec]

    for det in ("upper", "lower"):
        cx, cy = trend[det]
        print(f"  {det}: d(x)/d(linear) = {cx[0]:+.2f} hex/inch, "
              f"d(y)/d(horizontal) = {cy[1]:+.2f} hex/inch "
              f"(cross terms {cx[1]:+.2f}, {cy[0]:+.2f})")

    lin_range = (min(key_positions[k][0] for k in spec_keys),
                 max(key_positions[k][0] for k in spec_keys))
    hor_range = (min(key_positions[k][1] for k in spec_keys),
                 max(key_positions[k][1] for k in spec_keys))
    print(f"  scanned range: linear {lin_range[0]:.2f}..{lin_range[1]:.2f}, "
          f"horizontal {hor_range[0]:+.2f}..{hor_range[1]:+.2f}")

    sources = {slot: label
               for slot, (_, label) in slot_maps[spec_keys[0]].items()}
    out_path = OUT_TEMPLATE.format(holder=holder, convention=convention)
    rows = []
    for det in ("upper", "lower"):
        base = 1000 if det == "lower" else 0
        for pixel in range(1, 128):
            stored = pixel + base
            if args.pixels and stored not in args.pixels:
                continue
            target = np.array(physical_position(stored, det))
            for slot, label in sorted(sources.items()):
                frame_target = target - np.array(offsets[spec + (det,)][slot])
                linear, horizontal = invert_trend(trend[det], frame_target)
                in_range = (lin_range[0] - 0.1 <= linear <= lin_range[1] + 0.1
                            and hor_range[0] - 0.1 <= horizontal
                            <= hor_range[1] + 0.1)
                rows.append({
                    "detector": det, "pixel_number": stored,
                    "slot": slot, "source": label,
                    "linear_position": round(linear, 3),
                    "horizontal_position": round(horizontal, 3),
                    "within_scanned_range": in_range,
                })

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    reachable = sum(r["within_scanned_range"] for r in rows)
    print(f"wrote {out_path}: {len(rows)} (pixel, slot) positions, "
          f"{reachable} within the scanned range")


if __name__ == "__main__":
    main()
