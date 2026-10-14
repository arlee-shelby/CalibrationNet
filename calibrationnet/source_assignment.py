"""The aim of this module is to try and predict which physical calibration source sat over
which pixel, per run segment.

The physical detector pixel coordinates come from calibrationnet.geometry, where the lower detector
pixels are mirrored across the vertical center line compared to the upper one (i.e. the detectors face
each other), so a single source position maps to the same (x, y) on both detectors.

The method, in the order the information is constructed and trusted:

1. Source holder tray geometry: the source frame is rigid, so the offsets between
   its slots are fixed and known, only the frame's position moves. The position conventions
   (see calibrationnet.positions) provide starting values for the unit conversion between the
   recorded physical position and a pixel hex unit to track centered pixels as the source position
   changes. The convention's "zero" is never assumed to be the detector center.

2. Counts: for each segment the whole frame is placed by the
   single translation that best explains the counts under all its
   slots at once, i.e. one noisy pixel cannot drag the frame, because it would
   need four or five accomplices in rigid formation.

3. The signal is per-pixel relative: a pixel's counts are compared to that
   pixel's own median across all segments, so a chronically low-gain
   channel (which can have a significantly larger number of counts) looks ordinary and
   a real physical source stands out. A lone spike with no elevated neighbours is discounted, i.e.
   real sources light up a cluster of pixels.
"""

import math
from collections import defaultdict
from statistics import median

from sqlalchemy import func, select

from .db import get_session
from .geometry import detector_pixel_position
from .models import (Pixel, RunPixel, RunSegment, Source, SourceInstallation, TrapFilterOutput)
from .positions import predict_slot_position, verified_position_for

# all distances defined below are in the hex-grid units of calibrationnet/geometry.py
# (1 unit = one pixel center-to-corner radius)

# any two touching pixels are sqrt(3) units apart, adjacent columns are 1.5 units apart
ADJACENT_PIXEL_DISTANCE = math.sqrt(3)
# pixels within one ring of a source's center belong to that source (1.1 = center-to-corner radius + 10%)
MEMBER_RADIUS = 1.1 * ADJACENT_PIXEL_DISTANCE
# the measured counts may only move a source frame placement less than a column from the predicted position
# prevents counts from silently allowing the source to be positioned over a neighbouring source's cluster
VERIFY_RADIUS = 1.0   # less than column spacing (1.5 units apart)
# the ratio of a pixel's count to its own baseline is capped so one very noisy pixel cannot outweigh a cluster
ILLUMINATED_RATIO_CAP = 30.0
# a real source elevates a collection of pixels: a candidate position needs at least two pixels within
# ILLUMINATED_RADIUS whose count ratio reaches ILLUMINATED_RATIO, otherwise the candidate is
# discounted by LONE_SPIKE_FACTOR
ILLUMINATED_RADIUS = 2.0
ILLUMINATED_RATIO = 2.0
LONE_SPIKE_FACTOR = 0.15


def fetch_all_counts(label: str = "nabpy-standard") -> dict:
    """Returns counts_by_segment = {segment_id: {detector: counts}}, where segment_id is
    (run_number, segment_index) and counts = {pixel(1-127): count}, for all the trap filter outputs
    with a specified label. Does not pull any actual energy arrays, just their length.
    """
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
    counts_by_segment = defaultdict(lambda: defaultdict(dict))
    for run_number, segment_index, detector, pixel_number, count in rows:
        counts_by_segment[(run_number, segment_index)][detector][pixel_number % 1000] = count or 0
    
    counts_by_segment = {segment_id: dict(counts_by_detector) for segment_id, counts_by_detector in counts_by_segment.items()}
    return counts_by_segment


# --------------------------------------------------------------------------
# The signal is per-pixel relative: counts relative to each pixel's own baseline

def field_configuration_key(run) -> str:
    """Returns the run field configuration as a single compact key, i.e. magnet
    currents (MAIN and UDET) and ExB voltage, rounded to setting precision. The
    source frame mapping depends on these values, trends and baselines must never be
    grouped across field configurations.
    """
    def format_value(value, ndigits=0):
        if value is None:
            return "?"
        formatted_value = f"{round(value, ndigits):g}"
        return formatted_value
    configuration_key = f"{format_value(run.main)}/{format_value(run.udet)}A-exb{format_value(run.exb, -2)}"
    return configuration_key


