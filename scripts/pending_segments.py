"""List the run segments that still need a given trap filter output.

Prints one "<run> <segment>" line per segment, which is the manifest a
SLURM array job indexes by task id. Segments that already hold an output
with these exact settings and label are skipped, so re-submitting after a
partial or preempted run only redoes what is missing.

    python scripts/pending_segments.py --runs 9369 9370
    python scripts/pending_segments.py --runs-file run_list.txt
    python scripts/pending_segments.py --runs-file run_list.txt --all
"""

import argparse
from pathlib import Path

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import RunPixel, RunSegment, TrapFilterOutput


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--runs", type=int, nargs="+")
    source.add_argument("--runs-file", type=Path,
                        help="text file with one run number per line")
    parser.add_argument("-rt", "--risetime", type=int, default=1250)
    parser.add_argument("-ft", "--flattop", type=int, default=50)
    parser.add_argument("-fall", "--falltime", type=int, default=1250)
    parser.add_argument("--label", default="nabpy-standard")
    parser.add_argument("--all", action="store_true",
                        help="list every segment, including ones already done")
    args = parser.parse_args()

    runs = args.runs or [
        int(line) for line in args.runs_file.read_text().split() if line.strip()
    ]

    with get_session() as session:
        segments = session.scalars(
            select(RunSegment)
            .where(RunSegment.run_number.in_(runs))
            .order_by(RunSegment.run_number, RunSegment.segment_index)
        ).all()

        done = set()
        if not args.all:
            done = set(session.execute(
                select(RunPixel.run_number, RunPixel.segment_index)
                .join(TrapFilterOutput,
                      TrapFilterOutput.run_pixel_id == RunPixel.id)
                .where(RunPixel.run_number.in_(runs),
                       TrapFilterOutput.trap_rise == args.risetime,
                       TrapFilterOutput.trap_flattop == args.flattop,
                       TrapFilterOutput.trap_falltime == args.falltime,
                       TrapFilterOutput.label == args.label)
                .distinct()
            ).all())

        for segment in segments:
            key = (segment.run_number, segment.segment_index)
            if key not in done:
                print(f"{segment.run_number} {segment.segment_index}")

    missing = sorted(set(runs) - {s.run_number for s in segments})
    if missing:
        raise SystemExit(
            f"runs not in the database (ingest them first with "
            f"scripts/ingest_run.py): {missing}"
        )


if __name__ == "__main__":
    main()
