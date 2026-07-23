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
