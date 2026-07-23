"""Locate source count clusters on the detectors and relate them to the
frame positions, toward automatic run_pixel -> source assignment.

Physical coordinates: upper-detector pixels use the hit-map coordinates
from calibrationnet.geometry; lower-detector pixels are mirrored across
the vertical center line (the detectors face each other), so one physical
source location maps to the same (x, y) on both detectors.
"""

import math
from collections import defaultdict

from sqlalchemy import func, select

from ..db import get_session
from ..geometry import mirrored_x, pixel_positions
from ..models import Pixel, Run, RunPixel, TrapFilterOutput

# Hex centers: adjacent = sqrt(3); a "cluster" is a peak plus everything
# within two rings.
CLUSTER_RADIUS = 2.1 * math.sqrt(3)


def fetch_all_counts(label: str = "nabpy-standard") -> dict:
    """{run_number: {detector: {pixel_number(1-127): count}}} without
    pulling any energy arrays."""
    with get_session() as session:
        rows = session.execute(
            select(Run.run_number, Pixel.detector, Pixel.pixel_number,
                   func.array_length(TrapFilterOutput.energies, 1))
            .join(RunPixel, RunPixel.run_id == Run.id)
            .join(Pixel, RunPixel.pixel_id == Pixel.id)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(TrapFilterOutput.label == label)
        ).all()
    counts = defaultdict(lambda: defaultdict(dict))
    for run_number, detector, pixel_number, count in rows:
        counts[run_number][detector][pixel_number % 1000] = count or 0
    return {r: dict(d) for r, d in counts.items()}


def physical_position(pixel_1_127: int, detector: str) -> tuple:
    x, y = pixel_positions()[pixel_1_127]
    return (mirrored_x(x), y) if detector == "lower" else (x, y)


def find_clusters(counts: dict, detector: str, n_clusters: int) -> list:
    """Greedy peak clustering of a {pixel(1-127): count} map.

    Returns up to n_clusters dicts (strongest first): peak pixel,
    member pixels, total counts, and count-weighted centroid (x, y) in
    physical coordinates."""
    positions = {p: physical_position(p, detector) for p in counts}
    remaining = dict(counts)
    clusters = []
    for _ in range(n_clusters):
        if not remaining:
            break
        peak = max(remaining, key=remaining.get)
        px, py = positions[peak]
        members = {
            p: c for p, c in remaining.items()
            if math.hypot(positions[p][0] - px, positions[p][1] - py)
            <= CLUSTER_RADIUS
        }
        total = sum(members.values())
        if total == 0:
            break
        cx = sum(positions[p][0] * c for p, c in members.items()) / total
        cy = sum(positions[p][1] * c for p, c in members.items()) / total
        clusters.append({
            "peak": peak,
            "members": members,
            "counts": total,
            "centroid": (cx, cy),
        })
        for p in members:
            remaining.pop(p)
    return clusters


# ---------------------------------------------------------------------------
# Frame-based assignment: the frame's position in each run is anchored to
# that run's measured 109Cd cluster (unmistakable: ~10x hotter); the other
# slots are placed with rigid frame vectors derived from the confirmed
# run-8622 anchors, then snapped to the local count maximum.

REF_RUN = 8622
# Confirmed by eye (AS) for run 8622: slot -> center pixel per detector.
REF_ANCHORS = {
    "upper": {"R2C1": 60, "R2C2": 76},
    "lower": {"R2C1": 48, "R2C2": 52},
}
CD_SLOT = "R1C2"        # the Cd source's slot in the reference period
SNAP_RADIUS = 2.6       # look for the local max within ~1.5 pixel pitches
MEMBER_RADIUS = 1.1 * math.sqrt(3)  # pixels within one ring of the center


def build_frames(counts: dict) -> dict:
    """Per-detector rigid frame vectors from the reference run: column
    step (C->C+1), row step (R2->R1), and the reference Cd centroid."""
    frames = {}
    for det in ("upper", "lower"):
        cd = find_clusters(counts[REF_RUN][det], det, 1)[0]["centroid"]
        p1 = physical_position(REF_ANCHORS[det]["R2C1"], det)
        p2 = physical_position(REF_ANCHORS[det]["R2C2"], det)
        frames[det] = {
            "base": p2,                                  # R2C2 position
            "col": (p2[0] - p1[0], p2[1] - p1[1]),
            "row": (cd[0] - p2[0], cd[1] - p2[1]),
            "cd_ref": cd,
        }
    return frames


