"""Assign sources to run_pixels: for every run, anchor the source frame
to that run's measured 109Cd cluster, place the other slots with the
rigid frame vectors from the confirmed run-8622 anchors, snap each slot
to its local count maximum, and claim pixels within one ring.

Review first (writes source_assignment_review.csv, no DB changes);
apply with --apply after checking it.

    python scripts/assign_sources.py            # review only
    python scripts/assign_sources.py --apply
"""

import argparse
import csv

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import (
    Pixel,
    Run,
    RunPixel,
    Source,
    SourceInstallation,
)
from calibrationnet.pipeline.source_assignment import (
    assign_detector,
    build_frames,
    fetch_all_counts,
)

FLAG_DIST = 1.8  # snap moved more than ~1 pixel pitch -> review by eye


def slot_sources_for(session, run: Run) -> dict:
    """{slot: (source_id, source_label)} active at the run's start."""
    day = run.start_time.date()
    rows = session.execute(
        select(SourceInstallation.slot, Source.id, Source.label)
        .join(Source, SourceInstallation.source_id == Source.id)
        .where(SourceInstallation.installed_on <= day)
        .where((SourceInstallation.removed_on.is_(None))
               | (SourceInstallation.removed_on > day))
    ).all()
    return {slot: (sid, label) for slot, sid, label in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write run_pixels.source_id after review")
    parser.add_argument("--include-flagged", action="store_true",
                        help="also apply CHECK-flagged placements "
                             "(default: flagged pixels stay NULL)")
    parser.add_argument("--label", default="nabpy-standard")
    args = parser.parse_args()

    counts = fetch_all_counts(args.label)
    frames = build_frames(counts)

    # Pass 1: measure each slot's systematic prediction error (median
    # pred -> snapped-centroid offset per detector+slot across all runs)
    # so pass 2 corrects for frame distortion/parallax before snapping.
    from statistics import median
    from calibrationnet.pipeline.source_assignment import (
        find_clusters, predict_slot, snap_to_cluster)
    residuals = {}
    with get_session() as session0:
        runs0 = {r.run_number: r for r in session0.scalars(select(Run))}
        for run_number in sorted(counts):
            slot_map0 = slot_sources_for(session0, runs0[run_number])
            for det in ("upper", "lower"):
                cmap = counts[run_number][det]
                cd = find_clusters(cmap, det, 1)[0]["centroid"]
                for slot in slot_map0:
                    pred = predict_slot(frames[det], slot, cd)
                    snapped = snap_to_cluster(pred, cmap, det)
                    if snapped:
                        _, cen, _ = snapped
                        residuals.setdefault((det, slot), []).append(
                            (cen[0] - pred[0], cen[1] - pred[1]))
    slot_offsets = {
        key: (median(dx for dx, _ in v), median(dy for _, dy in v))
        for key, v in residuals.items()
    }

    review_rows = []
    flagged = 0
    assignments = {}  # (run_number, stored_pixel) -> source_id

    with get_session() as session:
        runs = {r.run_number: r for r in session.scalars(select(Run))}
        for run_number in sorted(counts):
            slot_map = slot_sources_for(session, runs[run_number])
            for det in ("upper", "lower"):
                labels = {s: lab for s, (sid, lab) in slot_map.items()}
                ids = {lab: sid for s, (sid, lab) in slot_map.items()}
                offsets = {s2: o for (d2, s2), o in slot_offsets.items()
                           if d2 == det}
                for (slot, label, pred, peak, dist, members) in (
                        assign_detector(counts[run_number][det], det,
                                        labels, frames[det], offsets)):
                    offset = 1000 if det == "lower" else 0
                    flag = dist is None or dist > FLAG_DIST
                    flagged += flag
                    review_rows.append({
                        "run": run_number, "detector": det, "slot": slot,
                        "source": label,
                        "pred_x": round(pred[0], 2),
                        "pred_y": round(pred[1], 2),
                        "peak_pixel": peak + offset if peak else "",
                        "snap_dist": round(dist, 2) if dist is not None else "",
                        "n_pixels": len(members),
                        "pixels": ";".join(str(p + offset)
                                           for p in sorted(members)),
                        "flag": "CHECK" if flag else "",
                    })
                    if not flag or args.include_flagged:
                        for p in members:
                            assignments[(run_number, p + offset)] = ids[label]

        out = "source_assignment_review.csv"
        with open(out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(review_rows[0]))
            writer.writeheader()
            writer.writerows(review_rows)
        print(f"{len(review_rows)} slot placements over {len(counts)} runs "
              f"-> {out}; {flagged} flagged CHECK")

        if args.apply:
            updated = 0
            for run_number in sorted(counts):
                run = runs[run_number]
                rps = session.execute(
                    select(RunPixel, Pixel.pixel_number)
                    .join(Pixel, RunPixel.pixel_id == Pixel.id)
                    .where(RunPixel.run_id == run.id)
                ).all()
                for rp, pixel_number in rps:
                    rp.source_id = assignments.get((run_number, pixel_number))
                    updated += rp.source_id is not None
            session.commit()
            print(f"applied: {updated} run_pixels assigned a source")


if __name__ == "__main__":
    main()
