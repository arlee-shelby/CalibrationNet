"""Assign sources to run_pixels, one run SEGMENT at a time (a segment is
a period of constant source position — see calibrationnet.models.
RunSegment). For each segment the rigid source frame is placed by the
single translation that best explains the excess counts under all its
slots at once, primed by the position readback in that segment's own
convention; each slot then snaps to a distinct cluster and claims the
pixels within one ring.

Workflow:
  1. python scripts/assign_sources.py        # writes the review CSV
     (refuses to overwrite a CSV containing manual edits; --force to
     discard them)
  2. review/edit source_assignment_review.csv:
       - flag empty  -> looks right, will be applied
       - flag CHECK  -> uncertain; leave to skip it (stays NULL), or
         change to OK (apply as computed), or change to REDO and correct
         peak_pixel (membership recomputed around your peak; the pixels
         column is ignored)
  3. python scripts/assign_sources.py --apply   # reads the edited CSV,
     writes run_pixels.source_id; never regenerates the CSV
"""

import argparse
import csv
import math
import os

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.geometry import detector_pixel_position
from calibrationnet.models import (
    Run,
    RunPixel,
    RunSegment,
    Source,
    SourceInstallation,
)
from calibrationnet.source_assignment import (
    MEMBER_RADIUS,
    VERIFY_RADIUS,
    assign_from_preds,
    check_anchor_installation,
    compute_baselines,
    excess_map,
    fetch_all_counts,
    field_key,
    installation_for,
    locate_all_frames,
    slot_offsets,
    support_at,
)

REVIEW_CSV = "source_assignment_review.csv"


def default_csv_path(runs):
    """Per-run review CSVs live in assignments/ (git-tracked: review
    flags are human provenance — docs/pipeline_usage.md). The DATABASE
    is the master record of applied claims; these files are the review
    trail, never merged into one another."""
    if not runs:
        return REVIEW_CSV
    import os
    os.makedirs("assignments", exist_ok=True)
    tag = "+".join(str(r) for r in sorted(set(runs)))
    return f"assignments/review_run_{tag}.csv"
FLAG_DIST = 0.95      # verification moved almost a full radius -> check
MIN_EXCESS = 2.0      # peak must be at least 2x its own baseline


def has_manual_edits(path: str) -> bool:
    if not os.path.exists(path):
        return False
    return any(r["flag"].strip().upper() not in ("", "CHECK")
               for r in csv.DictReader(open(path)))


