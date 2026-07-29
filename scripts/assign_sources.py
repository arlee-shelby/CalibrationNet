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
from calibrationnet.geometry import physical_position
from calibrationnet.models import (
    RunPixel,
    RunSegment,
    Source,
    SourceInstallation,
)
from calibrationnet.pipeline.source_assignment import (
    MEMBER_RADIUS,
    VERIFY_RADIUS,
    assign_from_preds,
    compute_baselines,
    excess_map,
    fetch_all_counts,
    fit_position_trend,
    locate_frame,
    predict_prior,
    predict_trend,
    slot_offsets,
    support_at,
)

REVIEW_CSV = "source_assignment_review.csv"
FLAG_DIST = 0.95      # verification moved almost a full radius -> check
MIN_EXCESS = 2.0      # peak must be at least 2x its own baseline


def slot_sources_for(session, segment: RunSegment) -> dict:
    """{slot: (source_id, source_label)} installed during this segment."""
    day = (segment.start_time or segment.run.start_time).date()
    rows = session.execute(
        select(SourceInstallation.slot, Source.id, Source.label)
        .join(Source, SourceInstallation.source_id == Source.id)
        .where(SourceInstallation.installed_on <= day)
        .where((SourceInstallation.removed_on.is_(None))
               | (SourceInstallation.removed_on > day))
    ).all()
    return {slot: (sid, label) for slot, sid, label in rows}


def has_manual_edits(path: str) -> bool:
    if not os.path.exists(path):
        return False
    return any(r["flag"].strip().upper() not in ("", "CHECK")
               for r in csv.DictReader(open(path)))


def generate_review(label: str, force: bool) -> None:
    if not force and has_manual_edits(REVIEW_CSV):
        raise SystemExit(
            f"{REVIEW_CSV} contains manual edits (OK/REDO flags). "
            "Apply them first (--apply) or pass --force to discard."
        )

    counts = fetch_all_counts(label)
    baselines = compute_baselines(counts)

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
        slot_maps = {k: slot_sources_for(session, segments[k]) for k in counts}

    unplaceable = sorted(k for k in counts
                         if key_positions[k][0] is None
                         or key_positions[k][1] is None)
    if unplaceable:
        print(f"note: {len(unplaceable)} segment(s) have no recorded position "
              f"and are skipped: {unplaceable[:5]}")

    keys = [k for k in sorted(counts) if k not in unplaceable]
    excesses = {k: {det: excess_map(counts[k][det], baselines[det])
                    for det in ("upper", "lower")}
                for k in keys}
    # Slot offsets are a property of the physical frame, so they only
    # depend on the convention's anchor, not on the segment.
    offsets = {}
    for k in keys:
        convention = conventions[k]
        for det in ("upper", "lower"):
            if (convention, det) not in offsets:
                offsets[(convention, det)] = slot_offsets(
                    convention, det, slot_maps[k])

    # Round 1: locate each segment's frame from a readback-based prior.
    # Round 2: refit the readback -> frame-position trend across all
    # segments of a convention, then relocate with that tighter prior.
    located = {}
    for k in keys:
        for det in ("upper", "lower"):
            prior = predict_prior(conventions[k], det,
                                  offsets[(conventions[k], det)],
                                  *key_positions[k])
            located[(k, det)] = locate_frame(
                excesses[k][det], det, offsets[(conventions[k], det)], prior)

    frames = {}
    for convention in set(conventions.values()):
        conv_keys = [k for k in keys if conventions[k] == convention]
        by_det = {det: {k: located[(k, det)] for k in conv_keys}
                  for det in ("upper", "lower")}
        trend = fit_position_trend(by_det, key_positions)
        for k in conv_keys:
            for det in ("upper", "lower"):
                prior = (predict_trend(trend, det, *key_positions[k])
                         or located[(k, det)])
                frames[(k, det)] = locate_frame(
                    excesses[k][det], det, offsets[(convention, det)], prior,
                    window=(2.0, 1.5), sigma=1.0)

    review_rows = []
    flagged = 0
    for k in keys:
        run_number, segment_index = k
        for det in ("upper", "lower"):
            labels = {slot: lab for slot, (sid, lab) in slot_maps[k].items()}
            tx, ty = frames[(k, det)]
            slot_offset = offsets[(conventions[k], det)]
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

    with open(REVIEW_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(review_rows[0]))
        writer.writeheader()
        writer.writerows(review_rows)
    print(f"{len(review_rows)} slot placements over {len(keys)} segments "
          f"-> {REVIEW_CSV}; {flagged} flagged CHECK")


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
            px, py = physical_position(int(r["peak_pixel"]), det)
            ring = {
                p: c for p, c in cmap.items()
                if math.hypot(physical_position(p, det)[0] - px,
                              physical_position(p, det)[1] - py)
                <= MEMBER_RADIUS
            }
            total = sum(ring.values()) or 1
            centers.append((
                r["source"],
                (sum(physical_position(p, det)[0] * c
                     for p, c in ring.items()) / total,
                 sum(physical_position(p, det)[1] * c
                     for p, c in ring.items()) / total),
            ))
        for p in cmap:
            xx, yy = physical_position(p, det)
            best = None
            for source_label, (cx, cy) in centers:
                d = math.hypot(xx - cx, yy - cy)
                if d <= MEMBER_RADIUS and (best is None or d < best[1]):
                    best = (source_label, d)
            if best:
                assignments[(run_number, segment_index,
                             p + offset)] = best[0]

    with get_session() as session:
        source_ids = {s.label: s.id for s in session.scalars(select(Source))}
        updated = 0
        for (run_number, segment_index) in sorted(counts):
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
    args = parser.parse_args()

    if args.apply:
        apply_from_csv(args.label)
    else:
        generate_review(args.label, args.force)


if __name__ == "__main__":
    main()