def compute_baselines(counts_by_segment: dict) -> dict:
    """Returns baselines_by_detector = {detector: baselines}, baselines = {pixel(1-127): median count
    across the segments in counts_by_segment}, a pixel's baseline rate. A source only visits any given 
    pixel a few times across many runs and segments, so the median is essentially the non-centered or 
    source-free baseline measurement.
    """

    counts_per_pixel = {"upper": defaultdict(list), "lower": defaultdict(list)}
    for counts_by_detector in counts_by_segment.values():
        for detector, counts in counts_by_detector.items():
            for pixel, count in counts.items():
                counts_per_pixel[detector][pixel].append(count)

    baselines_by_detector = {}
    for detector, pixels in counts_per_pixel.items():
        baselines_by_detector[detector] = {}
        for pixel, counts_across_segments in pixels.items():
            baselines_by_detector[detector][pixel] = max(median(counts_across_segments), 1.0)
    return baselines_by_detector


def count_ratios(counts: dict, baselines: dict) -> dict:
    """Returns ratios = {pixel(1-127): count / pixel's baseline} for one detector of one
    segment. Above ~2 means a source is probably there.
    """
    ratios = {pixel: count / baselines.get(pixel, 1.0) for pixel, count in counts.items()}
    return ratios


def illuminated_neighbor_number(peak_pixel: int, detector: str, ratios: dict) -> int:
    """Returns illuminated_neighbors, how many pixels within one ring of the peak_pixel have
    elevated counts with a ratio of at least ILLUMINATED_RATIO (the peak itself counts when
    it is elevated).
    """
    x0, y0 = detector_pixel_position(peak_pixel, detector)
    illuminated_neighbors = 0
    for pixel, ratio in ratios.items():
        if ratio < ILLUMINATED_RATIO:
            # not elevated enough above baseline, probably not centered
            continue
        pixel_x, pixel_y = detector_pixel_position(pixel, detector)
        if math.hypot(pixel_x - x0, pixel_y - y0) <= ILLUMINATED_RADIUS:
            illuminated_neighbors += 1
    return illuminated_neighbors


def _signal_strength_at(slot_position: tuple, ratios: dict, pixel_positions: dict) -> float:
    """Returns signal_strength, how strongly the counts say a source is centered at
    slot_position. The strength metric is defined to be the largest count ratio, among the 
    pixels withing VERIFY_RADIUS of slot_position, weighted by the distance between the slot and pixel
    position. Each ratio is capped at ILLUMINATED_RATIO_CAP so one very noisy pixel cannot
    dominate.

    A pixel exactly at slot_position contributes at full strength, one at the edge of
    VERIFY_RADIUS at about half (the 1/(1 + distance) weight). If fewer than two pixels within
    ILLUMINATED_RADIUS are elevated (ratio >= ILLUMINATED_RATIO) the strength is discounted by
    LONE_SPIKE_FACTOR, since a real source lights up a cluster of pixels and a noise burst lights
    up one. Used by locate_frame, which sums log1p(signal_strength) over all of the frame's
    slots for each candidate position.
    """
    signal_strength = 0.0
    illuminated_neighbors = 0
    for pixel, (pixel_x, pixel_y) in pixel_positions.items():
        distance = math.hypot(pixel_x - slot_position[0], pixel_y - slot_position[1])
        if distance <= VERIFY_RADIUS:
            signal_strength = max(signal_strength, min(ratios[pixel], ILLUMINATED_RATIO_CAP) / (1.0 + distance))
        if distance <= ILLUMINATED_RADIUS and ratios[pixel] >= ILLUMINATED_RATIO:
            illuminated_neighbors += 1
    # a lone spike, i.e. fewer than two illuminated pixels around the point, is discounted
    if illuminated_neighbors < 2:
        signal_strength *= LONE_SPIKE_FACTOR
    return signal_strength


# --------------------------------------------------------------------------
# The rigid frame: slot offsets, and locating the frame in one segment

def _reference_slot(centers: dict) -> str:
    """Returns reference_slot, the slot other slots are measured from, and is taken
    to be the lowest slot label present.
    """
    reference_slot = sorted(centers)[0]
    return reference_slot