def generate_review(label: str, force: bool, runs=None,
                    path: str = REVIEW_CSV) -> None:
    if not force and has_manual_edits(path):
        raise SystemExit(
            f"{path} contains manual edits (OK/REDO flags). "
            "Apply them first (--apply) or pass --force to discard."
        )

    counts = fetch_all_counts(label)

    with get_session() as session:
        segments = {
            (seg.run_number, seg.segment_index): seg
            for seg in session.scalars(select(RunSegment))
        }
        missing = sorted(k for k in counts if k not in segments)
        if missing:
            raise SystemExit(f"filter outputs exist for segments not in the "
                             f"database: {missing[:5]}")
        key_positions = {
            k: (segments[k].linear_position, segments[k].horizontal_position)
            for k in counts
        }
        conventions = {k: segments[k].position_convention for k in counts}
        slot_maps, holders, installations = {}, {}, {}
        for k in counts:
            slot_maps[k], holders[k], installations[k] = installation_for(
                session, segments[k])
        # Field configuration per segment: pools must never mix magnet/
        # ExB epochs (the readback->frame mapping depends on them).
        run_rows = {r.run_number: r for r in session.scalars(
            select(Run).where(Run.run_number.in_(
                {k[0] for k in counts})))}
        fields = {k: field_key(run_rows[k[0]]) for k in counts}
        # Every pool's anchor must come from the pool's own installation
        # (a re-mounted tray can sit differently) — checked up front so
        # a missing re-anchor fails loudly before any placement runs.
        for inst, holder, conv in sorted({(installations[k], holders[k],
                                           conventions[k]) for k in counts
                                          if holders[k] is not None}):
            check_anchor_installation(session, holder, conv, inst)

    unplaceable = sorted(k for k in counts
                         if key_positions[k][0] is None
                         or key_positions[k][1] is None)
    if unplaceable:
        print(f"note: {len(unplaceable)} segment(s) have no recorded position "
              f"and are skipped: {unplaceable[:5]}")

    keys = [k for k in sorted(counts) if k not in unplaceable]
    # Baselines are per (convention, field), NOT across the whole
    # campaign: a pixel's intrinsic rate depends on the bias/threshold/
    # field conditions of its epoch, so mixing epochs distorts every
    # excess. (Mixing the 2025 and 2026 data inflated one verified
    # source pixel's baseline nearly 8-fold, which was enough to pull
    # frames a whole pixel row off; the 110 A and 137 A epochs of 2026
    # are likewise kept apart.)
    # Baselines pool per (installation, holder, convention, field) —
    # the SAME spec frames and trends group by. The installation must
    # be in the key: two mountings can share everything else (two trays
    # at one convention+field — first seen 2026-08-10, the 5-slot
    # reinstall alongside the 6-slot runs 9402+ — or one tray
    # re-mounted later), and mixing their rasters shifts every pixel's
    # median enough to move already-reviewed placements.
    def pool_of(k):
        return (installations[k], holders[k], conventions[k], fields[k])

    baselines = {}
    for pool in set(pool_of(k) for k in keys):
        subset = {k: counts[k] for k in keys if pool_of(k) == pool}
        baselines[pool] = compute_baselines(subset)
        print(f"baselines for {pool[1]} {pool[2]} at {pool[3]} "
              f"(installation {pool[0]}): {len(subset)} segment(s)")
    excesses = {k: {det: excess_map(counts[k][det],
                                    baselines[pool_of(k)][det])
                    for det in ("upper", "lower")}
                for k in keys}
    # Slot offsets are a property of the physical frame, so they only
    # depend on the convention's anchor, not on the segment — but the
    # offsets dict is keyed by the full pool spec (incl. installation
    # and field) because frame location and trends group by it.
    offsets = {}
    for k in keys:
        spec = pool_of(k)
        for det in ("upper", "lower"):
            if spec + (det,) not in offsets:
                offsets[spec + (det,)] = slot_offsets(
                    holders[k], conventions[k], det, slot_maps[k])

    frames, _trends = locate_all_frames(
        excesses, key_positions, conventions, holders, offsets,
        fields=fields, installations=installations)

    review_rows = []
    flagged = 0
    # --runs scopes the OUTPUT only: baselines, frames, and trends
    # above always use all pooled data, so per-run placements are
    # identical to the global mode's.
    write_keys = [k for k in keys if runs is None or k[0] in set(runs)]
    for k in write_keys:
        run_number, segment_index = k
        for det in ("upper", "lower"):
            labels = {slot: lab for slot, (sid, lab) in slot_maps[k].items()}
            tx, ty = frames[(k, det)]
            slot_offset = offsets[pool_of(k) + (det,)]
            preds = {slot: (tx + slot_offset[slot][0],
                            ty + slot_offset[slot][1]) for slot in labels}
            excess = excesses[k][det]
            for (slot, label_, pred, peak, dist, members) in (
                    assign_from_preds(excess, det, labels, preds,
                                      radius=VERIFY_RADIUS)):
                offset = 1000 if det == "lower" else 0
                peak_excess = excess.get(peak) if peak else None
                n_support = support_at(peak, det, excess) if peak else 0
                flag = (dist is None or dist > FLAG_DIST
                        or (peak_excess or 0) < MIN_EXCESS
                        or n_support < 2)
                flagged += flag
                review_rows.append({
                    "run": run_number, "segment": segment_index,
                    "field": fields[k],
                    "detector": det, "slot": slot, "source": label_,
                    "pred_x": round(pred[0], 2),
                    "pred_y": round(pred[1], 2),
                    "peak_pixel": peak + offset if peak else "",
                    "snap_dist": round(dist, 2) if dist is not None else "",
                    "peak_excess": round(peak_excess, 1)
                                   if peak_excess else "",
                    "peak_counts": counts[k][det].get(peak, "")
                                   if peak else "",
                    "n_support": n_support,
                    "n_pixels": len(members),
                    "pixels": ";".join(str(p + offset)
                                       for p in sorted(members)),
                    "flag": "CHECK" if flag else "",
                })

    if not review_rows:
        raise SystemExit(f"no placements to write for runs {runs} — are "
                         "those runs ingested with filter outputs at "
                         f"label {label!r}?")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(review_rows[0]))
        writer.writeheader()
        writer.writerows(review_rows)
    print(f"{len(review_rows)} slot placements over {len(write_keys)} "
          f"segments -> {path}; {flagged} flagged CHECK")


