"""Apply the trap filter to raw .h5 subruns and write filter-output
CSVs — the offline counterpart of the cluster's apply_trap_filter, for
working entirely WITHOUT the database (e.g. at NERSC while GT is down).

Segments: the database normally supplies each segment's dwell window
(from slow controls). Offline, either provide the same windows as a CSV
(--segments: columns run,segment,start_time,end_time with ISO
timestamps including the timezone, e.g. 2026-08-10 12:55:48-04:00) or
omit it to process the WHOLE run as segment 0 — correct for
single-position runs.

Output naming matches the cluster staging format EXACTLY, so when the
database returns, every CSV ingests unchanged:

    Run{run}_seg{seg}_{wave}_filter_output_rt{rt}_ft{ft}_fall{fall}.csv
    -> python scripts/ingest_filter_output.py <out dir> --label ...

    python scripts/offline/trap_filter.py --h5-dir /pscratch/.../TempCal \\
        --run 9416 --out offline_output/filter
    python scripts/offline/trap_filter.py --h5-dir ... --run 9409 \\
        --segments my_segments.csv --out offline_output/filter
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from calibrationnet.pipeline.waveforms import (
    available_subruns,
    find_subruns,
    save_filter_output,
    segment_energies,
    to_ticks,
)


def read_segments(path, run_number):
    """[(segment_index, start_ticks, end_ticks)] for one run from the
    segments CSV. Timestamps must carry their timezone."""
    segments = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if int(row["run"]) != run_number:
                continue
            start = datetime.fromisoformat(row["start_time"])
            end = datetime.fromisoformat(row["end_time"])
            if start.tzinfo is None or end.tzinfo is None:
                raise SystemExit(
                    f"segment {row['run']}/{row['segment']}: timestamps "
                    "must include the timezone (e.g. 2026-08-10 "
                    "12:55:48-04:00) — the waveform clock is absolute.")
            segments.append((int(row["segment"]),
                             to_ticks(start), to_ticks(end)))
    return sorted(segments)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--h5-dir", required=True,
                        help="directory holding Run{N}_{subrun}.h5 files")
    parser.add_argument("--run", type=int, required=True, nargs="+")
    parser.add_argument("--segments", type=Path, default=None,
                        help="CSV of dwell windows (run,segment,"
                             "start_time,end_time); omit to process each "
                             "whole run as segment 0")
    parser.add_argument("--subruns", type=int, nargs=2, default=None,
                        metavar=("LO", "HI"),
                        help="only subruns LO..HI inclusive — for smoke "
                             "tests and for chunking a long run across "
                             "batch jobs (whole-run mode only)")
    parser.add_argument("--segment", type=int, default=None,
                        help="process only this segment index (with "
                             "--segments) — one batch task per segment")
    parser.add_argument("-rt", "--risetime", type=int, default=1250,
                        help="trap rise time, 4 ns bins (default 1250)")
    parser.add_argument("-ft", "--flattop", type=int, default=50)
    parser.add_argument("-fall", "--falltime", type=int, default=1250)
    parser.add_argument("--wave", default="singles",
                        choices=["singles", "pulsers"])
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/filter"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for run in args.run:
        if args.segments is not None:
            segments = read_segments(args.segments, run)
            if args.segment is not None:
                segments = [s for s in segments if s[0] == args.segment]
            if not segments:
                print(f"run {run}: no matching rows in {args.segments} "
                      "— skipped")
                continue
        else:
            segments = [(0, None, None)]     # the whole run, one segment

        for seg_index, start_ticks, end_ticks in segments:
            if start_ticks is None:
                subruns = available_subruns(args.h5_dir, run)
                if args.subruns:
                    subruns = [s for s in subruns
                               if args.subruns[0] <= s <= args.subruns[1]]
                window = None
                span = "whole run" if not args.subruns else (
                    f"subruns {args.subruns[0]}..{args.subruns[1]}")
            else:
                subruns = find_subruns(args.h5_dir, run,
                                       start_ticks, end_ticks,
                                       wave_type=args.wave)
                window = (start_ticks, end_ticks)
                span = f"{(end_ticks - start_ticks) / 2.5e8 / 60:.1f} min"
            if not subruns:
                print(f"run {run} seg {seg_index}: no subrun files found")
                continue
            print(f"run {run} segment {seg_index} ({span}): "
                  f"subruns {subruns[0]}-{subruns[-1]} "
                  f"({len(subruns)} files)")
            per_pixel = segment_energies(
                args.h5_dir, run, subruns,
                args.risetime, args.flattop, args.falltime,
                wave_type=args.wave, window=window)
            n = sum(len(v) for v in per_pixel.values())
            out = args.out / (
                f"Run{run}_seg{seg_index}_{args.wave}_filter_output"
                f"_rt{args.risetime}_ft{args.flattop}"
                f"_fall{args.falltime}.csv")
            save_filter_output(per_pixel, out)
            print(f"  -> {out.name}: {len(per_pixel)} pixels, "
                  f"{n} energies")


if __name__ == "__main__":
    main()
