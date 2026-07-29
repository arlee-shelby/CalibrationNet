"""Work out which calibration source sat over which pixel, per run segment.

Physical coordinates come from calibrationnet.geometry: upper-detector
pixels use the hit-map coordinates, lower-detector pixels are mirrored
across the vertical center line (the detectors face each other), so one
physical source location maps to the same (x, y) on both.

The method, in the order the evidence is trusted:

1. Geometry proposes. The source frame is rigid, so the offsets between
   its slots are fixed and known; only the frame's position moves. The
   readback position gives a prior for where the frame is, via that
   segment's own position convention (calibrationnet.positions) — always
   as a displacement from a verified anchor, never assuming any
   convention's zero is the detector center.

2. Counts decide. For each segment the whole frame is placed by the
   single translation that best explains the excess counts under ALL its
   slots at once. One noisy pixel cannot drag the frame, because it would
   need four or five accomplices in rigid formation; a genuine
   multi-column move wins easily because that much real evidence
   outweighs the prior.

3. Evidence is per-pixel relative. A pixel's counts are compared to that
   pixel's own median across all segments, so a chronically hot low-gain
   channel looks ordinary and a real source stands out. A lone spike with
   no elevated neighbours is discounted: real sources light up a cluster.
"""

import math
from collections import defaultdict
from statistics import median

from sqlalchemy import func, select

from ..db import get_session
from ..geometry import physical_position
from ..models import Pixel, RunPixel, TrapFilterOutput
from ..positions import CONVENTIONS, predict_slot_position

# Hex centers: adjacent pixels are sqrt(3) apart.
PIXEL_PITCH = math.sqrt(3)
# Pixels within one ring of a source's center belong to that source.
MEMBER_RADIUS = 1.1 * PIXEL_PITCH
# Verification may only move a placement less than a column pitch, so it
# can never silently swap to the neighbouring source's cluster.
VERIFY_RADIUS = 1.0
# One pixel can only testify so loudly, however hot it is.
EXCESS_CAP = 30.0
# A real source elevates its neighbourhood; a junk burst does not.
SUPPORT_RADIUS = 2.0
SUPPORT_EXCESS = 2.0
LONE_SPIKE_FACTOR = 0.15


def fetch_all_counts(label: str = "nabpy-standard") -> dict:
    """{(run_number, segment_index): {detector: {pixel(1-127): count}}}
    without pulling any energy arrays."""
    with get_session() as session:
        rows = session.execute(
            select(RunPixel.run_number, RunPixel.segment_index,
                   Pixel.detector, Pixel.pixel_number,
                   func.array_length(TrapFilterOutput.energies, 1))
            .join(Pixel, RunPixel.pixel_number == Pixel.pixel_number)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(TrapFilterOutput.label == label)
        ).all()
    counts = defaultdict(lambda: defaultdict(dict))
    for run_number, segment_index, detector, pixel_number, count in rows:
        counts[(run_number, segment_index)][detector][
            pixel_number % 1000] = count or 0
    return {k: dict(v) for k, v in counts.items()}


def find_clusters(counts: dict, detector: str, n_clusters: int) -> list:
    """Greedy peak clustering of a {pixel(1-127): count} map. Returns up to
    n_clusters dicts (strongest first) with peak pixel, member pixels,
    total counts, and count-weighted centroid."""
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
            <= 2.1 * PIXEL_PITCH
        }
        total = sum(members.values())
        if total == 0:
            break
        clusters.append({
            "peak": peak,
            "members": members,
            "counts": total,
            "centroid": (
                sum(positions[p][0] * c for p, c in members.items()) / total,
                sum(positions[p][1] * c for p, c in members.items()) / total,
            ),
        })
        for p in members:
            remaining.pop(p)
    return clusters


# --------------------------------------------------------------------------
# Evidence: counts relative to each pixel's own baseline.

def compute_baselines(counts: dict) -> dict:
    """{detector: {pixel(1-127): median count across segments}} — a pixel's
    intrinsic rate, including noise and gain artefacts. A source only
    visits any given pixel in a few segments, so the median is
    essentially source-free."""
    per_pixel = {"upper": defaultdict(list), "lower": defaultdict(list)}
    for dets in counts.values():
        for detector, pixels in dets.items():
            for pixel, count in pixels.items():
                per_pixel[detector][pixel].append(count)
    return {det: {p: max(median(v), 1.0) for p, v in pixels.items()}
            for det, pixels in per_pixel.items()}


