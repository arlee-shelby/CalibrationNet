"""Detector pixel geometry for plotting, replicating nabPy's nabPlot.detectorFigure
so that plots can be made without importing nabPy. This was developed from
the NabSim SetupHexPlot and nabPy. There are 127 pixels in 13 columns
(starts 1,8,16,25,35,46,58,71,83,94,104,113,121), numbered top-to-bottom
within a column. Coordinates and hexagon parameters match nabPy exactly:
column spacing 1.5*size, vertical pitch sqrt(3)*size, flat-top hexagons
(orientation pi/2, radius=size).

WARNING: as in nabPy, x = 0 is not the detector center, it is offset by
half a column to the left (i.e. -0.5) instead.

Lower-detector pixels are the same positions numbered +1000. Physically
the detectors face each other, so the lower detector is mirrored
left-right relative to the upper when both are viewed from the same side.
"""

import math

COLUMN_START = [1, 8, 16, 25, 35, 46, 58, 71, 83, 94, 104, 113, 121]
COLUMN_END = [7, 15, 24, 34, 45, 57, 70, 82, 93, 103, 112, 120, 127]
NUMBER_COLUMNS = len(COLUMN_START)

HEX_RADIUS = 1.0                # nabPy "size"
HEX_ORIENTATION = math.pi / 2   # flat-top
HORIZONTAL_OFFSET = 1.5 * HEX_RADIUS
VERTICAL_OFFSET = math.sqrt(3) * HEX_RADIUS


def pixel_positions() -> dict:
    """{pixel_number 1..127: (x, y)}, identical to nabPy's detectorFigure._generateCoordinates
    with size=1 (+y is up). Note, as in nabPy, the (x,y) grid is offset half a column left of x=0.
    """
    positions = {}
    for column, (start, end) in enumerate(zip(COLUMN_START, COLUMN_END)):
        x = (column - NUMBER_COLUMNS / 2) * HORIZONTAL_OFFSET
        length = end - start + 1
        for i, pixel in enumerate(range(start, end + 1)):
            positions[pixel] = (x, (length - 1) * VERTICAL_OFFSET / 2 - VERTICAL_OFFSET * i)
    return positions


def mirrored_x(x: float) -> float:
    """Mirror a hit-map x position about the detector center (lower vs upper detector views).
    Because the grid is offset half a column to the left of x=0, the mirrored
    coordinate is calculated (in column units, multiply by HORIZONTAL_OFFSET to get hex units)
    by x -> 2*(-0.5) - x (since the center is offset by -0.5).
    """
    x_left = (0 - NUMBER_COLUMNS / 2) * HORIZONTAL_OFFSET
    x_right = (NUMBER_COLUMNS - 1 - NUMBER_COLUMNS / 2) * HORIZONTAL_OFFSET
    return (x_left + x_right) - x


# built once at import, detector_pixel_positions returns these when called
_UPPER = pixel_positions()
_LOWER = {pixel: (mirrored_x(x), y) for pixel, (x, y) in _UPPER.items()}


def detector_pixel_position(pixel_number: int, detector: str) -> tuple:
    """Physical (x, y) of a pixel, in coordinates shared by both detectors,
    i.e. where the lower detector is mirrored from the upper one, which is
    a property of the physical orientation of the installed detectors. Accepts
    either detector's numbering (1-127 or 1001-1127).
    """
    return (_LOWER if detector == "lower" else _UPPER)[pixel_number % 1000]


def neighbors(pixel: int) -> set:
    """Pixel numbers (1-127) adjacent to the given pixel on the hex grid.
    """
    position = pixel_positions()
    x0, y0 = position[pixel]
    limit = 2.0  # adjacent centers sit at sqrt(3); next-nearest at 3.0
    return {neighbor for neighbor, (x, y) in position.items() if neighbor != pixel and math.hypot(x - x0, y - y0) < limit}


def ring(pixel: int, n: int) -> set:
    """Pixel numbers (1-127) exactly n hex steps from the input pixel, i.e. its n-th
    ring. Ring 1 of a pixel is its (up to six) touching neighbors, ring 2 the twelve
    around those, and so on. Around the center pixel (64) the rings are full and tile
    the whole detector. Accepts either detector's numbering. n=0 is the pixel itself.
    """
    current_ring = {pixel % 1000}
    visited = {pixel % 1000}
    for _ in range(n):
        next_ring = set()
        for ring_pixel in current_ring:
            for neighbor in neighbors(ring_pixel):
                if neighbor not in visited:
                    next_ring.add(neighbor)
        visited = visited | next_ring
        current_ring = next_ring
    return current_ring


def ring_number(pixel: int, center: int = 64) -> int:
    """How many hex steps from the supplied "center" pixel (default pixel 64)
    to the target supplied "pixel". Using the default center (pixel 64) this returns
    the ring index of a pixel in the detector-wide convention. Note, pixel 64 is ring
    0, the outer edge is ring 6.
    """
    target = pixel % 1000
    current_ring = {center % 1000}
    visited = {center % 1000}
    steps = 0
    while target not in current_ring:
        next_ring = set()
        for ring_pixel in current_ring:
            for neighbor in neighbors(ring_pixel):
                if neighbor not in visited:
                    next_ring.add(neighbor)
        visited = visited | next_ring
        if not next_ring:
            raise ValueError(f"pixel {pixel} is not on the detector grid")
        current_ring = next_ring
        steps += 1
    return steps