def predict_slot(frame: dict, slot: str, cd_run: tuple) -> tuple:
    """Predicted physical (x, y) of a slot in a run whose Cd centroid is
    cd_run. Works for any RrCc label."""
    r, c = int(slot[1]), int(slot[3])
    dx = cd_run[0] - frame["cd_ref"][0]
    dy = cd_run[1] - frame["cd_ref"][1]
    return (
        frame["base"][0] + (c - 2) * frame["col"][0]
        + (2 - r) * frame["row"][0] + dx,
        frame["base"][1] + (c - 2) * frame["col"][1]
        + (2 - r) * frame["row"][1] + dy,
    )


def snap_to_cluster(pred: tuple, counts: dict, detector: str,
                    radius: float = SNAP_RADIUS):
    """Snap a predicted position to the strongest pixel within
    SNAP_RADIUS; returns (peak_pixel, centroid, distance) or None."""
    positions = {p: physical_position(p, detector) for p in counts}
    nearby = {
        p: c for p, c in counts.items()
        if math.hypot(positions[p][0] - pred[0], positions[p][1] - pred[1])
        <= radius
    }
    if not nearby:
        return None
    peak = max(nearby, key=nearby.get)
    px, py = positions[peak]
    members = {
        p: c for p, c in counts.items()
        if math.hypot(positions[p][0] - px, positions[p][1] - py)
        <= MEMBER_RADIUS
    }
    total = sum(members.values()) or 1
    cx = sum(positions[p][0] * c for p, c in members.items()) / total
    cy = sum(positions[p][1] * c for p, c in members.items()) / total
    dist = math.hypot(px - pred[0], py - pred[1])
    return peak, (cx, cy), dist


def snap_all(preds: dict, counts_det: dict, detector: str,
             radius: float = SNAP_RADIUS) -> dict:
    """Jointly snap predicted slot positions to DISTINCT clusters: greedy
    by snap distance, and each snap claims one ring of pixels, so two
    sources can never be placed on the same cluster (physically
    impossible). Returns {slot: (peak, centroid, dist)}; slots that find
    no unclaimed cluster are absent."""
    remaining = dict(counts_det)
    pending = dict(preds)
    out = {}
    while pending:
        best_slot, best = None, None
        for slot, pred in pending.items():
            snapped = snap_to_cluster(pred, remaining, detector, radius)
            if snapped and (best is None or snapped[2] < best[2]):
                best_slot, best = slot, snapped
        if best is None:
            break
        peak = best[0]
        out[best_slot] = best
        pending.pop(best_slot)
        px, py = physical_position(peak, detector)
        for p in list(remaining):
            xx, yy = physical_position(p, detector)
            if math.hypot(xx - px, yy - py) <= MEMBER_RADIUS:
                remaining.pop(p)
    return out


def assign_detector(counts_det: dict, detector: str, slot_sources: dict,
                    frame: dict, slot_offsets: dict = None) -> list:
    """Assign sources for one run-detector map. Returns rows of
    (slot, source_label, predicted, peak_pixel, snap_dist, members) where
    members maps pixel(1-127) -> its distance to the source center."""
    cd = find_clusters(counts_det, detector, 1)[0]["centroid"]
    positions = {p: physical_position(p, detector) for p in counts_det}
    preds = {}
    for slot in slot_sources:
        pred = predict_slot(frame, slot, cd)
        if slot_offsets and slot in slot_offsets:
            ox, oy = slot_offsets[slot]
            pred = (pred[0] + ox, pred[1] + oy)
        preds[slot] = pred
    snaps = snap_all(preds, counts_det, detector)
    centers = {}
    results = []
    for slot, source_label in sorted(slot_sources.items()):
        if slot not in snaps:
            results.append((slot, source_label, preds[slot], None, None, {}))
            continue
        peak, centroid, dist = snaps[slot]
        centers[slot] = (source_label, centroid, peak, preds[slot], dist)
    claimed = {}
    for slot, (label, centroid, peak, pred, dist) in centers.items():
        for p in counts_det:
            d = math.hypot(positions[p][0] - centroid[0],
                           positions[p][1] - centroid[1])
            if d <= MEMBER_RADIUS and (
                    p not in claimed or d < claimed[p][1]):
                claimed[p] = (slot, d)
    for slot, (label, centroid, peak, pred, dist) in centers.items():
        members = {p: d for p, (s, d) in claimed.items() if s == slot}
        results.append((slot, label, pred, peak, dist, members))
    return results


