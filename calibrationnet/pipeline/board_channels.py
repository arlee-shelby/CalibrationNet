"""Ingest the board-channel -> pixel map from a run's HDF5 data file into
run_pixels.board_channel.

The map lives in Parameters/BoardChannelToPixelMap (rows of [board_channel,
pixel_number]) and does not change within a run, so any subrun file works.
Reading it needs only h5py, not nabPy.

Junk rows in the map: pixel 0 is the catch-all for board channels with
nothing plugged in, all-zero rows are padding, and a pixel mapped from
several board channels (seen for pixel 58, which has no electronics) is
ambiguous — all are skipped, with a report.
"""

from collections import defaultdict

import h5py
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Pixel, Run, RunPixel

BC_MAP_DATASET = "Parameters/BoardChannelToPixelMap"


def read_bc_map(h5_path) -> dict:
    """Return {pixel_number: board_channel} from a run data file, dropping
    pixel 0, padding rows, and ambiguous multi-BC pixels (reported)."""
    with h5py.File(h5_path, "r") as f:
        rows = f[BC_MAP_DATASET][()]

    candidates = defaultdict(set)
    for board_channel, pixel_number in rows:
        if pixel_number == 0:  # unplugged catch-all / padding
            continue
        candidates[int(pixel_number)].add(int(board_channel))

    bc_map = {}
    for pixel_number, channels in sorted(candidates.items()):
        if len(channels) > 1:
            print(f"note: pixel {pixel_number} maps from multiple board "
                  f"channels {sorted(channels)} — skipped as ambiguous")
            continue
        bc_map[pixel_number] = channels.pop()
    return bc_map


def ingest_board_channels(session: Session, run_number: int, h5_path) -> int:
    """Set board_channel on the run's run_pixels from the data file's map,
    creating missing run_pixels. Returns the number of pixels mapped.
    Does not commit."""
    run = session.execute(
        select(Run).where(Run.run_number == run_number)
    ).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Run {run_number} is not in the database — "
                         "ingest it first (scripts/ingest_run.py).")

    bc_map = read_bc_map(h5_path)

    pixels = {
        p.pixel_number: p
        for p in session.scalars(
            select(Pixel).where(Pixel.pixel_number.in_(bc_map))
        )
    }
    unknown = sorted(set(bc_map) - set(pixels))
    if unknown:
        raise ValueError(f"Data file maps pixels not in the database: "
                         f"{unknown}")

    run_pixels = {
        rp.pixel_id: rp
        for rp in session.scalars(
            select(RunPixel).where(RunPixel.run_id == run.id)
        )
    }
    for pixel_number, board_channel in bc_map.items():
        pixel = pixels[pixel_number]
        rp = run_pixels.get(pixel.id)
        if rp is None:
            rp = RunPixel(run=run, pixel=pixel)
            session.add(rp)
            run_pixels[pixel.id] = rp
        rp.board_channel = board_channel
    return len(bc_map)