def excess_map(counts_det: dict, baseline_det: dict) -> dict:
    """Count / that pixel's own baseline. Above ~2 means a source is
    probably there, whatever the pixel's absolute rate."""
    return {p: c / baseline_det.get(p, 1.0) for p, c in counts_det.items()}


def support_at(peak_pixel: int, detector: str, excess: dict) -> int:
    """How many pixels within one ring of peak_pixel are also elevated."""
    px, py = physical_position(peak_pixel, detector)
    return sum(
        1 for p, e in excess.items()
        if e >= SUPPORT_EXCESS
        and math.hypot(physical_position(p, detector)[0] - px,
                       physical_position(p, detector)[1] - py)
        <= SUPPORT_RADIUS
    )


def _evidence(pos, excess: dict, positions: dict) -> float:
    """Strength of source evidence at a position: the best
    distance-weighted excess within VERIFY_RADIUS, capped, and heavily
    discounted when nothing around it is elevated."""
    best = 0.0
    support = 0
    for p, (x, y) in positions.items():
        d = math.hypot(x - pos[0], y - pos[1])
        if d <= VERIFY_RADIUS:
            best = max(best, min(excess[p], EXCESS_CAP) / (1.0 + d))
        if d <= SUPPORT_RADIUS and excess[p] >= SUPPORT_EXCESS:
            support += 1
    return best if support >= 2 else best * LONE_SPIKE_FACTOR


# --------------------------------------------------------------------------
# The rigid frame: slot offsets, and locating it in one segment.

def slot_offsets(convention: str, detector: str, slots) -> dict:
    """{slot: (dx, dy)} of each slot relative to the frame's reference
    slot, in hex units. These come from the physical frame, so they are
    the same in every run and convention; the grid vectors are fitted from
    the anchor's verified slots so that any slot label — including ones
    the anchor did not have — can be placed."""
    anchor = CONVENTIONS[convention]["anchor"]
    if anchor is None:
        raise ValueError(
            f"Position convention {convention!r} has no verified anchor "
            "yet, so source positions cannot be predicted. Verify one "
            "segment's slot-to-pixel mapping and add it to "
            "calibrationnet.positions.CONVENTIONS."
        )
    verified = anchor["pixels"][detector]

    def center(pixels):
        points = [physical_position(p, detector) for p in pixels]
        return (sum(x for x, _ in points) / len(points),
                sum(y for _, y in points) / len(points))

    centers = {slot: center(pixels) for slot, pixels in verified.items()}
    reference = centers[_reference_slot(centers)]

    # Least-squares grid: center(slot) ~ origin + col*(c) + row*(r).
    import numpy as np
    rows_cols = [(int(s[1]), int(s[3])) for s in centers]
    A = np.array([[1.0, c, r] for r, c in rows_cols])
    solution = [
        np.linalg.lstsq(A, np.array([centers[s][axis] for s in centers]),
                        rcond=None)[0]
        for axis in (0, 1)
    ]

    offsets = {}
    for slot in slots:
        r, c = int(slot[1]), int(slot[3])
        if slot in centers:          # verified: use it exactly
            point = centers[slot]
        else:                        # extrapolate on the fitted grid
            point = tuple(s[0] + s[1] * c + s[2] * r for s in solution)
        offsets[slot] = (point[0] - reference[0], point[1] - reference[1])
    return offsets


def _reference_slot(centers: dict) -> str:
    """The slot other offsets are measured from (lowest label present)."""
    return sorted(centers)[0]


def locate_frame(excess: dict, detector: str, offsets: dict, prior: tuple,
                 window: tuple = (3.5, 2.5), step: float = 0.25,
                 sigma: float = 2.0) -> tuple:
    """The frame translation that best explains the excess counts under all
    slots at once, searched on a grid around the prior.

    Returns the position of the reference slot. The prior only penalises
    distance, so enough real evidence can move the frame well away from
    where the readback suggested."""
    positions = {p: physical_position(p, detector) for p in excess}
    best_t, best_score = prior, None
    nx, ny = int(window[0] / step), int(window[1] / step)
    for i in range(-nx, nx + 1):
        for j in range(-ny, ny + 1):
            t = (prior[0] + i * step, prior[1] + j * step)
            score = -0.5 * ((i * step / sigma) ** 2 + (j * step / sigma) ** 2)
            for dx, dy in offsets.values():
                score += math.log1p(
                    _evidence((t[0] + dx, t[1] + dy), excess, positions))
            if best_score is None or score > best_score:
                best_t, best_score = t, score
    return best_t