def slot_offsets(holder: str, convention: str, detector: str, slots) -> dict:
    """Returns offsets = {slot: (offset_x, offset_y)}, the position of each slot relative to the
    frame's reference slot (lowest slot label present in the verified position), in hex units.

    These offsets are a property of the physical tray, so they are the same in every run
    that tray is mounted in. The frame slots are defined in a grid, and the row/column vectors 
    are fitted from the verified tray position's slots, so any slot label can be placed, including 
    ones that were not visible in the verified run.
    """
    verified_pixels_by_slot = verified_position_for(holder, convention)["pixels"][detector]

    def center(pixels):
        """For some verified pixels by slot (for a given holder and convention), multiple pixels can
        be supplied (i.e. a source/slot was not well centered and illuminated a few pixels). This function
        determines the center of pixels supplied by taking the average. 
        """
        points = [detector_pixel_position(pixel, detector) for pixel in pixels]
        center_position = (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))
        return center_position

    centers = {slot: center(pixels) for slot, pixels in verified_pixels_by_slot.items()}
    reference = centers[_reference_slot(centers)]

    # least-squares fit of the slot grid: center(slot) = origin + column * column_step + row * row_step,
    # solved separately for the x and y axes (slot labels look like "R2C3": row 2, column 3)
    # example: slot = "R2C3" -> slot[1] = 2, slot [3] = 3
    import numpy as np
    rows_and_columns = [(int(slot[1]), int(slot[3])) for slot in centers]
    design_matrix = np.array([[1.0, column, row] for row, column in rows_and_columns])
    axis_solutions = [np.linalg.lstsq(design_matrix,np.array([centers[slot][axis] for slot in centers]),rcond=None)[0] for axis in (0, 1)]

    offsets = {}
    for slot in slots:
        row, column = int(slot[1]), int(slot[3])
        if slot in centers:          # verified by eye: use that position exactly
            point = centers[slot]
        else:                        # not verified: extrapolate on the fitted grid
            point = tuple(solution[0] + solution[1] * column + solution[2] * row
                          for solution in axis_solutions)
        offsets[slot] = (point[0] - reference[0], point[1] - reference[1])
    return offsets


def refine_slot_offsets(ratios_by_segment: dict, frames: dict, offsets_by_detector: dict, segment_ids,
                        radius: float = 2.0, min_weight: float = 5.0) -> tuple:
    """Returns (corrected_offsets_by_detector, refine_report): the slot offsets derived from the
    verified tray position, corrected using ALL scanned segments, and
    refine_report[detector][slot] = (applied_x_shift, applied_y_shift, number_of_segments_measured).

    slot_offsets() puts each verified source at its pixel's CENTER, but a source can be
    verified "on pixel 101" while really sitting up to ~0.9 hex units off that center, so
    every slot-to-slot spacing inherits up to a pixel of quantization error (seen directly
    in run 9327, where the predicted R1C2-R1C3 spacing was ~1.2 hex units too large).
    Here every slot's predicted landing point (located frame + offset) is compared with
    the ratio-weighted centroid of the counts around it, once per segment, and the median
    residual per slot is folded back into that slot's offset. Residuals are taken relative
    to the reference slot's residual, so the frame's reference point stays put.
    """
    corrected_offsets_by_detector, refine_report = {}, {}
    for detector, offsets in offsets_by_detector.items():
        residuals = {slot: [] for slot in offsets}
        for segment_id in segment_ids:
            frame = frames.get((segment_id, detector))
            if frame is None:
                continue
            ratios = ratios_by_segment[segment_id][detector]
            pixel_positions = {pixel: detector_pixel_position(pixel, detector) for pixel in ratios}
            for slot, (offset_x, offset_y) in offsets.items():
                predicted_x, predicted_y = frame[0] + offset_x, frame[1] + offset_y
                weight_sum = weighted_x = weighted_y = 0.0
                for pixel, ratio in ratios.items():
                    if ratio <= 0:
                        continue
                    pixel_x, pixel_y = pixel_positions[pixel]
                    if ((pixel_x - predicted_x) ** 2
                            + (pixel_y - predicted_y) ** 2) > radius ** 2:
                        continue
                    weight = min(ratio, ILLUMINATED_RATIO_CAP)
                    weight_sum += weight
                    weighted_x += weight * pixel_x
                    weighted_y += weight * pixel_y
                if weight_sum < min_weight:
                    continue  # slot is off the detector face, or too little signal here
                residuals[slot].append((weighted_x / weight_sum - predicted_x,
                                        weighted_y / weight_sum - predicted_y))

        shifts = {
            slot: ((median([residual[0] for residual in slot_residuals]),
                    median([residual[1] for residual in slot_residuals]))
                   if slot_residuals else (0.0, 0.0))
            for slot, slot_residuals in residuals.items()
        }
        reference_shift = shifts[_reference_slot(offsets)]
        corrected_offsets_by_detector[detector] = {
            slot: (offset_x + shifts[slot][0] - reference_shift[0],
                   offset_y + shifts[slot][1] - reference_shift[1])
            for slot, (offset_x, offset_y) in offsets.items()
        }
        refine_report[detector] = {
            slot: (shifts[slot][0] - reference_shift[0],
                   shifts[slot][1] - reference_shift[1],
                   len(residuals[slot]))
            for slot in offsets
        }
    return corrected_offsets_by_detector, refine_report


