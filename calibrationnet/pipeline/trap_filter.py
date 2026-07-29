"""Ingest trap filter output CSVs into trap_filter_outputs.

A filter output file holds one filter pass over one run's waveforms:

    pixel,energy
    1052,2910.453880871706
    ...

one row per waveform. Rise time and flat top are encoded in the filename
(e.g. filter_output_rt100_ft10.csv); fall time is not, so the caller
supplies it.

A file holds one filter pass over one run SEGMENT — a period of constant
source position. Runs taken at a single position have only segment 0;
rastering runs have dozens, and their waveforms must be selected by the
segment's time range before filtering.

The full optimization scan (~26 rise times x ~20 flat tops per run) stays
on disk — only curated outputs (the per-pixel optimized settings, plus
any comparison settings) are ingested, labeled with why they're stored.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Pixel, RunPixel, RunSegment, TrapFilterOutput


def parse_filter_filename(path) -> dict:
    """Extract trap settings — and the run number if present — from a name
    like Run8622_filter_output_rt1250_ft50.csv. Settings are in units of
    4 ns time bins."""
    name = Path(path).name
    match = re.search(r"rt(\d+(?:\.\d+)?)_ft(\d+(?:\.\d+)?)", name)
    if not match:
        raise ValueError(
            f"Cannot parse rise time / flat top from filename {name!r} "
            "(expected e.g. filter_output_rt100_ft10.csv)"
        )
    parsed = {"trap_rise": float(match.group(1)),
              "trap_flattop": float(match.group(2))}
    run_match = re.search(r"Run(\d+)", name, re.I)
    if run_match:
        parsed["run_number"] = int(run_match.group(1))
    segment_match = re.search(r"seg(?:ment)?[_-]?(\d+)", name, re.I)
    if segment_match:
        parsed["segment_index"] = int(segment_match.group(1))
    return parsed


def read_filter_output(path) -> tuple:
    """Read a filter output CSV into ({pixel_number: [energies...]}, skipped).

    Rows with an empty/NaN energy (pandas writes NaN as an empty field)
    are skipped and counted rather than stored."""
    energies = defaultdict(list)
    skipped = 0
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if [h.strip().lower() for h in header[:2]] != ["pixel", "energy"]:
            raise ValueError(f"Unexpected header {header!r} in {path}")
        for row in reader:
            value = row[1].strip()
            if not value or value.lower() == "nan":
                skipped += 1
                continue
            energies[int(row[0])].append(float(value))
    return dict(energies), skipped


def ingest_filter_output(
    session: Session,
    run_number: int,
    path,
    trap_falltime: float,
    label: Optional[str] = None,
    segment_index: int = 0,
) -> list:
    """Store one filter output file: one TrapFilterOutput per pixel found
    in the file, under the given run segment. Creates missing run_pixels
    (bare — board_channel/source get filled by later ingest steps).
    Replaces any existing output with the same (run_pixel, settings), so
    re-ingesting a file is idempotent. Does not commit."""
    segment = session.get(RunSegment, (run_number, segment_index))
    if segment is None:
        raise ValueError(
            f"Run {run_number} segment {segment_index} is not in the "
            "database — ingest the run first (scripts/ingest_run.py), which "
            "derives its segments."
        )

    settings = parse_filter_filename(path)
    # The caller resolves which run/segment the file belongs to.
    settings.pop("run_number", None)
    settings.pop("segment_index", None)
    settings["trap_falltime"] = trap_falltime
    per_pixel, skipped = read_filter_output(path)
    if skipped:
        print(f"note: skipped {skipped} empty/NaN energy rows in {path}")
    # Pixel 0 is the replay's catch-all for board channels with no pixel
    # physically plugged in; pixel 58 (and 1058) has no electronics, and
    # what the replay attributes to it is an aggregate of junk board
    # channels. None of it is real detector data.
    for junk_pixel in (0, 58, 1058):
        junk = per_pixel.pop(junk_pixel, None)
        if junk is not None:
            print(f"note: skipped pixel {junk_pixel} (junk channels, "
                  f"{len(junk)} waveforms)")

    pixels = {
        p.pixel_number: p
        for p in session.scalars(
            select(Pixel).where(Pixel.pixel_number.in_(per_pixel))
        )
    }
    unknown = sorted(set(per_pixel) - set(pixels))
    if unknown:
        raise ValueError(f"File references pixels not in the database: "
                         f"{unknown[:10]}{'...' if len(unknown) > 10 else ''}")

    run_pixels = {rp.pixel_number: rp for rp in segment.run_pixels}

    outputs = []
    for pixel_number, pixel_energies in sorted(per_pixel.items()):
        pixel = pixels[pixel_number]
        rp = run_pixels.get(pixel_number)
        if rp is None:
            rp = RunPixel(segment=segment, pixel=pixel)
            session.add(rp)
            run_pixels[pixel_number] = rp
        for old in [t for t in rp.trap_filter_outputs
                    if (t.trap_rise, t.trap_flattop, t.trap_falltime)
                    == (settings["trap_rise"], settings["trap_flattop"],
                        settings["trap_falltime"])]:
            session.delete(old)
        output = TrapFilterOutput(
            run_pixel=rp,
            energies=pixel_energies,
            label=label,
            source_file=Path(path).name,
            **settings,
        )
        session.add(output)
        outputs.append(output)
    return outputs
