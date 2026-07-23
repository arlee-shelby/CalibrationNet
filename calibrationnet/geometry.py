"""Detector pixel geometry, replicating nabPy's nabPlot.detectorFigure
(and the collaboration C++ SetupHexPlot): 127 pixels in 13 columns
(starts 1,8,16,25,35,46,58,71,83,94,104,113,121), numbered top-to-bottom
within a column. Coordinates and hexagon parameters match nabPy exactly:
column spacing 1.5*size, vertical pitch sqrt(3)*size, flat-top hexagons
(orientation pi/2, radius=size).

Lower-detector pixels are the same positions numbered +1000; physically
the detectors face each other, so the lower detector is mirrored
left-right relative to the upper when both are viewed from the same side.
"""

import math

COL_START = [1, 8, 16, 25, 35, 46, 58, 71, 83, 94, 104, 113, 121]
COL_END = [7, 15, 24, 34, 45, 57, 70, 82, 93, 103, 112, 120, 127]
NUM_COL = len(COL_START)

HEX_RADIUS = 1.0            # nabPy "size"
HEX_ORIENTATION = math.pi / 2  # flat-top
X_PITCH = 1.5 * HEX_RADIUS
Y_PITCH = math.sqrt(3) * HEX_RADIUS


def pixel_positions() -> dict:
    """{pixel_number 1..127: (x, y)}, identical to nabPy's
    detectorFigure._generateCoordinates with size=1 (+y up)."""
    positions = {}
    for col, (start, end) in enumerate(zip(COL_START, COL_END)):
        x = (col - NUM_COL / 2) * X_PITCH
        length = end - start + 1
        for i, pixel in enumerate(range(start, end + 1)):
            positions[pixel] = (x, (length - 1) * Y_PITCH / 2 - Y_PITCH * i)
    return positions


def mirrored_x(x: float) -> float:
    """Mirror a hit-map x about the detector center (lower vs upper view)."""
    x_left = (0 - NUM_COL / 2) * X_PITCH
    x_right = (NUM_COL - 1 - NUM_COL / 2) * X_PITCH
    return (x_left + x_right) - x


def neighbors(pixel: int) -> set:
    """Pixel numbers (1-127) adjacent to the given pixel on the hex grid."""
    pos = pixel_positions()
    x0, y0 = pos[pixel]
    limit = 2.0  # adjacent centers sit at sqrt(3); next-nearest at 3.0
    return {
        p for p, (x, y) in pos.items()
        if p != pixel and math.hypot(x - x0, y - y0) < limit
    }
