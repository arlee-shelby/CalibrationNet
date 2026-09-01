"""Ingest trap filter output into trap_filter_outputs table. Note, the convention
of this repo (and the scripts) is to output the filter outputs as CSV files. The filter
output file holds one filter pass over one run segment's waveforms, ex:

    pixel,energy
    1052,2910.453880871706
    ...

one row per waveform. All three trap settings (risetime, flat-top, and fall-time) are
encoded in the CSV filename (ex: filter_output_rt1250_ft50_fall1250.csv) by every script
which applies the trap filter (scripts/apply_trap_filter.py and scripts/offline/trap_filter.py).
Note: earlier development-era names lacked the _fall component (they used an assumed value of 1250
(in 4ns bins)) and ingesting one requires an explicitly supplied fall time. But, using the current
repo and scripts does not require this.

A file holds one filter output over one run segment (defined as a period of constant
source position). Runs taken at a single position only have one segment, indexed at 0.
Runs with many positions have many segments, and their waveforms are selected by the
segment's start/end times before filtering.

The repo pipeline deletes each output CSV after successful ingestion,
so a file persists only when an ingest failed and awaits rescue (scripts/ingest_filter_output.py). In the
"offline" version (a database-free pipeline (scripts/offline/)), the files are not deleted. This allows for
use of the pipeline in case the database is down (ex: maintenance periods) and the filter outputs can then be
later ingested when the connection is back up.

Note, not every filter setting belongs in the database: only curated settings are ingested,
each labeled with why it's stored (the label — ex: "nabpy-standard", "short-trap-Fall2025" —
is how analyses can select many at once). This policy exists because a robust "trap filter optimization" scan
can produce ~500 settings per run (to cover the full range of possible settings). The raw energy output arrays
make the trap_filter_outputs table the database's lrgest by far, so the outputs from the whole scan
are not stored, only final "optimized" setting output. But, the label column allows for test-like settings to be
added.

This file contains the ingestion logic, but does not commit the ingestion to the database. That
is done through files in the "scripts" folder.
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
    """Extract trap settings from an output file, and the run and segment numbers if they exist in the
    file name, (ex: Run8622_seg0_singles_filter_output_rt1250_ft50_fall1250.csv).
    Settings are in units of 4 ns time bins (like the DAQ). A legacy name without a
    _fall component parses without a trap_falltime key, but the user must either know the input
    falltime or understand the default value of 1250 was used (ingestion demands an explicit fall time).
    """
    name = Path(path).name
    match = re.search(r"rt(\d+(?:\.\d+)?)_ft(\d+(?:\.\d+)?)", name)
    if not match:
        raise ValueError(
            f"Cannot parse rise time / flat top from filename {name!r} "
            "(expected ex: filter_output_rt100_ft10_fall1250.csv)")
    parsed_filter_settings = {"trap_rise": float(match.group(1)),"trap_flattop": float(match.group(2))}
    fall_match = re.search(r"fall(\d+(?:\.\d+)?)", name)
    if fall_match:
        parsed_filter_settings["trap_falltime"] = float(fall_match.group(1))
    run_match = re.search(r"Run(\d+)", name, re.I)
    if run_match:
        parsed_filter_settings["run_number"] = int(run_match.group(1))
    segment_match = re.search(r"seg(?:ment)?[_-]?(\d+)", name, re.I)
    if segment_match:
        parsed_filter_settings["segment_index"] = int(segment_match.group(1))
    return parsed_filter_settings


def segments_missing_output(session: Session,run_numbers,trap_rise: float,trap_flattop: float,trap_falltime: float,label: Optional[str] = None) -> dict:
    """{run_number: [segment_index, ...]} for segments that do not yet have
    a filter output with these settings. The database is the shared state between
    parallel filter jobs, so this is how any of them (or a person) can tell what is
    left to do. Runs with nothing missing are absent from the result.
    """
    segments_with_filter_outputs = set(session.execute(
        select(RunPixel.run_number, RunPixel.segment_index)
        .join(TrapFilterOutput,
              TrapFilterOutput.run_pixel_id == RunPixel.id)
        .where(RunPixel.run_number.in_(run_numbers),
               TrapFilterOutput.trap_rise == trap_rise,
               TrapFilterOutput.trap_flattop == trap_flattop,
               TrapFilterOutput.trap_falltime == trap_falltime,
               TrapFilterOutput.label == label)
        .distinct()
    ).all())

    missing_segments = defaultdict(list)
    for run_number, segment_index in session.execute(
        select(RunSegment.run_number, RunSegment.segment_index)
        .where(RunSegment.run_number.in_(run_numbers))
        .order_by(RunSegment.run_number, RunSegment.segment_index)
    ).all():
        if (run_number, segment_index) not in segments_with_filter_outputs:
            missing_segments[run_number].append(segment_index)
    return dict(missing_segments)


def read_filter_output(path) -> tuple:
    """Read a filter output CSV into {pixel_number: [energies...]} and return it and
    the number of skipped rows as a tuple. Rows with an empty/NaN energy value
    (pandas writes NaN as an empty field) are skipped and counted rather than stored.
    """
    energies = defaultdict(list)
    skipped_rows = 0
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if [h.strip().lower() for h in header[:2]] != ["pixel", "energy"]:
            raise ValueError(f"Unexpected header {header!r} in {path}")
        for row in reader:
            value = row[1].strip()
            if not value or value.lower() == "nan":
                skipped_rows += 1
                continue
            energies[int(row[0])].append(float(value))
    return dict(energies), skipped_rows


def ingest_filter_output(session: Session,run_number: int,path,trap_falltime: Optional[float] = None,label: Optional[str] = None,segment_index: int = 0) -> list:
    """Ingest one filter output file. The output for each pixel found in the file is
    stored as one TrapFilterOutput, for the given run segment. Any missing run_pixels are
    created, but with only the bare minimum columns added (i.e. the board_channel/source columns
    get filled by later ingest steps). An existing output with the same run_pixel and settings is
    replaced, so re-ingesting a file is idempotent.

    The fall-time value comes from the filename's _fall component. For a legacy name that lacks
    one, pass trap_falltime. If the fall-time is in the filename and is passed, they must agree -
    a mismatch is an error and never a silent pick.
    """
    # validate everything the filename tells us before touching the database, note: run number and segment index are supplied
    filter_settings = parse_filter_filename(path)
    filter_settings.pop("run_number", None)
    filter_settings.pop("segment_index", None)
    filename_fall = filter_settings.get("trap_falltime")
    if (trap_falltime is not None and filename_fall is not None and trap_falltime != filename_fall):
        raise ValueError(
            f"fall time mismatch for {Path(path).name}: the filename "
            f"says fall{filename_fall:g} but the caller supplied "
            f"{trap_falltime:g}")
    if trap_falltime is None and filename_fall is None:
        raise ValueError(
            f"{Path(path).name} has no _fall component (legacy name) "
            "and no fall time was supplied")
    if filename_fall is None:
        filter_settings["trap_falltime"] = trap_falltime

    # ensure the run segment is in the database before proceeding
    segment = session.get(RunSegment, (run_number, segment_index))
    if segment is None:
        raise ValueError(
            f"Run {run_number} segment {segment_index} is not in the "
            "database — ingest the run first (scripts/ingest_run.py), which "
            "derives its segments.")

    energies_per_pixel, skipped_rows = read_filter_output(path)
    if skipped_rows:
        print(f"note: skipped {skipped_rows} empty/NaN energy rows in {path}")
    # pixel 0 is the replay's catch-all for board channels with no pixel physically plugged in;
    # pixel 58 (and 1058) has no electronics
    for junk_pixel in (0, 58, 1058):
        junk = energies_per_pixel.pop(junk_pixel, None)
        if junk is not None:
            print(f"note: skipped pixel {junk_pixel} (junk channels, "
                  f"{len(junk)} waveforms)")

    pixels = {pixel.pixel_number: pixel for pixel in session.scalars(select(Pixel).where(Pixel.pixel_number.in_(energies_per_pixel)))}
    unknown_pixels = sorted(set(energies_per_pixel) - set(pixels))
    if unknown_pixels:
        raise ValueError(f"File references pixels not in the database: "
                         f"{unknown_pixels[:10]}{'...' if len(unknown_pixels) > 10 else ''}")

    run_pixels = {run_pixel.pixel_number: run_pixel for run_pixel in segment.run_pixels}

    filter_outputs = []
    for pixel_number, pixel_energies in sorted(energies_per_pixel.items()):
        pixel = pixels[pixel_number]
        run_pixel = run_pixels.get(pixel_number)
        if run_pixel is None:
            run_pixel = RunPixel(segment=segment, pixel=pixel)
            session.add(run_pixel)
            run_pixels[pixel_number] = run_pixel
        for old_output in [t for t in run_pixel.trap_filter_outputs if (t.trap_rise, t.trap_flattop, t.trap_falltime) == (filter_settings["trap_rise"], filter_settings["trap_flattop"],filter_settings["trap_falltime"])]:
            session.delete(old_output)
        new_output = TrapFilterOutput(run_pixel=run_pixel,energies=pixel_energies,label=label,source_file=Path(path).name,**filter_settings)
        session.add(new_output)
        # flush per pixel so each insert carries one energies array
        # otherwise every pending row is added to a single insert, which can be hundreds of MB and close the server connection mid-send
        # (can happen for segments with a large number of subruns)
        # module does not commit ingestion, so per-file all-or-nothing is unchanged
        session.flush()
        filter_outputs.append(new_output)
    return filter_outputs