# ---------------------------------------------------------------------------
# Global smooth model: one linear fit per detector over ALL runs and slots,
# slot_center(lin, 2D) = a1*lin + a2*2D + offset_slot. Because predictions
# vary smoothly with the scan positions, a wrong-cluster jump between
# neighboring runs (which per-run anchoring allowed) cannot happen; paired
# with a tight snap radius the wrong source is simply out of reach.

FINAL_SNAP_RADIUS = 1.4   # < half the ~5-unit source spacing


def fit_global_model(counts: dict, run_positions: dict,
                     slots_by_run: dict, frames: dict) -> dict:
    """Iteratively fit the smooth model from confident snaps only.
    run_positions: {run: (lin, hor)}; slots_by_run: {run: [slot, ...]}.
    Returns {det: {"slopes": (ax1, ax2, ay1, ay2), "offsets": {slot: (ox, oy)}}}."""
    import numpy as np

    model = {}
    for det in ("upper", "lower"):
        # Round 1 seed points: per-run Cd-anchored predictions, tight cut.
        points = []  # (lin, hor, slot, cx, cy)
        for run, slots in slots_by_run.items():
            cmap = counts[run][det]
            cd = find_clusters(cmap, det, 1)[0]["centroid"]
            preds = {s: predict_slot(frames[det], s, cd) for s in slots}
            for s, (peak, cen, dist) in snap_all(preds, cmap, det).items():
                if dist <= 1.0:
                    lin, hor = run_positions[run]
                    points.append((lin, hor, s, cen[0], cen[1]))

        for round_radius, keep in ((1.2, 0.9), (None, None)):
            slots = sorted({p[2] for p in points})
            index = {s: i for i, s in enumerate(slots)}
            A = np.zeros((len(points), 2 + len(slots)))
            bx = np.zeros(len(points))
            by = np.zeros(len(points))
            for i, (lin, hor, s, cx, cy) in enumerate(points):
                A[i, 0], A[i, 1] = lin, hor
                A[i, 2 + index[s]] = 1.0
                bx[i], by[i] = cx, cy
            solx, *_ = np.linalg.lstsq(A, bx, rcond=None)
            soly, *_ = np.linalg.lstsq(A, by, rcond=None)
            model[det] = {
                "slopes": (solx[0], solx[1], soly[0], soly[1]),
                "offsets": {s: (solx[2 + i], soly[2 + i])
                            for s, i in index.items()},
            }
            if round_radius is None:
                break
            # Re-snap everything from the smooth model; refit on the
            # confident subset.
            points = []
            for run, slots_r in slots_by_run.items():
                cmap = counts[run][det]
                lin, hor = run_positions[run]
                preds = {s: predict_global(model, det, s, lin, hor)
                         for s in slots_r if s in model[det]["offsets"]}
                for s, (peak, cen, dist) in snap_all(
                        preds, cmap, det, round_radius).items():
                    if dist <= keep:
                        points.append((lin, hor, s, cen[0], cen[1]))
    return model


def predict_global(model: dict, det: str, slot: str,
                   lin: float, hor: float) -> tuple:
    ax1, ax2, ay1, ay2 = model[det]["slopes"]
    ox, oy = model[det]["offsets"][slot]
    return (ax1 * lin + ax2 * hor + ox, ay1 * lin + ay2 * hor + oy)