def locate_frame(ratios: dict, detector: str, offsets: dict, predicted_position: tuple,
                 search_half_widths: tuple = (3.5, 2.5), search_spacing: float = 0.25,
                 predicted_position_sigma: float = 2.0) -> tuple:
    """Returns best_frame, the (x, y) of the reference slot whose single translation best
    explains the count ratios under all slots at once, searched on a grid of candidate frames
    around the predicted position.

    Distance from the predicted position is only a penalty, not a limit, so enough real signal
    can move the frame well away from where the readback suggested it would be.
    """
    pixel_positions = {pixel: detector_pixel_position(pixel, detector) for pixel in ratios}
    best_frame, best_goodness = predicted_position, None
    steps_x = int(search_half_widths[0] / search_spacing)
    steps_y = int(search_half_widths[1] / search_spacing)
    for step_index_x in range(-steps_x, steps_x + 1):
        for step_index_y in range(-steps_y, steps_y + 1):
            candidate_frame = (
                predicted_position[0] + step_index_x * search_spacing,
                predicted_position[1] + step_index_y * search_spacing)
            # penalty for straying from the predicted position, as a Gaussian in units
            # of predicted_position_sigma
            goodness = -0.5 * (
                (step_index_x * search_spacing / predicted_position_sigma) ** 2
                + (step_index_y * search_spacing / predicted_position_sigma) ** 2)
            for offset_x, offset_y in offsets.values():
                slot_position = (candidate_frame[0] + offset_x, candidate_frame[1] + offset_y)
                goodness += math.log1p(_signal_strength_at(slot_position, ratios, pixel_positions))
            if best_goodness is None or goodness > best_goodness:
                best_frame, best_goodness = candidate_frame, goodness
    return best_frame


def installation_for(session, segment) -> tuple:
    """Returns (sources_by_slot, holder, installation) for one segment, where
    sources_by_slot = {slot: (source_id, source_label)} is what sat in each slot on the
    segment's date, holder is the tray, and installation is the mounting period's key.

    The holder decides the frame geometry, so it travels with the slot map
    rather than being inferred from the date. The installation key — the
    latest installed_on among the rows in force (ISO date string) —
    identifies the MOUNTING period: touching any source means pulling the
    tray, so any change to the installed set starts a new mounting, and
    segments must never be grouped across mountings (a re-installed tray can
    sit differently; AS design, 2026-08-10).
    """
    day = (segment.start_time or segment.run.start_time).date()
    rows = session.execute(
        select(SourceInstallation.slot, Source.id, Source.label,
               SourceInstallation.holder, SourceInstallation.installed_on)
        .join(Source, SourceInstallation.source_id == Source.id)
        .where(SourceInstallation.installed_on <= day)
        .where((SourceInstallation.removed_on.is_(None))
               | (SourceInstallation.removed_on > day))
    ).all()
    holders = {holder for _, _, _, holder, _ in rows}
    if len(holders) > 1:
        raise ValueError(f"segment {segment.run_number}/"
                         f"{segment.segment_index} spans holders {holders}")
    installation = (max(installed_on for _, _, _, _, installed_on in rows).isoformat()
                    if rows else None)
    sources_by_slot = {slot: (source_id, source_label) for slot, source_id, source_label, _, _ in rows}
    holder = holders.pop() if holders else None
    return sources_by_slot, holder, installation


