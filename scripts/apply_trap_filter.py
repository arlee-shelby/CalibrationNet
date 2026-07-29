"""Apply the trap filter to ONE run segment, ingest the result, delete the CSV.

Designed as the body of a SLURM array task (one task per segment), so it
is self-contained: it looks up its own segment's dwell window and its
run's subrun count, works out which subruns overlap that window, filters
only those waveforms, ingests the energies, and removes the intermediate
CSV — the raw .h5 files remain the archive, so keeping filter output on
disk would only waste storage.

    python scripts/apply_trap_filter.py -d /path/to/h5/ -r 9369 -s 12
    python scripts/apply_trap_filter.py -d /path/to/h5/ -r 8622   # segment 0

Trap settings are in 4 ns time bins; the nabPy standard is 1250/50/1250.
"""

import argparse
import time
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.models import RunSegment
from calibrationnet.pipeline.trap_filter import ingest_filter_output
from calibrationnet.pipeline.waveforms import (
    find_subrun_range,
    save_filter_output,
    segment_energies,
    to_ticks,
)

DEFAULT_OUTPUT_DIR = Path("data/TrapFilterData")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-d", "--directory", required=True,
                        help="directory holding RunNNNN_S.h5 files")
    parser.add_argument("-r", "--run", type=int, required=True)
    parser.add_argument("-s", "--segment", type=int, default=0,
                        help="segment index within the run (default 0)")
    parser.add_argument("-rt", "--risetime", type=int, default=1250)
    parser.add_argument("-ft", "--flattop", type=int, default=50)
    parser.add_argument("-fall", "--falltime", type=int, default=1250)
    parser.add_argument("-w", "--wave", default="singles",
                        choices=["singles", "pulsers"])
    parser.add_argument("--label", default="nabpy-standard",
                        help="why this output is stored")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--keep-csv", action="store_true",
                        help="do not delete the CSV after ingesting")
    args = parser.parse_args()

    started = time.perf_counter()

    # 1. What this task is responsible for.
    with get_session() as session:
        segment = session.get(RunSegment, (args.run, args.segment))
        if segment is None:
            raise SystemExit(
                f"run {args.run} segment {args.segment} is not in the "
                "database — ingest the run first (scripts/ingest_run.py)."
            )
        n_subruns = segment.run.number_subruns
        n_segments = len(segment.run.segments)
        window = (to_ticks(segment.start_time), to_ticks(segment.end_time))
        print(f"run {args.run} segment {args.segment}/{n_segments - 1}: "
              f"{segment.start_time:%Y-%m-%d %H:%M} -> "
              f"{segment.end_time:%H:%M}, "
              f"position {segment.linear_position}/"
              f"{segment.horizontal_position}")

    if not n_subruns:
        raise SystemExit(f"run {args.run} has no recorded subrun count.")

    # 2. Only the subruns overlapping this segment's dwell. A single-segment
    #    run covers everything, so skip the search and take them all.
    if n_segments == 1:
        subruns = range(n_subruns)
        mask_window = None
    else:
        subruns = find_subrun_range(args.directory, args.run, n_subruns,
                                    *window, wave_type=args.wave)
        mask_window = window
        if not subruns:
            raise SystemExit(
                f"no subrun of run {args.run} overlaps segment "
                f"{args.segment}'s window — nothing to do."
            )
    print(f"subruns {subruns.start}-{subruns.stop - 1} of 0-{n_subruns - 1}")

    # 3. Filter.
    per_pixel = segment_energies(
        args.directory, args.run, subruns,
        args.risetime, args.flattop, args.falltime,
        wave_type=args.wave, window=mask_window,
    )
    if not per_pixel:
        raise SystemExit("no waveforms selected — nothing to ingest.")
    total = sum(len(v) for v in per_pixel.values())
    print(f"filtered {total} waveforms over {len(per_pixel)} pixels "
          f"in {time.perf_counter() - started:.0f}s")

    # 4. Store, then ingest, then drop the intermediate file.
    csv_path = (args.out / f"Run{args.run}" /
                f"Run{args.run}_seg{args.segment}_{args.wave}"
                f"_filter_output_rt{args.risetime}_ft{args.flattop}"
                f"_fall{args.falltime}.csv")
    save_filter_output(per_pixel, csv_path)
    print(f"wrote {csv_path}")

    with get_session() as session:
        outputs = ingest_filter_output(
            session, args.run, csv_path,
            trap_falltime=args.falltime, label=args.label,
            segment_index=args.segment,
        )
        session.commit()
        print(f"ingested {len(outputs)} pixel outputs")

    if args.keep_csv:
        print(f"kept {csv_path}")
    else:
        csv_path.unlink()
        print(f"deleted {csv_path} (the .h5 files remain the archive)")

    print(f"done in {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