def assign_from_preds(counts_det: dict, detector: str, slot_sources: dict,
                      preds: dict, radius: float = None) -> list:
    """Like assign_detector but with externally supplied predictions and
    a tight snap radius. counts_det may be raw counts or excess ratios.
    Same result tuple layout."""
    positions = {p: physical_position(p, detector) for p in counts_det}
    snaps = snap_all(preds, counts_det, detector,
                     radius if radius is not None else FINAL_SNAP_RADIUS)
    centers = {}
    results = []
    for slot, source_label in sorted(slot_sources.items()):
        if slot not in snaps:
            results.append((slot, source_label, preds[slot], None, None, {}))
            continue
        peak, centroid, dist = snaps[slot]
        centers[slot] = (source_label, centroid, peak, preds[slot], dist)
    claimed = {}
    for slot, (label, centroid, peak, pred, dist) in centers.items():
        for p in counts_det:
            d = math.hypot(positions[p][0] - centroid[0],
                           positions[p][1] - centroid[1])
            if d <= MEMBER_RADIUS and (p not in claimed or d < claimed[p][1]):
                claimed[p] = (slot, d)
    for slot, (label, centroid, peak, pred, dist) in centers.items():
        members = {p: d for p, (s, d) in claimed.items() if s == slot}
        results.append((slot, label, pred, peak, dist, members))
    return results


# ---------------------------------------------------------------------------
# Geometry-first prediction (counts verify, never steer):
#   - fixed slopes from the known motion: 0.4 inch of linear position = one
#     pixel column (1.5 units) -> 3.75 units/inch; 1 unit of 2D = one pixel
#     vertically -> -sqrt(3) units per 2D unit (increasing 2D moves down).
#   - anchored at the user-verified run 8622 positions.
#   - verification against EXCESS counts (count / that pixel's median count
#     across all runs), which neutralizes chronically hot low-gain pixels.

X_PER_INCH = 1.5 / 0.4          # one column per 0.4 inch
Y_PER_2D = -math.sqrt(3)        # one pixel row per 2D unit, + moves down
ANCHOR_RUN = 8622
ANCHOR_POSITIONS = (34.0, 2.7)  # (linear, 2D) of the anchor run
# User-verified centers for run 8622 (stored pixel numbering).
ANCHOR_PIXELS = {
    "upper": {"R1C2": [106], "R1C3": [109], "R2C1": [60], "R2C2": [76],
              "R2C3": [67, 80]},
    "lower": {"R1C2": [1019], "R1C3": [1022], "R2C1": [1048],
              "R2C2": [1052], "R2C3": [1068]},
}
VERIFY_RADIUS = 1.0             # < column pitch: cannot change columns


def compute_baselines(counts: dict) -> dict:
    """{det: {pixel(1-127): median count across runs}} — a pixel's
    intrinsic rate (noise, gain artifacts); sources only visit each pixel
    in a few runs, so the median is source-free."""
    from statistics import median
    per_pixel = {"upper": {}, "lower": {}}
    for run, dets in counts.items():
        for det in per_pixel:
            for p, c in dets[det].items():
                per_pixel[det].setdefault(p, []).append(c)
    return {det: {p: max(median(v), 1.0) for p, v in pixels.items()}
            for det, pixels in per_pixel.items()}


def excess_map(counts_det: dict, baseline_det: dict) -> dict:
    """Count / per-pixel baseline: >~2 means a source is likely there."""
    return {p: c / baseline_det.get(p, 1.0) for p, c in counts_det.items()}


def predict_fixed(det: str, slot: str, lin: float, hor: float) -> tuple:
    """Anchor position + fixed-slope displacement. Lower-detector physical
    coordinates are already mirrored, so the same slopes apply."""
    pixels = ANCHOR_PIXELS[det][slot]
    xs, ys = zip(*(physical_position(p % 1000, det) for p in pixels))
    ax, ay = sum(xs) / len(xs), sum(ys) / len(ys)
    dlin = lin - ANCHOR_POSITIONS[0]
    dhor = hor - ANCHOR_POSITIONS[1]
    return (ax + X_PER_INCH * dlin, ay + Y_PER_2D * dhor)