def fit_position_trend(frames_by_key: dict, key_positions: dict) -> dict:
    """Least-squares (linear, horizontal) -> frame position, per detector,
    over every located segment. Used as the second-round prior, and it is
    what makes the mapping empirical: a re-homing or a units change shows
    up as new coefficients, not as a wrong prediction."""
    import numpy as np
    trend = {}
    for detector, located in frames_by_key.items():
        keys = [k for k in located if None not in key_positions[k]]
        if len(keys) < 3:
            trend[detector] = None
            continue
        A = np.array([[key_positions[k][0], key_positions[k][1], 1.0]
                      for k in keys])
        trend[detector] = tuple(
            np.linalg.lstsq(A, np.array([located[k][axis] for k in keys]),
                            rcond=None)[0]
            for axis in (0, 1)
        )
    return trend


def predict_trend(trend: dict, detector: str, linear: float,
                  horizontal: float):
    coefficients = trend.get(detector)
    if coefficients is None:
        return None
    cx, cy = coefficients
    return (cx[0] * linear + cx[1] * horizontal + cx[2],
            cy[0] * linear + cy[1] * horizontal + cy[2])


def predict_prior(convention: str, detector: str, offsets: dict,
                  linear: float, horizontal: float):
    """First-round prior for the frame's reference-slot position, from the
    convention's anchor and slopes."""
    reference = _reference_slot(
        CONVENTIONS[convention]["anchor"]["pixels"][detector])
    return predict_slot_position(convention, detector, reference,
                                 linear, horizontal)


# --------------------------------------------------------------------------
# Placing the sources once the frame is located.

def snap_to_cluster(pred: tuple, counts: dict, detector: str,
                    radius: float = VERIFY_RADIUS):
    """Snap a predicted position to the strongest pixel within radius.
    Returns (peak_pixel, centroid, distance) or None."""
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
    return (
        peak,
        (sum(positions[p][0] * c for p, c in members.items()) / total,
         sum(positions[p][1] * c for p, c in members.items()) / total),
        math.hypot(px - pred[0], py - pred[1]),
    )


def snap_all(preds: dict, counts_det: dict, detector: str,
             radius: float = VERIFY_RADIUS) -> dict:
    """Snap every slot to a DISTINCT cluster, closest-first, each snap
    claiming one ring of pixels — so two sources can never be placed on
    the same cluster, which is physically impossible."""
    remaining = dict(counts_det)
    pending = dict(preds)
    snapped = {}
    while pending:
        best_slot, best = None, None
        for slot, pred in pending.items():
            candidate = snap_to_cluster(pred, remaining, detector, radius)
            if candidate and (best is None or candidate[2] < best[2]):
                best_slot, best = slot, candidate
        if best is None:
            break
        snapped[best_slot] = best
        pending.pop(best_slot)
        px, py = physical_position(best[0], detector)
        for p in list(remaining):
            xx, yy = physical_position(p, detector)
            if math.hypot(xx - px, yy - py) <= MEMBER_RADIUS:
                remaining.pop(p)
    return snapped


def assign_from_preds(counts_det: dict, detector: str, slot_sources: dict,
                      preds: dict, radius: float = VERIFY_RADIUS) -> list:
    """Place each slot's source and claim its pixels.

    Returns rows of (slot, source_label, predicted, peak_pixel, snap_dist,
    members), where members maps pixel(1-127) to its distance from the
    source center. Each pixel goes to its nearest source only."""
    positions = {p: physical_position(p, detector) for p in counts_det}
    snapped = snap_all(preds, counts_det, detector, radius)

    centers, results = {}, []
    for slot, source_label in sorted(slot_sources.items()):
        if slot not in snapped:
            results.append((slot, source_label, preds[slot], None, None, {}))
            continue
        peak, centroid, dist = snapped[slot]
        centers[slot] = (source_label, centroid, peak, dist)

    claimed = {}
    for slot, (_, centroid, _, _) in centers.items():
        for p in counts_det:
            d = math.hypot(positions[p][0] - centroid[0],
                           positions[p][1] - centroid[1])
            if d <= MEMBER_RADIUS and (p not in claimed or d < claimed[p][1]):
                claimed[p] = (slot, d)

    for slot, (source_label, _, peak, dist) in centers.items():
        members = {p: d for p, (s, d) in claimed.items() if s == slot}
        results.append((slot, source_label, preds[slot], peak, dist, members))
    return results
