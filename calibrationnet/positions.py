"""Source-frame position conventions.

A raw position number is only meaningful together with the convention it
was recorded in, so every run_segment stores its convention name:

  "legacy-units"  (before 2026-07-24)
      Positions were only written in the run-description free text.
      Linear in inches; horizontal ("2D") in machine units, with the
      centered position reading about 2.7.

  "inches-2026"   (from 2026-07-24)
      Positions come from the motion-control readback channels
      (calibrationnet.pipeline.motion_control). BOTH axes are in inches,
      and the stage was re-homed, so the centered position now reads 0
      horizontally.

Nothing here assumes any convention's zero is the detector center. Source
assignment only ever uses *displacements*: each convention carries its own
anchor — a run/segment whose slot-to-pixel mapping was verified by eye —
and predicts anchor_pixel + slope * (readback - anchor_readback). A
re-homing therefore only requires a new anchor in the new convention, and
never a conversion between conventions.
"""

from datetime import date
from typing import Optional

from .geometry import X_PITCH, Y_PITCH, physical_position

LEGACY = "legacy-units"
INCHES_2026 = "inches-2026"

# Runs from this date report positions from motion control, in inches.
INCHES_2026_START = date(2026, 7, 24)

# 0.4 inch of stage travel moves the sources one pixel column, and a
# column step is X_PITCH hex units.
HEX_PER_INCH = X_PITCH / 0.4

# Legacy horizontal "units": one unit moved the sources about one pixel
# row (Y_PITCH hex units). The legacy scan spanned 1.7-3.7 units and the
# 2026 scan spans -0.5..+0.5 inch over the same physical range, i.e. about
# half an inch per legacy unit — consistent with Y_PITCH / HEX_PER_INCH.
HEX_PER_LEGACY_UNIT = Y_PITCH

CONVENTIONS = {
    LEGACY: {
        "linear_units": "inch",
        "horizontal_units": "machine units (~0.5 inch each)",
        # Hex units of source motion per unit of readback. Increasing
        # linear position moves the sources +x; increasing horizontal
        # moves them -y (toward pixel 70).
        "hex_per_linear": HEX_PER_INCH,
        "hex_per_horizontal": -HEX_PER_LEGACY_UNIT,
        "anchor": {
            "run_number": 8622,
            "segment_index": 0,
            "linear_position": 34.0,
            "horizontal_position": 2.7,
            # Verified by eye (AS): slot -> center pixel(s), per detector.
            "pixels": {
                "upper": {"R1C2": [106], "R1C3": [109], "R2C1": [60],
                          "R2C2": [76], "R2C3": [67, 80]},
                "lower": {"R1C2": [1019], "R1C3": [1022], "R2C1": [1048],
                          "R2C2": [1052], "R2C3": [1068]},
            },
        },
    },
    INCHES_2026: {
        "linear_units": "inch",
        "horizontal_units": "inch",
        # Both measured from data rather than assumed. Linear: fitted over
        # run 9370's 2.73 inch span (rms 0.94 hex), which gives ~0.45 inch
        # per pixel column rather than the 0.4 inch previously assumed.
        # Horizontal: from runs 9326/9327, which sit at the SAME linear
        # position 0.25 inch apart, so they isolate this axis with no
        # linear confound — the frame moved 0.98 hex in -y, confirming the
        # sign did not flip when the stage was re-homed.
        #
        # Kept as one scale per axis on purpose: the stage moves linear and
        # horizontal independently, so any diagonal component in the hit
        # pattern is magnetic-field distortion, not motion, and does not
        # belong in this mapping.
        "hex_per_linear": 3.35,
        "hex_per_horizontal": -3.90,
        "anchor": {
            "run_number": 9326,
            "segment_index": 0,
            "linear_position": 33.502,
            "horizontal_position": -0.249,
            # Identified by eye (AS, 2026-07-30) from run 9326's hit maps.
            # R1C1 (Bi-207-8890) is deliberately absent: it sits off the
            # detector face at this position, so it is extrapolated from
            # the grid the other five slots define rather than guessed.
            "pixels": {
                "upper": {"R1C2": [97], "R1C3": [101],
                          "R2C1": [59], "R2C2": [50], "R2C3": [67]},
                "lower": {"R1C2": [1019], "R1C3": [1032],
                          "R2C1": [1059], "R2C2": [1076], "R2C3": [1079]},
            },
        },
    },
}


def convention_for_date(when: date) -> str:
    """Which position convention a run taken on this date reports in."""
    return INCHES_2026 if when >= INCHES_2026_START else LEGACY


def anchor_pixel_center(convention: str, detector: str, slot: str):
    """Physical (x, y) of a slot in the convention's anchor segment, or
    None if that slot was not verified."""
    anchor = CONVENTIONS[convention]["anchor"]
    if anchor is None:
        return None
    pixels = anchor["pixels"].get(detector, {}).get(slot)
    if not pixels:
        return None
    points = [physical_position(p, detector) for p in pixels]
    return (sum(x for x, _ in points) / len(points),
            sum(y for _, y in points) / len(points))


def predict_slot_position(convention: str, detector: str, slot: str,
                          linear: float, horizontal: float) -> Optional[tuple]:
    """Predicted physical (x, y) of a slot at the given readback.

    Purely differential from the convention's own anchor, so it is immune
    to re-homing and to the units differing between conventions. Returns
    None when the convention has no anchor for that slot, which the caller
    should treat as "cannot predict" rather than as a position.
    """
    spec = CONVENTIONS[convention]
    anchor = spec["anchor"]
    base = anchor_pixel_center(convention, detector, slot)
    if base is None:
        return None
    d_linear = linear - anchor["linear_position"]
    d_horizontal = horizontal - anchor["horizontal_position"]
    return (base[0] + spec["hex_per_linear"] * d_linear,
            base[1] + spec["hex_per_horizontal"] * d_horizontal)
