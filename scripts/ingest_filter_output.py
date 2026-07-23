"""Store a trap filter output CSV for a run (one row per pixel).

Rise time / flat top come from the filename (rtNNN_ftNN); fall time must
be given. Only ingest curated outputs — the per-pixel optimized settings
and any comparison settings — not the full optimization scan.

    python scripts/ingest_filter_output.py 8622 filter_output_rt100_ft10.csv \\
        --falltime 1250 --label comparison
"""

import argparse

from calibrationnet.db import get_session
from calibrationnet.pipeline.trap_filter import ingest_filter_output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_number", type=int)
    parser.add_argument("csv_file")
    parser.add_argument("--falltime", type=float, required=True,
                        help="trap fall time (not encoded in the filename)")
    parser.add_argument("--label", default=None,
                        help='why this output is stored, e.g. "optimized"')
    args = parser.parse_args()

    with get_session() as session:
        outputs = ingest_filter_output(
            session, args.run_number, args.csv_file,
            trap_falltime=args.falltime, label=args.label,
        )
        session.commit()
        total = sum(len(o.energies) for o in outputs)
        print(f"run {args.run_number}: stored {len(outputs)} pixel outputs "
              f"({total} waveforms) from {args.csv_file}")


if __name__ == "__main__":
    main()
