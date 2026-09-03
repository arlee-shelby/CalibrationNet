"""Calibration source position and conventions. This module mainly contains all the necessary ingredients
for source assignment and predicting positions based on dedicated position grid sweep runs.

A raw position number is only meaningful together with the convention it
was recorded in, so every run_segment stores its convention name:

  "legacy-units"  (before 2026-07-24)
      Positions were only written in the run-description free text (i.e. not recorded in
      the slow controls database). The linear position is in inches, the horizontal ("2D")
      in machine units, with the centered position reading 2.7 units. The horizontal limits
      were from 1.7 to 3.7.

  "inches-2026"   (from 2026-07-24)
      Positions come from the recorded RSIS position, as stored in the new slow control database
      (calibrationnet.acquisition.epics_controls). Both the horizontal and linear positions are
      in inches, and the RSIS centered position is now 0 horizontally. The horizontal limits were
      from -0.5 to 0.5

Nothing here assumes any convention's zero is the detector center. Source
assignment only ever uses *displacements*. Each convention carries its own
anchor, a run/segment whose source position and corresponding centered pixels were
verified by eye. This is used during source assignment, where the pipeline attempts to determine which
physical source was centered over a particular pixel for a given run/segment. A
re-homing only requires a new anchor in the new convention, never a conversion between conventions.
"""

from datetime import date
from typing import Optional

from .geometry import HORIZONTAL_OFFSET, VERTICAL_OFFSET, detector_pixel_position

LEGACY = "legacy-units"
INCHES_2026 = "inches-2026"

# runs from this date report positions from RSIS, in inches
INCHES_2026_START = date(2026, 7, 24)

# 0.4 inch of travel moves the sources one pixel column, and a column step is
# HORIZONTAL_OFFSET hex units (see geometry.py)
HEX_PER_INCH = HORIZONTAL_OFFSET / 0.4

# for legacy horizontal "units", one unit moved the sources about one pixel row
# (VERTICAL_OFFSET hex units, see geometry.py)
HEX_PER_LEGACY_UNIT = VERTICAL_OFFSET

CONVENTIONS = {
    LEGACY: {
        "linear_units": "inch",
        "horizontal_units": "machine units (~1 pixel row per unit)",
        # hex units of source motion per unit, increasing linear position moves the sources +x
        # increasing horizontal moves them -y (toward pixel 70)
        "hex_per_linear": HEX_PER_INCH,
        "hex_per_horizontal": -HEX_PER_LEGACY_UNIT,},
    INCHES_2026: {
        "linear_units": "inch",
        "horizontal_units": "inch",
        # hex units of source motion per inch, both derived from rastering data runs
        # (linear: run 9370, horizontal: 9326,9327)
        # note, these values are a start point for source assignment and position planning, they are re-derived from
        # measured data in the locate_all_frames function in source_assignment.py
        "hex_per_linear": 3.35,
        "hex_per_horizontal": -3.90,},
}

# source holder geometry belongs to the physical tray, and a tray can be removed and re-installed
# the source position is dependent on an anchor which is keyed by (holder, convention)
#
# re-installing a known tray under a convention it has no anchor for is an explicit gap:
# the slot offsets carry over, but source assignment asks for a verified segment to pin the position rather than assume
# previous mounting still applies
ANCHORS = {
    ("5-slot", LEGACY): {
        "run_number": 8622,
        "segment_index": 0,
        "linear_position": 34.0,
        "horizontal_position": 2.7,
        # verified by eye: slot -> center pixel(s), per detector.
        "pixels": {
            "upper": {"R1C2": [106], "R1C3": [109], "R2C1": [60],
                      "R2C2": [76], "R2C3": [67, 80]},
            "lower": {"R1C2": [1019], "R1C3": [1022], "R2C1": [1048],
                      "R2C2": [1052], "R2C3": [1068]},
        },
    },
    ("6-slot", INCHES_2026): {
        "run_number": 9326,
        "segment_index": 0,
        "linear_position": 33.502,
        "horizontal_position": -0.249,
        # the R2C3 entries are the expected region (no peaks were actually visible, but kept because they are
        # exactly grid-consistent with the verified slots
        # R1C1 is absent because it sits off the detector face at this position and is extrapolated from the grid instead
        "pixels": {
            "upper": {"R1C2": [97], "R1C3": [101],
                      "R2C1": [59], "R2C2": [50], "R2C3": [67]},
            "lower": {"R1C2": [1019], "R1C3": [1032],
                      "R2C1": [1059], "R2C2": [1076], "R2C3": [1079]},
        },
    },
    ("5-slot", INCHES_2026): {
        "run_number": 9464,
        "segment_index": 30,
        "linear_position": 33.320846875,
        "horizontal_position": -0.3202057631349333,
        # the upper R2C1 center (~47) is approximate
        "pixels": {
            "upper": {"R1C2": [85], "R1C3": [77, 89],
                      "R2C1": [47], "R2C2": [51], "R2C3": [43]},
            "lower": {"R1C2": [1038], "R1C3": [1041],
                      "R2C1": [1072], "R2C2": [1087, 1076],
                      "R2C3": [1091, 1079]},
        },
    },
}


