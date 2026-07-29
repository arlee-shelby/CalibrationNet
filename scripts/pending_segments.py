"""What still needs a given trap filter output, and what is finished.

Default output is the manifest a SLURM array job indexes by task id: one
"<run> <segment>" line per segment that does NOT yet hold an output with
these settings. Re-submitting therefore only redoes what is missing.

    python scripts/pending_segments.py --runs 9370
    python scripts/pending_segments.py --runs-file run_list.txt

--summary instead prints a per-run progress report and exits non-zero if
anything is still missing, which is how the batch reports completion:

    python scripts/pending_segments.py --runs-file run_list.txt --summary
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select

from calibrationnet.db import get_session
from calibrationnet.models import RunSegment
from calibrationnet.pipeline.trap_filter import segments_missing_output


def compress(numbers) -> str:
    """[0,1,2,5,6,9] -> '0-2,5-6,9' so a long list stays readable."""
    spans, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            spans.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ",".join(spans)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
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
                        help="list every segment, including finished ones")
    parser.add_argument("--summary", action="store_true",
                        help="report progress per run instead of a manifest")
    args = parser.parse_args()

    runs = args.runs or [
        int(line) for line in args.runs_file.read_text().split() if line.strip()
    ]

    with get_session() as session:
        totals = dict(session.execute(
            select(RunSegment.run_number, func.count())
            .where(RunSegment.run_number.in_(runs))
            .group_by(RunSegment.run_number)
        ).all())
        unknown = sorted(set(runs) - set(totals))
        if unknown:
            raise SystemExit(
                f"runs not in the database (ingest them first with "
                f"scripts/ingest_run.py): {unknown}"
            )
        missing = segments_missing_output(
            session, runs, args.risetime, args.flattop, args.falltime,
            args.label,
        )
        if args.all:
            # Treat nothing as done, so every segment is listed/redone.
            missing = {run: list(range(totals[run])) for run in runs}

    if not args.summary:
        for run in sorted(runs):
            for segment in missing.get(run, []):
                print(f"{run} {segment}")
        return

    setting = (f"rt={args.risetime} ft={args.flattop} "
               f"fall={args.falltime} ({args.label})")
    print(f"trap filter output for {setting}\n")
    total_segments = done_segments = 0
    for run in sorted(runs):
        left = missing.get(run, [])
        done = totals[run] - len(left)
        total_segments += totals[run]
        done_segments += done
        state = ("COMPLETE" if not left
                 else f"missing {compress(left)}")
        print(f"  run {run}: {done}/{totals[run]} segments ingested"
              f" — {state}")

    incomplete = [r for r in runs if missing.get(r)]
    print(f"\n{done_segments}/{total_segments} segments ingested "
          f"across {len(runs)} run(s)")
    if incomplete:
        print(f"INCOMPLETE: {len(incomplete)} run(s) still missing segments: "
              f"{sorted(incomplete)}")
        print("re-run scripts/submit_trap_filter.sh to finish them")
        sys.exit(1)
    print("ALL RUNS COMPLETE — every segment has this filter output")


if __name__ == "__main__":
    main()