def check_verified_installation(session, holder, convention, installation):
    """A group of segments must take its verified tray position from its OWN
    installation: a re-mounted tray can sit differently, so a position verified under a
    previous mounting must never pin a new one. Raises with the re-verification
    instructions when it does not match, which is the same explicit gap as a missing
    verified position (scan, eye-verify one segment's hit maps, add the
    VERIFIED_TRAY_POSITIONS entry).
    """
    verified_position = verified_position_for(holder, convention)
    verified_segment = session.execute(
        select(RunSegment).where(
            RunSegment.run_number == verified_position["run_number"],
            RunSegment.segment_index == verified_position["segment_index"])
    ).scalar_one_or_none()
    if verified_segment is None:
        raise ValueError(
            f"the verified position for ({holder}, {convention}) points at run "
            f"{verified_position['run_number']} segment {verified_position['segment_index']}, "
            "which is not in the database — ingest it or fix the entry.")
    _sources_by_slot, _holder, verified_installation = installation_for(session, verified_segment)
    if verified_installation != installation:
        raise ValueError(
            f"the ({holder}, {convention}) position was verified under "
            f"installation {verified_installation} (run "
            f"{verified_position['run_number']}), but these segments are from "
            f"installation {installation} — a re-mounted tray can sit "
            "differently, so this installation needs its own verified "
            "position: raster a run, eye-verify one segment's hit maps "
            "(scripts/show_hitmap.py), and add the VERIFIED_TRAY_POSITIONS "
            "entry in calibrationnet/positions.py.")


def locate_all_frames(ratios_by_segment: dict, readback_positions: dict,
                      conventions: dict, holders: dict, offsets_by_conditions: dict,
                      fields: dict = None, installations: dict = None,
                      share_slopes: bool = True) -> tuple:
    """Returns (frames, trends) from a two-round frame location for every (segment_id, detector):
    frames = {(segment_id, detector): frame} and trends = {conditions: trend}.

    Round 1 locates each segment on its own, starting from the position predicted by the
    verified tray position and that segment's readback. Round 2 fits the
    readback -> frame-position trend across all segments sharing the same conditions and
    re-locates each segment starting from that tighter, measured prediction.

    The conditions are (holder, convention), plus the field configuration when `fields`
    is given ({segment_id: field_configuration_key(run)}), because the mapping depends on
    the magnet/ExB configuration, plus the installation when `installations` is given,
    because a re-mounted tray can sit differently. Trends must never be fitted across
    field configurations or mountings. offsets_by_conditions is keyed by
    conditions + (detector,) and holds each group's offsets = {slot: (offset_x, offset_y)}.
    """
    def conditions_of(segment_id):
        conditions = ((installations[segment_id],)
                      if installations is not None else ())
        conditions += (holders[segment_id], conventions[segment_id])
        conditions += ((fields[segment_id],) if fields is not None else ())
        return conditions

    segment_ids = list(ratios_by_segment)
    first_round_frames = {}
    for segment_id in segment_ids:
        for detector in ("upper", "lower"):
            offsets = offsets_by_conditions[conditions_of(segment_id) + (detector,)]
            predicted_position = predict_frame_position(
                holders[segment_id], conventions[segment_id], detector,
                *readback_positions[segment_id])
            first_round_frames[(segment_id, detector)] = locate_frame(
                ratios_by_segment[segment_id][detector], detector, offsets,
                predicted_position)

    frames, trends = {}, {}
    for conditions in {conditions_of(segment_id) for segment_id in segment_ids}:
        group_segment_ids = [segment_id for segment_id in segment_ids
                             if conditions_of(segment_id) == conditions]
        frames_by_detector = {
            detector: {segment_id: first_round_frames[(segment_id, detector)]
                       for segment_id in group_segment_ids}
            for detector in ("upper", "lower")}
        trend = fit_position_trend(frames_by_detector, readback_positions)
        if share_slopes:
            # Default ON (AS ruling 2026-08-15): see share_trend_slopes.
            trend = share_trend_slopes(trend, frames_by_detector,
                                       readback_positions)
        trends[conditions] = trend
        for segment_id in group_segment_ids:
            for detector in ("upper", "lower"):
                predicted_position = (
                    predict_trend(trend, detector,
                                  *readback_positions[segment_id])
                    or first_round_frames[(segment_id, detector)])
                frames[(segment_id, detector)] = locate_frame(
                    ratios_by_segment[segment_id][detector], detector,
                    offsets_by_conditions[conditions + (detector,)], predicted_position,
                    search_half_widths=(2.0, 1.5),
                    predicted_position_sigma=1.0)
    return frames, trends


