"""Set run_pixels.board_channel from the per-run board-channel maps.

Either directly from one run's HDF5 file (any subrun works), or in bulk
from the CSV that scripts/extract_bc_maps.py produces on the cluster:

    python scripts/ingest_board_channels.py 8622 development/inputs/Run8622_0.h5
    python scripts/ingest_board_channels.py --csv data/bc_maps.csv
"""

import argparse
import csv
from collections import defaultdict

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import Run
from calibrationnet.acquisition.board_channels import (
    apply_bc_map,
    clean_bc_pairs,
    ingest_board_channels,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_number", type=int, nargs="?")
    parser.add_argument("h5_file", nargs="?")
    parser.add_argument("--csv", help="data/bc_maps.csv from extract_bc_maps.py")
    args = parser.parse_args()

    if args.csv:
        pairs_by_run = defaultdict(list)
        with open(args.csv, newline="") as f:
            for row in csv.DictReader(f):
                pairs_by_run[int(row["run_number"])].append(
                    (int(row["board_channel"]), int(row["pixel_number"])))
        skipped = []
        with get_session() as session:
            known_runs = {r for (r,) in session.execute(select(Run.run_number))}
            for run_number in sorted(pairs_by_run):
                if run_number not in known_runs:
                    skipped.append(run_number)
                    continue
                bc_map = clean_bc_pairs(pairs_by_run[run_number])
                n = apply_bc_map(session, run_number, bc_map)
                session.commit()
                print(f"run {run_number}: board channels set for {n} pixels")
        if skipped:
            print(f"\nskipped {len(skipped)} run(s) not in the database "
                  f"(no run metadata ingested): {skipped}")
    elif args.run_number and args.h5_file:
        with get_session() as session:
            n = ingest_board_channels(session, args.run_number, args.h5_file)
            session.commit()
            print(f"run {args.run_number}: board channels set for {n} pixels")
    else:
        parser.error("give RUN_NUMBER H5_FILE, or --csv bc_maps.csv")


if __name__ == "__main__":
    main()
