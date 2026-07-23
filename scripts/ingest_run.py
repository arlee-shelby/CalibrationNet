"""Add run(s) to CalibrationNet, pulling metadata from slow controls.

The slow-controls tunnel must be open (or wrap the call):

    python scripts/ingest_run.py 8622 8623
    python scripts/ingest_run.py --file run_list.txt
"""

import argparse
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.pipeline.ingest import ingest_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_numbers", type=int, nargs="*")
    parser.add_argument(
        "--file", type=Path, help="text file with one run number per line"
    )
    args = parser.parse_args()

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
                run = ingest_run(session, run_number)
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