def apply_from_csv(label: str, path: str = REVIEW_CSV) -> None:
    """Apply the reviewed CSV: empty/OK rows as computed, REDO rows
    recomputed around the hand-corrected peak_pixel, CHECK rows skipped.
    Membership is recomputed jointly per segment-detector so REDO
    corrections claim pixels consistently."""
    from collections import defaultdict

    counts = fetch_all_counts(label)
    rows = list(csv.DictReader(open(path)))
    groups = defaultdict(list)
    skipped = 0
    for r in rows:
        flag = r["flag"].strip().upper()
        if flag in ("", "OK", "REDO"):
            if not r["peak_pixel"]:
                if flag == "REDO":
                    raise SystemExit(f"REDO row without peak_pixel: {r}")
                skipped += 1  # nothing was found; nothing to apply
                continue
            groups[(int(r["run"]), int(r["segment"]),
                    r["detector"])].append(r)
        elif flag == "CHECK":
            skipped += 1
        else:
            raise SystemExit(f"Unknown flag {r['flag']!r} in row: {r}")

    assignments = {}
    for (run_number, segment_index, det), group in groups.items():
        peaks = [int(r["peak_pixel"]) for r in group]
        if len(peaks) != len(set(peaks)):
            raise SystemExit(f"Duplicate peak_pixel in run {run_number} "
                             f"segment {segment_index} {det}: "
                             f"{sorted(peaks)}")
        cmap = counts[(run_number, segment_index)][det]
        offset = 1000 if det == "lower" else 0
        centers = []
        for r in group:
            px, py = detector_pixel_position(int(r["peak_pixel"]), det)
            ring = {
                p: c for p, c in cmap.items()
                if math.hypot(detector_pixel_position(p, det)[0] - px,
                              detector_pixel_position(p, det)[1] - py)
                <= MEMBER_RADIUS
            }
            total = sum(ring.values()) or 1
            centers.append((
                r["source"],
                (sum(detector_pixel_position(p, det)[0] * c
                     for p, c in ring.items()) / total,
                 sum(detector_pixel_position(p, det)[1] * c
                     for p, c in ring.items()) / total),
            ))
        for p in cmap:
            xx, yy = detector_pixel_position(p, det)
            best = None
            for source_label, (cx, cy) in centers:
                d = math.hypot(xx - cx, yy - cy)
                if d <= MEMBER_RADIUS and (best is None or d < best[1]):
                    best = (source_label, d)
            if best:
                assignments[(run_number, segment_index,
                             p + offset)] = best[0]

    # Scope the apply to the runs the CSV actually covers: a per-run
    # CSV must never NULL other runs' claims (AS design 2026-08-24).
    csv_runs = {int(r["run"]) for r in rows}
    with get_session() as session:
        source_ids = {s.label: s.id for s in session.scalars(select(Source))}
        updated = 0
        for (run_number, segment_index) in sorted(counts):
            if run_number not in csv_runs:
                continue
            rps = session.scalars(
                select(RunPixel).where(
                    RunPixel.run_number == run_number,
                    RunPixel.segment_index == segment_index)
            ).all()
            for rp in rps:
                label_ = assignments.get(
                    (run_number, segment_index, rp.pixel_number))
                rp.source_id = source_ids[label_] if label_ else None
                updated += label_ is not None
        session.commit()
    print(f"applied {updated} run_pixel source assignments "
          f"({skipped} rows skipped)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="apply the (possibly hand-edited) review CSV")
    parser.add_argument("--force", action="store_true",
                        help="overwrite a review CSV that has manual edits")
    parser.add_argument("--label", default="nabpy-standard")
    parser.add_argument("--runs", type=int, nargs="+", default=None,
                        help="write (and apply) placements for these "
                             "runs ONLY, to a per-run CSV in "
                             "assignments/ (git-tracked review "
                             "records). Baselines/frames/trends are "
                             "still computed from ALL data — identical "
                             "placements to a global run, scoped "
                             "output. Applying a per-run CSV touches "
                             "ONLY its runs' claims. Without --runs: "
                             "the historical global mode "
                             "(source_assignment_review.csv).")
    parser.add_argument("--path", default=None,
                        help="review CSV path (default: per --runs "
                             "convention, else the global CSV)")
    args = parser.parse_args()

    path = args.path or default_csv_path(args.runs)
    if args.apply:
        apply_from_csv(args.label, path)
    else:
        generate_review(args.label, args.force, runs=args.runs, path=path)


if __name__ == "__main__":
    main()
