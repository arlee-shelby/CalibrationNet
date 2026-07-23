"""Set run_pixels.board_channel for a run from its HDF5 data file.

Any subrun file works — the map doesn't change within a run.

    python scripts/ingest_board_channels.py 8622 Run8622_0.h5
"""

import argparse

from calibrationnet.db import get_session
from calibrationnet.pipeline.board_channels import ingest_board_channels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_number", type=int)
    parser.add_argument("h5_file")
    args = parser.parse_args()

    with get_session() as session:
        n = ingest_board_channels(session, args.run_number, args.h5_file)
        session.commit()
        print(f"run {args.run_number}: board channels set for {n} pixels")


if __name__ == "__main__":
    main()
