"""Ingest the board-channel -> pixel map from a run's raw HDF5 data file into
run_pixels.board_channel. Since the board-channel -> pixel map can change
frequently (more than the pixel -> preamp/FET maps), they are derived from the raw
data files and specific to a unique run + pixel.

The map is in Parameters/BoardChannelToPixelMap of the HDF5 files and represented as tuples
(board_channel, pixel_number). The map doesn't change within a run, so any subrun file can be
used to obtain it. Note, reading the map only requires h5py (not nabPy).

This file contains the ingestion logic, but does not commit the ingestion to the database. That
is done through files in the "scripts" folder.

Notes:
- pixel 0 is the catch-all for board channels with nothing plugged in,
- all-zero rows are padding
- a pixel mapped from several board channels (ex: pixel 58, which has no electronics) is
  ambiguous and is skipped, but reported.
"""

from collections import defaultdict

import h5py
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Pixel, Run, RunPixel

BC_MAP_DATASET = "Parameters/BoardChannelToPixelMap"


def clean_bc_pairs(pairs) -> dict:
    """{pixel_number: board_channel} from raw (bc, pixel) rows, dropping
    pixel 0, padding rows, and ambiguous multi-BC pixels (reported).
    """
    candidates = defaultdict(set)
    for board_channel, pixel_number in pairs:
        if pixel_number == 0:  # unplugged catch-all / padding
            continue
        candidates[int(pixel_number)].add(int(board_channel))

    board_channel_map = {}
    for pixel_number, board_channels in sorted(candidates.items()):
        if len(board_channels) > 1:
            print(f"note: pixel {pixel_number} maps from multiple board "
                  f"channels {sorted(board_channels)} — skipped as ambiguous")
            continue
        board_channel_map[pixel_number] = board_channels.pop()
    return board_channel_map


def read_bc_map(h5_path) -> dict:
    """Obtain and clean board-channel map straight from a run data file.
    """
    with h5py.File(h5_path, "r") as f:
        rows = f[BC_MAP_DATASET][()]
    return clean_bc_pairs(rows)


def apply_bc_map(session: Session, run_number: int, board_channel_map: dict) -> int:
    """Add a run's board-channel-pixel map to run_pixels from the cleaned map (creating missing
    run_pixels which are not in the database but are in the map). The board-channel-pixel map is a
    property of the run, not of a source position, so it is written to every segment of the run.
    """
    run = session.execute(select(Run).where(Run.run_number == run_number)).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Run {run_number} is not in the database — "
                         "ingest it first (scripts/ingest_run.py).")
    if not run.segments:
        raise ValueError(f"Run {run_number} has no segments — re-ingest it "
                         "(scripts/ingest_run.py) to derive them.")

    pixels = {pixel.pixel_number: pixel for pixel in session.scalars(select(Pixel).where(Pixel.pixel_number.in_(board_channel_map)))}

    # determine pixel in board_channel_map not found in database
    unknown_pixels = sorted(set(board_channel_map) - set(pixels))
    if unknown_pixels:
        raise ValueError(f"Data file maps pixels not in the database: "
                         f"{unknown_pixels}")

    for segment in run.segments:
        existing_run_pixels = {run_pixel.pixel_number: run_pixel for run_pixel in segment.run_pixels}
        for pixel_number, board_channel in board_channel_map.items():
            run_pixel = existing_run_pixels.get(pixel_number)
            if run_pixel is None:
                run_pixel = RunPixel(segment=segment, pixel=pixels[pixel_number])
                session.add(run_pixel)
            run_pixel.board_channel = board_channel
    return len(board_channel_map)


def ingest_board_channels(session: Session, run_number: int, h5_path) -> int:
    """Ingest a run's cleaned board-channel-pixel map into the run's run_pixels table.
    Does not commit the ingestion. Returns the number of pixels mapped (used when ingestion is committed).
    """
    return apply_bc_map(session, run_number, read_bc_map(h5_path))
