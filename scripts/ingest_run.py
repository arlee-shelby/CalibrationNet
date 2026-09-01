"""Add run(s) to CalibrationNet, pulling metadata from slow controls.

The slow-controls tunnel must be open (or wrap the call):

    python scripts/ingest_run.py 8622 8623
    python scripts/ingest_run.py --file run_list.txt
"""

import argparse
from datetime import timedelta
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.acquisition.run_metadata import ingest_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_numbers", type=int, nargs="*")
    parser.add_argument(
        "--file", type=Path, help="text file with one run number per line"
    )
    parser.add_argument(
        "--min-dwell", type=float, default=None, metavar="MINUTES",
        help="shortest stationary stretch that counts as a dwell segment "
             "(default 5). Lower it for runs whose grid dwells are "
             "themselves ~5 min, where the default sits on the boundary "
             "and drops segments."
    )
    args = parser.parse_args()
    min_dwell = (timedelta(minutes=args.min_dwell)
                 if args.min_dwell is not None else None)

    run_numbers = list(args.run_numbers)
    if args.file:
        run_numbers += [
            int(line) for line in args.file.read_text().split() if line.strip()
        ]
    if not run_numbers:
        parser.error("no run numbers given (arguments or --file)")

    failed = []
    with get_session() as session:
        for run_number in run_numbers:
            try:
                run = ingest_run(session, run_number, min_dwell=min_dwell)
                session.commit()  # per run, so one failure doesn't lose the rest
                print(f"ingested run {run.run_number}: "
                      f"{run.start_time} -> {run.end_time}")
            except Exception as exc:
                session.rollback()
                failed.append(run_number)
                print(f"FAILED run {run_number}: {exc}")

    print(f"\n{len(run_numbers) - len(failed)}/{len(run_numbers)} runs ingested")
    if failed:
        print("failed:", " ".join(str(r) for r in failed))


if __name__ == "__main__":
    main()
