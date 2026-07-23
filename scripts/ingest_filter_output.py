"""Store trap filter output CSVs (one row per pixel per file).

Rise time / flat top come from the filename (rtNNN_ftNN, in 4 ns time
bins); the run number comes from a RunNNNN_ filename prefix or --run.
Fall time is not encoded in filenames; default 1250 (the standard nabPy
setting is rise/flattop/fall = 1250/50/1250). Only ingest curated outputs
— not the full optimization scan.

    # one file, explicit run:
    python scripts/ingest_filter_output.py filter_output_rt100_ft10.csv \\
        --run 8622 --label comparison
    # a whole folder of RunNNNN_-prefixed files:
    python scripts/ingest_filter_output.py nabPyStandardFilterOutputs/ \\
        --label nabpy-standard
"""

import argparse
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.pipeline.trap_filter import (
    ingest_filter_output,
    parse_filter_filename,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+",
                        help="filter output CSV file(s) and/or folder(s)")
    parser.add_argument("--run", type=int, default=None,
                        help="run number (otherwise parsed from RunNNNN_ "
                             "filename prefix)")
    parser.add_argument("--falltime", type=float, default=1250,
                        help="trap fall time in 4 ns bins (default 1250)")
    parser.add_argument("--label", default=None,
                        help='why this output is stored, e.g. "nabpy-standard"')
    args = parser.parse_args()

    files = []
    for p in (Path(p) for p in args.paths):
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]

    failed = []
    with get_session() as session:
        for path in files:
            run_number = args.run or parse_filter_filename(path).get("run_number")
            if run_number is None:
                parser.error(f"{path.name}: no RunNNNN_ prefix and no --run")
            try:
                outputs = ingest_filter_output(
                    session, run_number, path,
                    trap_falltime=args.falltime, label=args.label,
                )
                session.commit()  # per file, so one failure loses nothing else
                total = sum(len(o.energies) for o in outputs)
                print(f"run {run_number}: {len(outputs)} pixel outputs "
                      f"({total} waveforms) from {path.name}", flush=True)
            except Exception as exc:
                session.rollback()
                failed.append(path.name)
                print(f"FAILED {path.name}: {exc}", flush=True)

    print(f"\n{len(files) - len(failed)}/{len(files)} files ingested")
    if failed:
        print("failed:", " ".join(failed))


if __name__ == "__main__":
    main()