def share_trend_slopes(trend: dict, frames_by_detector: dict,
                       readback_positions: dict) -> dict:
    """The tray is RIGID: both detectors see the same physical stage
    motion, so the readback->frame slopes must be shared (AS ruling
    2026-08-15). The upper detector's own fit is biased — its count-ratio
    maps are ~5x weaker (the sources favor LDET), and the resulting
    slope error (fit -5.15 hex/inch vs ground-truth -4.33; lower fit
    -4.38 agrees) mis-pointed every UDET dwell of run 9469 by up to
    ~4 mm. Keep the LOWER slopes for both detectors; refit only the
    upper INTERCEPTS (median residual over the located upper frames
    with slopes held). Returns corrected_trend; the input trend itself,
    unchanged, when either detector's fit is missing."""
    import numpy as np
    lower, upper = trend.get("lower"), trend.get("upper")
    if lower is None or upper is None:
        return trend
    points = [(frame, readback_positions[segment_id])
              for segment_id, frame in frames_by_detector["upper"].items()
              if frame is not None
              and None not in readback_positions[segment_id]]
    if not points:
        return trend
    new_upper = []
    for axis in (0, 1):
        # per axis the trend is frame = a * linear + b * horizontal + c
        a, b, _c = lower[axis]
        residuals = [frame[axis] - a * linear - b * horizontal
                     for frame, (linear, horizontal) in points]
        new_upper.append((a, b, float(np.median(residuals))))
    corrected_trend = dict(trend)
    corrected_trend["upper"] = tuple(new_upper)
    return corrected_trend


def fit_position_trend(frames_by_detector: dict,
                       readback_positions: dict) -> dict:
    """Returns trend = {detector: (x_coefficients, y_coefficients)}, each a triple (a, b, c) in
    frame = a * linear + b * horizontal + c, or None for a detector with fewer than three located
    segments: the least-squares fit of (linear, horizontal) readback -> frame position, per
    detector, over every located segment. Used as the second-round prediction, and it is what
    makes the mapping empirical: a re-homing or a units change shows up as new coefficients,
    not as a wrong prediction.
    """
    import numpy as np
    trend = {}
    for detector, frames in frames_by_detector.items():
        segment_ids = [segment_id for segment_id in frames
                       if None not in readback_positions[segment_id]]
        if len(segment_ids) < 3:
            trend[detector] = None
            continue
        design_matrix = np.array(
            [[readback_positions[segment_id][0],
              readback_positions[segment_id][1], 1.0]
             for segment_id in segment_ids])
        trend[detector] = tuple(
            np.linalg.lstsq(design_matrix,
                            np.array([frames[segment_id][axis]
                                      for segment_id in segment_ids]),
                            rcond=None)[0]
            for axis in (0, 1)
        )
    return trend


def predict_trend(trend: dict, detector: str, linear: float,
                  horizontal: float):
    """Returns predicted_position, the frame position this detector's fitted trend predicts at
    a (linear, horizontal) readback, or None when the trend could not be fitted."""
    coefficients = trend.get(detector)
    if coefficients is None:
        return None
    x_coefficients, y_coefficients = coefficients
    predicted_position = (x_coefficients[0] * linear + x_coefficients[1] * horizontal + x_coefficients[2],
                          y_coefficients[0] * linear + y_coefficients[1] * horizontal + y_coefficients[2])
    return predicted_position


def predict_frame_position(holder: str, convention: str, detector: str,
                           linear: float, horizontal: float):
    """Returns predicted_position, the first-round prediction of where the frame's reference
    slot sits, from this tray's verified position and the convention's unit conversions."""
    reference_slot = _reference_slot(verified_position_for(holder, convention)["pixels"][detector])
    predicted_position = predict_slot_position(holder, convention, detector, reference_slot, linear, horizontal)
    return predicted_position


# --------------------------------------------------------------------------
# Placing the sources once the frame is located

