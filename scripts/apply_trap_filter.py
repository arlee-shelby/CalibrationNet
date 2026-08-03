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
from calibrationnet.pipeline.board_channels import ingest_board_channels
from calibrationnet.pipeline.trap_filter import (
    ingest_filter_output,
    segments_missing_output,
)
from calibrationnet.pipeline.waveforms import (
    available_subruns,
    find_subruns,
    save_filter_output,
    segment_energies,
    subrun_file,
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

    # 2. Only the subruns overlapping this segment's dwell. A single-segment
    #    run covers everything, so skip the search and take them all.
    if n_segments == 1:
        subruns = available_subruns(args.directory, args.run)
        if not subruns:
            raise SystemExit(
                f"no Run{args.run}_*.h5 files in {args.directory} — check -d."
            )
        mask_window = None
    else:
        subruns = find_subruns(args.directory, args.run, *window,
                               wave_type=args.wave)
        mask_window = window
    print(f"using subruns {subruns[0]}-{subruns[-1]} "
          f"({len(subruns)} files, run has {n_subruns})")

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

    # 4. Store, then ingest, then drop the intermediate file. The CSV
    # sits FLAT in the staging directory (no per-run subfolder — it is
    # deleted after a successful ingest; it only survives, name fully
    # self-describing, when the ingest fails and needs a rescue via
    # scripts/ingest_filter_output.py).
    csv_path = (args.out /
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

        # The board-channel map lives in the same h5 files this task just
        # filtered, so fill run_pixels.board_channel here — no separate
        # extract/transfer CSV step. Idempotent and per-run: every task
        # re-applies the same map to whatever run_pixels exist by then,
        # so between all of a run's tasks every segment gets covered.
        try:
            n_bc = ingest_board_channels(
                session, args.run,
                subrun_file(args.directory, args.run, subruns[0]))
            session.commit()
            print(f"board channels set for {n_bc} pixels")
        except Exception as exc:
            session.rollback()
            print(f"note: board-channel ingest skipped ({exc})")

        # Parallel tasks share only the database, so ask it whether this
        # task happened to be the last one for the run.
        left = segments_missing_output(
            session, [args.run], args.risetime, args.flattop, args.falltime,
            args.label,
        ).get(args.run, [])
        if left:
            print(f"run {args.run}: {n_segments - len(left)}/{n_segments} "
                  f"segments ingested, {len(left)} to go")
        else:
            print(f"RUN {args.run} COMPLETE: all {n_segments} segments have "
                  f"rt={args.risetime} ft={args.flattop} "
                  f"fall={args.falltime} ({args.label})")

    if args.keep_csv:
        print(f"kept {csv_path}")
    else:
        csv_path.unlink()
        print(f"deleted {csv_path} (the .h5 files remain the archive)")

    print(f"done in {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