# ---------------------------------------------------------------------------
# Joint rigid-frame localization: per run, grid-search the single frame
# translation T that maximizes excess-count evidence under ALL slots at
# once (rigid frame: sources move together, so one noisy pixel cannot
# hijack the fit), with the smooth cross-run trend as a soft prior. This
# lets counts speak (real two-column moves win) while geometry keeps
# individual clusters honest.


def slot_offsets_from_anchors(det: str) -> dict:
    """Rigid offsets of each slot relative to the Cd slot (R1C2), from
    the user-verified run-8622 anchor pixels."""
    def center(pixels):
        xs, ys = zip(*(physical_position(p % 1000, det) for p in pixels))
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cd = center(ANCHOR_PIXELS[det]["R1C2"])
    return {slot: tuple(a - b for a, b in zip(center(pixels), cd))
            for slot, pixels in ANCHOR_PIXELS[det].items()}


EXCESS_CAP = 30.0        # one pixel can only testify so loudly
SUPPORT_RADIUS = 2.0     # one hex ring: real sources elevate neighbors
LONE_SPIKE_FACTOR = 0.15  # discount for evidence with no neighbor support


def _evidence(pos, excess, positions):
    """Strength of source evidence at a position: best distance-weighted
    excess within VERIFY_RADIUS, capped, and heavily discounted if it has
    no support — a real source elevates a cluster of pixels, while a junk
    burst on a dead/low-gain channel is a lone spike."""
    best = 0.0
    support = 0
    for p, (x, y) in positions.items():
        d = math.hypot(x - pos[0], y - pos[1])
        if d <= VERIFY_RADIUS:
            v = min(excess[p], EXCESS_CAP) / (1.0 + d)
            if v > best:
                best = v
        if d <= SUPPORT_RADIUS and excess[p] >= 2.0:
            support += 1
    return best if support >= 2 else best * LONE_SPIKE_FACTOR


def support_at(peak_pixel: int, det: str, excess: dict) -> int:
    """Number of pixels within one ring of peak_pixel with excess >= 2."""
    px, py = physical_position(peak_pixel, det)
    return sum(
        1 for p, e in excess.items()
        if e >= 2.0 and math.hypot(physical_position(p, det)[0] - px,
                                   physical_position(p, det)[1] - py)
        <= SUPPORT_RADIUS
    )


def locate_frame(excess: dict, det: str, offsets: dict, prior: tuple,
                 window: tuple = (3.5, 2.5), step: float = 0.25,
                 sigma: float = 2.0) -> tuple:
    """Return the frame translation T (position of the Cd slot) that
    maximizes sum(log1p(evidence at T+offset_slot)) - prior penalty."""
    positions = {p: physical_position(p, det) for p in excess}
    best_t, best_score = prior, None
    nx = int(window[0] / step)
    ny = int(window[1] / step)
    for i in range(-nx, nx + 1):
        for j in range(-ny, ny + 1):
            t = (prior[0] + i * step, prior[1] + j * step)
            score = -0.5 * ((i * step / sigma) ** 2 + (j * step / sigma) ** 2)
            for slot, (ox, oy) in offsets.items():
                score += math.log1p(
                    _evidence((t[0] + ox, t[1] + oy), excess, positions))
            if best_score is None or score > best_score:
                best_t, best_score = t, score
    return best_t


def fit_affine_trend(t_by_run: dict, run_positions: dict) -> dict:
    """Least-squares (lin, hor) -> T trend per detector for the prior."""
    import numpy as np
    out = {}
    for det, entries in t_by_run.items():
        A = np.array([[run_positions[rn][0], run_positions[rn][1], 1.0]
                      for rn in entries])
        tx = np.array([entries[rn][0] for rn in entries])
        ty = np.array([entries[rn][1] for rn in entries])
        cx, *_ = np.linalg.lstsq(A, tx, rcond=None)
        cy, *_ = np.linalg.lstsq(A, ty, rcond=None)
        out[det] = (cx, cy)
    return out


def predict_trend(trend, det, lin, hor) -> tuple:
    cx, cy = trend[det]
    return (cx[0] * lin + cx[1] * hor + cx[2],
            cy[0] * lin + cy[1] * hor + cy[2])