def match_to_cluster(predicted_position: tuple, ratios: dict, detector: str,
                     radius: float = VERIFY_RADIUS):
    """Returns (peak_pixel, centroid, match_distance) for the strongest pixel within radius of a
    slot's predicted position, or None when no pixel is in range. The centroid is the
    ratio-weighted center of the pixels within MEMBER_RADIUS of that peak, and match_distance
    is how far the peak sits from the prediction.
    """
    pixel_positions = {pixel: detector_pixel_position(pixel, detector) for pixel in ratios}
    nearby = {
        pixel: ratio for pixel, ratio in ratios.items()
        if math.hypot(pixel_positions[pixel][0] - predicted_position[0],
                      pixel_positions[pixel][1] - predicted_position[1]) <= radius
    }
    if not nearby:
        return None
    peak_pixel = max(nearby, key=nearby.get)
    peak_x, peak_y = pixel_positions[peak_pixel]
    members = {
        pixel: ratio for pixel, ratio in ratios.items()
        if math.hypot(pixel_positions[pixel][0] - peak_x,
                      pixel_positions[pixel][1] - peak_y) <= MEMBER_RADIUS
    }
    ratio_sum = sum(members.values()) or 1
    centroid = (sum(pixel_positions[pixel][0] * ratio for pixel, ratio in members.items()) / ratio_sum,
                sum(pixel_positions[pixel][1] * ratio for pixel, ratio in members.items()) / ratio_sum)
    match_distance = math.hypot(peak_x - predicted_position[0], peak_y - predicted_position[1])
    return peak_pixel, centroid, match_distance


def match_all_slots(predicted_positions: dict, ratios: dict,
                    detector: str, radius: float = VERIFY_RADIUS) -> dict:
    """Returns matches_by_slot = {slot: (peak_pixel, centroid, match_distance)}: every slot
    matched to a DISTINCT cluster, closest match first, each match claiming one ring of pixels,
    so two sources can never be placed on the same cluster, which is physically impossible.
    Slots with no pixel in range are left out.
    """
    unclaimed_ratios = dict(ratios)
    unmatched_slots = dict(predicted_positions)
    matches_by_slot = {}
    while unmatched_slots:
        best_slot, best_match = None, None
        for slot, predicted_position in unmatched_slots.items():
            candidate_match = match_to_cluster(predicted_position, unclaimed_ratios, detector, radius)
            # candidate_match[2] is the match distance: keep the closest match this round
            if candidate_match and (best_match is None
                                    or candidate_match[2] < best_match[2]):
                best_slot, best_match = slot, candidate_match
        if best_match is None:
            break
        matches_by_slot[best_slot] = best_match
        unmatched_slots.pop(best_slot)
        peak_x, peak_y = detector_pixel_position(best_match[0], detector)
        for pixel in list(unclaimed_ratios):
            pixel_x, pixel_y = detector_pixel_position(pixel, detector)
            if math.hypot(pixel_x - peak_x, pixel_y - peak_y) <= MEMBER_RADIUS:
                unclaimed_ratios.pop(pixel)
    return matches_by_slot


def assign_sources_from_predictions(ratios: dict, detector: str,
                                    source_labels_by_slot: dict,
                                    predicted_positions: dict,
                                    radius: float = VERIFY_RADIUS) -> list:
    """Returns placements, one row per slot: (slot, source_label, predicted_position, peak_pixel,
    match_distance, members), where members = {pixel(1-127): distance from the source center}.
    An unmatched slot has peak_pixel and match_distance None and no members. Each pixel goes to
    its nearest source only.
    """
    pixel_positions = {pixel: detector_pixel_position(pixel, detector) for pixel in ratios}
    matches_by_slot = match_all_slots(predicted_positions, ratios, detector, radius)

    matched_slots, placements = {}, []
    for slot, source_label in sorted(source_labels_by_slot.items()):
        if slot not in matches_by_slot:
            placements.append((slot, source_label, predicted_positions[slot],
                               None, None, {}))
            continue
        peak_pixel, centroid, match_distance = matches_by_slot[slot]
        matched_slots[slot] = (source_label, centroid, peak_pixel, match_distance)

    # every pixel within MEMBER_RADIUS of a source center is claimed by the nearest one
    claimed_pixels = {}
    for slot, (_, centroid, _, _) in matched_slots.items():
        for pixel in ratios:
            distance = math.hypot(pixel_positions[pixel][0] - centroid[0],
                                  pixel_positions[pixel][1] - centroid[1])
            if distance <= MEMBER_RADIUS and (
                    pixel not in claimed_pixels
                    or distance < claimed_pixels[pixel][1]):
                claimed_pixels[pixel] = (slot, distance)

    for slot, (source_label, _, peak_pixel, match_distance) in matched_slots.items():
        members = {pixel: distance
                   for pixel, (claiming_slot, distance) in claimed_pixels.items()
                   if claiming_slot == slot}
        placements.append((slot, source_label, predicted_positions[slot], peak_pixel,
                           match_distance, members))
    return placements
