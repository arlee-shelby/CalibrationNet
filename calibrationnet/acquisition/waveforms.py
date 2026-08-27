"""Apply the trapezoidal filter to raw waveforms, one run segment at a time.

This is the only module that needs nabPy (and therefore the h5/MPI stack),
so nothing else in the package imports it. Point NABPY_PATH at a pyNab
checkout's src/ directory if nabPy is not already importable.

Waveform selection is by wall-clock time: nabPy's header column
'unix timestamp' counts 4 ns ticks since the epoch, so dividing by
TICKS_PER_SECOND gives ordinary unix seconds. A segment's dwell window is
applied as a mask on that column, which is what keeps one segment's
energies free of any waveform taken at another source position.
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# nabPy's 'unix timestamp' is in 4 ns ticks since the unix epoch.
TICKS_PER_SECOND = 2.5e8

# nabPy accessor for each kind of wave.
WAVE_ACCESSORS = {"singles": "singleWaves", "pulsers": "pulsrWaves"}


def import_nabpy():
    """Import nabPy, honouring NABPY_PATH for a source checkout."""
    path = os.environ.get("NABPY_PATH")
    if path and path not in sys.path:
        sys.path.insert(0, path)
    import nabPy
    return nabPy


def to_ticks(when) -> float:
    """A datetime -> nabPy 'unix timestamp' ticks."""
    return when.timestamp() * TICKS_PER_SECOND


def subrun_file(directory, run_number: int, subrun: int) -> Path:
    return Path(directory) / f"Run{run_number}_{subrun}.h5"


def _headers(nab, path: Path, wave_type: str):
    waves = getattr(nab.File(str(path)), WAVE_ACCESSORS[wave_type])()
    return waves, waves.headers()


def available_subruns(directory, run_number: int) -> list:
    """Sorted subrun indices that actually have a file in this directory."""
    pattern = re.compile(rf"Run{run_number}_(\d+)\.h5$")
    found = []
    for path in Path(directory).glob(f"Run{run_number}_*.h5"):
        match = pattern.match(path.name)
        if match:
            found.append(int(match.group(1)))
    return sorted(found)


def subrun_span(directory, run_number: int, subrun: int,
                wave_type: str = "singles"):
    """(first, last) 'unix timestamp' ticks in a subrun, or None if the file
    is missing or holds no waves of this type."""
    path = subrun_file(directory, run_number, subrun)
    if not path.exists():
        print(f"note: {path} does not exist")
        return None
    nab = import_nabpy()
    try:
        _, headers = _headers(nab, path, wave_type)
    except Exception as exc:                       # unreadable/empty subrun
        print(f"note: could not read headers from {path.name}: {exc}")
        return None
    if len(headers) == 0:
        print(f"note: {path.name} holds no '{wave_type}' waves")
        return None
    stamps = headers["unix timestamp"]
    return float(stamps.min()), float(stamps.max())


def find_subruns(directory, run_number: int,
                 start_ticks: float, end_ticks: float,
                 wave_type: str = "singles") -> list:
    """The subruns that can hold waveforms inside [start_ticks, end_ticks].

    Subruns are written in time order, so this binary searches on each
    candidate's FIRST timestamp: a dozen header reads instead of opening
    all 600-odd files. One extra subrun is included at each end, since
    segment_energies masks by timestamp anyway and over-including is free
    of correctness risk while under-including would silently lose data.

    Raises FileNotFoundError if the directory holds no files for this run —
    which is otherwise indistinguishable from a genuine lack of overlap.
    """
    subruns = available_subruns(directory, run_number)
    if not subruns:
        raise FileNotFoundError(
            f"no Run{run_number}_*.h5 files in {directory} — check the "
            "directory (-d) and that the run's data has been replayed there."
        )

    cache = {}

    def first_stamp(subrun):
        if subrun not in cache:
            limits = subrun_span(directory, run_number, subrun, wave_type)
            cache[subrun] = None if limits is None else limits[0]
        return cache[subrun]

    def last_at_or_before(target):
        """Index into `subruns` of the last one starting at/before target."""
        low, high, found = 0, len(subruns) - 1, -1
        while low <= high:
            mid = (low + high) // 2
            stamp = first_stamp(subruns[mid])
            if stamp is None:                      # unreadable: step past it
                low = mid + 1
                continue
            if stamp <= target:
                found, low = mid, mid + 1
            else:
                high = mid - 1
        return found

    # The subrun containing the window's start is the last one that began
    # at or before it; everything up to the last one beginning before the
    # window's end can contribute.
    first_index = max(last_at_or_before(start_ticks), 0)
    last_index = last_at_or_before(end_ticks)
    if last_index < 0:
        readable = [s for s in subruns if first_stamp(s) is not None]
        raise ValueError(
            f"every subrun of run {run_number} starts after this segment's "
            f"window ends — {len(readable)} readable of {len(subruns)} files. "
            "Check that the segment times and the waveform timestamps use "
            "the same clock."
        )

    lo = max(first_index - 1, 0)
    hi = min(last_index + 1, len(subruns) - 1)
    return subruns[lo:hi + 1]


def segment_energies(directory, run_number: int, subruns,
                     risetime: int, flattop: int, falltime: int,
                     wave_type: str = "singles",
                     window: tuple = None) -> dict:
    """{pixel_number: [energy, ...]} for the given subruns.

    window is an optional (start_ticks, end_ticks) pair; waveforms outside
    it are dropped, which is how a segment is separated from its
    neighbours when the source moved mid-subrun. Trap settings are in 4 ns
    time bins, as everywhere else in this project.
    """
    nab = import_nabpy()
    per_pixel = defaultdict(list)

    for subrun in subruns:
        path = subrun_file(directory, run_number, subrun)
        if not path.exists():
            print(f"note: {path.name} missing, skipped")
            continue
        waves, headers = _headers(nab, path, wave_type)
        if len(headers) == 0:
            continue

        mask = None
        if window is not None:
            stamps = headers["unix timestamp"]
            mask = ((stamps >= window[0]) & (stamps <= window[1])).values
            if not mask.any():
                continue                       # subrun lies outside the dwell

        # Filter the whole subrun and cut the ENERGIES, rather than boolean-
        # indexing the waveforms: waves() is a lazy ~7.6 GB dask array per
        # subrun, and masking it would force an expensive rechunk, whereas
        # masking the resulting energies is free. Only the two subruns at a
        # segment's edges have anything masked off at all.
        energies, _ = nab.bf.applyTrapFilter(
            waves.waves(), risetime, flattop, falltime, useGPU=False
        )
        if hasattr(energies, "compute"):
            energies = energies.compute()

        pixels = headers["pixel"].values
        if mask is not None:
            energies, pixels = energies[mask], pixels[mask]

        for pixel, energy in zip(pixels, energies):
            per_pixel[int(pixel)].append(float(energy))

    return dict(per_pixel)


def save_filter_output(per_pixel: dict, path) -> Path:
    """Write {pixel: [energies]} as the pixel,energy CSV that
    pipeline.trap_filter ingests. Written before ingesting so a failed
    ingest can be retried without redoing the filtering."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("pixel,energy\n")
        for pixel in sorted(per_pixel):
            for energy in per_pixel[pixel]:
                f.write(f"{pixel},{energy}\n")
    return path
