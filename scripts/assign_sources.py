"""Assign sources to run_pixels using the global smooth frame model:
each detector's slot centers are fit as a linear function of the run's
(linear_position, 2D position) across ALL runs at once, so predictions
cannot jump clusters between neighboring scan positions; each slot is
then snapped jointly (distinct clusters, tight radius) and pixels within
one ring are claimed.

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
from calibrationnet.models import (
    Pixel,
    Run,
    RunPixel,
    Source,
    SourceInstallation,
)
from calibrationnet.pipeline.source_assignment import (
    ANCHOR_POSITIONS,
    ANCHOR_RUN,
    MEMBER_RADIUS,
    VERIFY_RADIUS,
    X_PER_INCH,
    Y_PER_2D,
    assign_from_preds,
    compute_baselines,
    excess_map,
    fetch_all_counts,
    fit_affine_trend,
    locate_frame,
    support_at,
    physical_position,
    predict_fixed,
    predict_trend,
    slot_offsets_from_anchors,
)

REVIEW_CSV = "source_assignment_review.csv"
FLAG_DIST = 0.95      # verification moved almost a full radius -> check
MIN_EXCESS = 2.0      # peak must be at least 2x its own baseline


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
        runs = {r.run_number: r for r in session.scalars(select(Run))}
        run_positions = {
            rn: (runs[rn].linear_position, runs[rn].horizontal_position)
            for rn in counts
        }
        slot_maps = {rn: slot_sources_for(session, runs[rn])
                     for rn in counts}

    offsets = {det: slot_offsets_from_anchors(det)
               for det in ("upper", "lower")}
    excesses = {rn: {det: excess_map(counts[rn][det], baselines[det])
                     for det in ("upper", "lower")}
                for rn in counts}

    # Round 1: locate each run's rigid frame with a loose fixed-slope
    # prior; Round 2: refit the cross-run affine trend from those frames
    # and relocate with the tighter, data-driven prior.
    t_by_run = {"upper": {}, "lower": {}}
    for rn in sorted(counts):
        lin, hor = run_positions[rn]
        for det in ("upper", "lower"):
            prior = predict_fixed(det, "R1C2", lin, hor)
            t_by_run[det][rn] = locate_frame(
                excesses[rn][det], det, offsets[det], prior)
    trend = fit_affine_trend(t_by_run, run_positions)
    frames_t = {"upper": {}, "lower": {}}
    for rn in sorted(counts):
        lin, hor = run_positions[rn]
        for det in ("upper", "lower"):
            prior = predict_trend(trend, det, lin, hor)
            frames_t[det][rn] = locate_frame(
                excesses[rn][det], det, offsets[det], prior,
                window=(2.0, 1.5), sigma=1.0)

    review_rows = []
    flagged = 0
    for run_number in sorted(counts):
        lin, hor = run_positions[run_number]
        for det in ("upper", "lower"):
            labels = {s: lab for s, (sid, lab)
                      in slot_maps[run_number].items()}
            tx, ty = frames_t[det][run_number]
            preds = {s: (tx + offsets[det][s][0], ty + offsets[det][s][1])
                     for s in labels}
            excess = excesses[run_number][det]
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
                    "run": run_number, "detector": det, "slot": slot,
                    "source": label_,
                    "pred_x": round(pred[0], 2),
                    "pred_y": round(pred[1], 2),
                    "peak_pixel": peak + offset if peak else "",
                    "snap_dist": round(dist, 2) if dist is not None else "",
                    "peak_excess": round(peak_excess, 1)
                                   if peak_excess else "",
                    "peak_counts": counts[run_number][det].get(peak, "")
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
    print(f"{len(review_rows)} slot placements over {len(counts)} runs "
          f"-> {REVIEW_CSV}; {flagged} flagged CHECK")


def apply_from_csv(label: str, path: str = REVIEW_CSV) -> None:
    """Apply the reviewed CSV: empty/OK rows as computed, REDO rows
    recomputed around the hand-corrected peak_pixel, CHECK rows skipped.
    Membership is recomputed jointly per run-detector so REDO corrections
    claim pixels consistently."""
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
            groups[(int(r["run"]), r["detector"])].append(r)
        elif flag == "CHECK":
            skipped += 1
        else:
            raise SystemExit(f"Unknown flag {r['flag']!r} in row: {r}")

    assignments = {}
    for (run_number, det), group in groups.items():
        peaks = [int(r["peak_pixel"]) for r in group]
        if len(peaks) != len(set(peaks)):
            raise SystemExit(f"Duplicate peak_pixel in run {run_number} "
                             f"{det}: {sorted(peaks)}")
        cmap = counts[run_number][det]
        offset = 1000 if det == "lower" else 0
        centers = []
        for r in group:
            base = int(r["peak_pixel"]) - offset
            px, py = physical_position(base, det)
            ring = {
                p: c for p, c in cmap.items()
                if math.hypot(physical_position(p, det)[0] - px,
                              physical_position(p, det)[1] - py)
                <= MEMBER_RADIUS
            }
            total = sum(ring.values()) or 1
            cx = sum(physical_position(p, det)[0] * c
                     for p, c in ring.items()) / total
            cy = sum(physical_position(p, det)[1] * c
                     for p, c in ring.items()) / total
            centers.append((r["source"], (cx, cy)))
        for p in cmap:
            xx, yy = physical_position(p, det)
            best = None
            for source_label, (cx, cy) in centers:
                d = math.hypot(xx - cx, yy - cy)
                if d <= MEMBER_RADIUS and (best is None or d < best[1]):
                    best = (source_label, d)
            if best:
                assignments[(run_number, p + offset)] = best[0]

    with get_session() as session:
        source_ids = {s.label: s.id for s in session.scalars(select(Source))}
        runs = {r.run_number: r for r in session.scalars(select(Run))}
        updated = 0
        for run_number in sorted(counts):
            run = runs[run_number]
            rps = session.execute(
                select(RunPixel, Pixel.pixel_number)
                .join(Pixel, RunPixel.pixel_id == Pixel.id)
                .where(RunPixel.run_id == run.id)
            ).all()
            for rp, pixel_number in rps:
                label_ = assignments.get((run_number, pixel_number))
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
