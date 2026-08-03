"""Extract BoardChannelToPixelMap from every run's HDF5 data file into
one small CSV — run this ON THE CLUSTER where the h5 files live, then
ingest the CSV locally with:

    python scripts/ingest_board_channels.py --csv data/bc_maps.csv

Needs only h5py (pip install h5py). The map doesn't change within a run,
so one subrun file per run suffices (the lowest-numbered one found).

    python scripts/extract_bc_maps.py /path/to/h5/files -o data/bc_maps.csv
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import h5py

DATASET = "Parameters/BoardChannelToPixelMap"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("h5_dir", type=Path)
    parser.add_argument("-o", "--out", default="data/bc_maps.csv")
    args = parser.parse_args()

    by_run = defaultdict(list)  # run -> [(subrun, path)]
    for path in args.h5_dir.rglob("Run*_*.h5"):
        m = re.match(r"Run(\d+)_(\d+)\.h5$", path.name)
        if m:
            by_run[int(m.group(1))].append((int(m.group(2)), path))
    if not by_run:
        raise SystemExit(
            f"No files matching Run<run>_<subrun>.h5 found under "
            f"{args.h5_dir} (searched recursively). Check the path, or "
            "tell me the actual filename pattern."
        )

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["run_number", "board_channel", "pixel_number"])
        for run_number in sorted(by_run):
            _, path = min(by_run[run_number])
            with h5py.File(path, "r") as h5:
                rows = h5[DATASET][()]
            for board_channel, pixel_number in rows:
                writer.writerow([run_number, int(board_channel),
                                 int(pixel_number)])
            print(f"run {run_number}: {len(rows)} map rows from {path.name}")

    print(f"\nwrote {args.out} for {len(by_run)} runs")


if __name__ == "__main__":
    main()
