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


def snap_to_cluster(pred: tuple, counts: dict, detector: str):
    """Snap a predicted position to the strongest pixel within
    SNAP_RADIUS; returns (peak_pixel, centroid, distance) or None."""
    positions = {p: physical_position(p, detector) for p in counts}
    nearby = {
        p: c for p, c in counts.items()
        if math.hypot(positions[p][0] - pred[0], positions[p][1] - pred[1])
        <= SNAP_RADIUS
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


def assign_detector(counts_det: dict, detector: str, slot_sources: dict,
                    frame: dict, slot_offsets: dict = None) -> list:
    """Assign sources for one run-detector map. Returns rows of
    (slot, source_label, predicted, peak_pixel, snap_dist, members) where
    members maps pixel(1-127) -> its distance to the source center."""
    cd = find_clusters(counts_det, detector, 1)[0]["centroid"]
    positions = {p: physical_position(p, detector) for p in counts_det}
    centers = {}
    results = []
    for slot, source_label in sorted(slot_sources.items()):
        pred = predict_slot(frame, slot, cd)
        if slot_offsets and slot in slot_offsets:
            ox, oy = slot_offsets[slot]
            pred = (pred[0] + ox, pred[1] + oy)
        snapped = snap_to_cluster(pred, counts_det, detector)
        if snapped is None:
            results.append((slot, source_label, pred, None, None, {}))
            continue
        peak, centroid, dist = snapped
        centers[slot] = (source_label, centroid, peak, pred, dist)
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
