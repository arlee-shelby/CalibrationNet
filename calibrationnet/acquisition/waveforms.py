"""Apply the trapezoidal filter to raw waveforms for one run segment at a time.

This is the only module that needs nabPy (and therefore the h5/MPI stack),
so nothing else in the package imports it. Point NABPY_PATH to the pyNab
checkout src/ directory if nabPy is not already importable.

Waveform selection is from time, thus keeping a run segment's energies (derived from the waveforms)
separate. The nabPy singleWaves header column has 'unix timestamp' which counts in 4ns ticks since
the unix epoch, so dividing by TICKS_PER_SECOND gives ordinary unix seconds which can be
converted to a datetime and compared to the datetimes stored in the RunSegment table.
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# 'unix timestamp' in nabPy is in 4 ns ticks since the unix start time
TICKS_PER_SECOND = 2.5e8

# nabPy accessor for each kind of wave potentially of interest to this repo
WAVE_ACCESSORS = {"singles": "singleWaves", "pulsers": "pulsrWaves"}


def import_nabpy():
    """Import nabPy, honouring NABPY_PATH for those who have installed nabPy and would like
    to specify the location to that installation.
    """
    path = os.environ.get("NABPY_PATH")
    if path and path not in sys.path:
        sys.path.insert(0, path)
    import nabPy
    return nabPy


def to_ticks(when) -> float:
    """Convert a datetime to 'unix timestamp' ticks (which comes from nabPy). This function is
    used to convert datetimes, as stored in the RunSegment table, to the relevant time unit to select
    waveforms.
    """
    return when.timestamp() * TICKS_PER_SECOND


def subrun_file(directory, run_number: int, subrun: int) -> Path:
    """Returns the subrun file path for the specified run and subrun number.
    """
    return Path(directory) / f"Run{run_number}_{subrun}.h5"


def _waves_and_headers(nab, path: Path, wave_type: str):
    """Obtain the waves and headers for the wave type specified. This uses the nabPy "File" class
    which is specifically optimized to parse one file.
    """
    waves = getattr(nab.File(str(path)), WAVE_ACCESSORS[wave_type])()
    return waves, waves.headers()


def available_subruns(directory, run_number: int) -> list:
    """Return sorted subrun indices that actually have a file in the directory specified.
    """
    pattern = re.compile(rf"Run{run_number}_(\d+)\.h5$")
    subruns = []
    for path in Path(directory).glob(f"Run{run_number}_*.h5"):
        match = pattern.match(path.name)
        if match:
            subruns.append(int(match.group(1)))
    return sorted(subruns)


def subrun_timespan(directory, run_number: int, subrun: int,wave_type: str = "singles"):
    """ Returns a tuple of the first and last 'unix timestamp' ticks in a subrun,
    or None if the file is missing or holds no waves of the specified type.
    """
    path = subrun_file(directory, run_number, subrun)
    if not path.exists():
        print(f"note: {path} does not exist")
        return None
    nab = import_nabpy()
    try:
        _, headers = _waves_and_headers(nab, path, wave_type)
    except Exception as e: # unreadable/empty subrun
        print(f"note: could not read headers from {path.name}: {e}")
        return None
    if len(headers) == 0:
        print(f"note: {path.name} holds no '{wave_type}' waves")
        return None
    unix_timestamps = headers["unix timestamp"]
    return float(unix_timestamps.min()), float(unix_timestamps.max())


def find_segment_subruns(directory,run_number: int,start_time: float,end_time: float,wave_type: str = "singles") -> list:
    """Returns the subruns that could potentially hold waveforms inside a given time range.
    Subruns are written in time order, so this function does a binary search using each
    candidate subrun's first timestamp. This reduces the number of header reads significantly, especially
    for runs with a large number of subruns. One extra subrun is included at each end to allow masks
    to be applied to full subruns in order to obtain the actual waveforms for each segment. Over-including
    subruns is free of risk while under-including would silently lose data.

    Raises FileNotFoundError if the directory holds no files for this run,
    which is otherwise indistinguishable from a genuine lack of overlap.
    """
    subruns = available_subruns(directory, run_number)
    if not subruns:
        raise FileNotFoundError(
            f"no Run{run_number}_*.h5 files in {directory} — check the "
            "directory (-d) and that the run's data has been replayed there.")

    subrun_start_timestamps = {}

    def start_timestamp(subrun):
        """Add a subrun's first (i.e. starting) timestamp, if found, to "subrun_start_timestamps".
        """
        if subrun not in subrun_start_timestamps:
            limits = subrun_timespan(directory, run_number, subrun, wave_type)
            subrun_start_timestamps[subrun] = None if limits is None else limits[0]
        return subrun_start_timestamps[subrun]

    def last_subrun_at_or_before_time(target):
        """Returns the index of the last subrun whose first timestamp is at or before the target time.
        """
        low_subrun_index, high_subrun_index, found_subrun = 0, len(subruns) - 1, -1
        while low_subrun_index <= high_subrun_index:
            midpoint_index = (low_subrun_index + high_subrun_index) // 2
            timestamp = start_timestamp(subruns[midpoint_index])
            if timestamp is None:
                # first timestamp of subrun is unreadable, step to the next subrun increasing the index
                low_subrun_index = midpoint_index + 1
                continue
            if timestamp <= target:
                found_subrun, low_subrun_index = midpoint_index, midpoint_index + 1
            else:
                high_subrun_index = midpoint_index - 1
        return found_subrun

    first_index = max(last_subrun_at_or_before_time(start_time), 0)
    last_index = last_subrun_at_or_before_time(end_time)
    if last_index < 0:
        readable = [subrun for subrun in subruns if start_timestamp(subrun) is not None]
        raise ValueError(
            f"every subrun of run {run_number} starts after the target end time "
            f"— {len(readable)} readable of {len(subruns)} files. "
            "Check that the segment times and the waveform timestamps use "
            "the same clock.")

    # extra subrun included at each end
    low = max(first_index - 1, 0)
    high = min(last_index + 1, len(subruns) - 1)
    return subruns[low:high + 1]


def segment_energies(directory,run_number: int,subruns,risetime: int,flattop: int,falltime: int,wave_type: str = "singles",window: tuple = None) -> dict:
    """Returns {pixel_number: [energy, ...]} for the given subruns which is to be added
    to an output CSV file and ingested as a trap filter output.

    The segment window is an optional (start_time, end_time) pair. Waveforms outside
    of it are dropped, which is how a segment is separated from its
    neighbours when the source moved mid-subrun. If the window is not passed, all the waveforms
    for all the subruns passed in are included, i.e. the waveforms are not separated by time for a
    subrun where the source position moved within it, instead they are all included. Effectively this means
    without the window, the subruns passed in are assumed to be within a single segment.
    The trap filter settings are in 4 ns time bins (like the DAQ and everywhere else in this project).
    """
    nab = import_nabpy()
    energies_per_pixel = defaultdict(list)

    for subrun in subruns:
        path = subrun_file(directory, run_number, subrun)
        if not path.exists():
            print(f"note: {path.name} missing, skipped")
            continue
        waves, headers = _waves_and_headers(nab, path, wave_type)
        if len(headers) == 0:
            continue

        mask = None
        if window is not None:
            timestamps = headers["unix timestamp"]
            mask = ((timestamps >= window[0]) & (timestamps <= window[1])).values
            if not mask.any():
                # subrun lies outside the window
                continue

        # apply the trap filter to the entire subrun and use a mask on the output energies to separate out waveforms
        # (only two subruns at a segment's end are masked), see docs/cluster_resources.md for rationale
        energies, _ = nab.bf.applyTrapFilter(waves.waves(), risetime, flattop, falltime, useGPU=False)
        if hasattr(energies, "compute"):
            energies = energies.compute()

        pixels = headers["pixel"].values
        if mask is not None:
            energies, pixels = energies[mask], pixels[mask]

        for pixel, energy in zip(pixels, energies):
            energies_per_pixel[int(pixel)].append(float(energy))

    return dict(energies_per_pixel)


def save_filter_output(energies_per_pixel: dict, path) -> Path:
    """Write {pixel: [energies]} as the pixel,energy CSV that
    acquisition.trap_filter ingests. Written before ingesting so a failed
    ingest can be retried without redoing the filtering.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("pixel,energy\n")
        for pixel in sorted(energies_per_pixel):
            for energy in energies_per_pixel[pixel]:
                f.write(f"{pixel},{energy}\n")
    return path