def anchor_for(holder: str, convention: str) -> dict:
    """Returns the verified anchor for a source holder under a specific RSIS motion unit convention.

    Raises an error, with an actionable message, when a known holder reappears under a
    convention it has not been anchored in, i.e. the geometry is reusable, the
    mounting position is not (this keeps the holder configuration fixed but allows for slight position
    changes between installations).
    """
    anchor = ANCHORS.get((holder, convention))
    if anchor is None:
        known = sorted(h for h, c in ANCHORS if h == holder)
        raise ValueError(
            f"no anchor for holder {holder!r} under convention "
            f"{convention!r}. " + (
                f"That tray is anchored under {known} — its slot geometry "
                "carries over, but one segment's slot-to-pixel mapping must "
                "be verified to pin where it is mounted now."
                if known else
                "That tray has never been anchored: verify one segment's "
                "slot-to-pixel mapping first."
            ) + " Add it to calibrationnet.positions.ANCHORS."
        )
    return anchor


def horizontal_limit(convention: str, linear: float):
    """Returns the maximum and minimum horizontal travel allowed at this linear position, or
    None when no limit is recorded for the convention. This is because the inches-2026 convention
    has very strict software limits for the RSIS motion. Between linear 32.39 and
    34.09 inches the horizontal motion limits are +/-0.5 inch. Beyond
    that band the RSIS should stay within +/-0.25 inch.
    """
    if convention == INCHES_2026:
        return (-0.5, 0.5) if 32.39 <= linear <= 34.09 else (-0.25, 0.25)
    return None


def convention_for_date(when: date) -> str:
    """Which position convention a run taken on this date reports in.
    """
    return INCHES_2026 if when >= INCHES_2026_START else LEGACY


def anchor_pixel_center(holder: str, convention: str, detector: str, slot: str):
    """Returns the physical (x, y) position of a slot's center in this source holder's anchor run,
    or None if that slot was not verified there. If a slot was centered over multiple pixels, its
    center is taken to be the average of the centers of the pixels listed in the anchor.
    """
    pixels = anchor_for(holder, convention)["pixels"].get(detector, {}).get(slot)
    if not pixels:
        return None
    points = [detector_pixel_position(pixel, detector) for pixel in pixels]
    return (sum(x for x, _ in points) / len(points),sum(y for _, y in points) / len(points))


def predict_slot_position(holder: str, convention: str, detector: str,slot: str, linear: float, horizontal: float) -> Optional[tuple]:
    """Returns the predicted physical (x, y) of a slot at a supplied source position (linear + horizontal) for
    a given convention. Returns None when the convention has no anchor for that slot, which should be
    treated as "cannot predict" rather than as a position.
    """
    convention_specs = CONVENTIONS[convention]
    anchor = anchor_for(holder, convention)
    base = anchor_pixel_center(holder, convention, detector, slot)
    if base is None:
        return None
    linear_difference = linear - anchor["linear_position"]
    horizontal_difference = horizontal - anchor["horizontal_position"]
    return (base[0] + convention_specs["hex_per_linear"] * linear_difference, base[1] + convention_specs["hex_per_horizontal"] * horizontal_difference)
