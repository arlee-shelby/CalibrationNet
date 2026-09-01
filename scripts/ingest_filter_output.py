"""Store trap filter output CSVs (one row per pixel per file).

Trap settings come from the filename (rtNNN_ftNN_fallNNNN, in 4 ns time
bins); the run number comes from a RunNNNN_ filename prefix or --run; the
segment comes from a segN filename component or --segment (default 0 —
correct for every single-position run). A legacy name without a fallNNNN
component needs --falltime (the standard nabPy setting is
rise/flattop/fall = 1250/50/1250); when the filename has one, --falltime
may only confirm it — a mismatch is an error. Only ingest curated
settings: every stored output is labeled with why it's stored, and
that label is how analyses select outputs.

    # one file, explicit run (single-position run -> segment 0):
    python scripts/ingest_filter_output.py \\
        filter_output_rt100_ft10_fall1250.csv --run 8622 --label comparison
    # a whole folder of legacy RunNNNN_-prefixed files (no _fall in the
    # names, so the fall time must be supplied):
    python scripts/ingest_filter_output.py nabPyStandardFilterOutputs/ \\
        --falltime 1250 --label nabpy-standard
    # a CSV left behind by an apply_trap_filter.py task whose ingest step
    # failed (run and segment are parsed from the name):
    python scripts/ingest_filter_output.py \\
        data/TrapFilterData/Run9371_seg7_singles_filter_output_rt1250_ft50_fall1250.csv \\
        --label nabpy-standard
"""

import argparse
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.acquisition.trap_filter import (
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
    parser.add_argument("--segment", type=int, default=None,
                        help="segment index (otherwise parsed from a segN "
                             "filename component; default 0)")
    parser.add_argument("--falltime", type=float, default=None,
                        help="trap fall time in 4 ns bins — needed only "
                             "for legacy filenames without a _fall "
                             "component; if the filename has one, the "
                             "two must agree")
    parser.add_argument("--label", default=None,
                        help='why this output is stored, e.g. "nabpy-standard"')
    args = parser.parse_args()

    files = []
    for p in (Path(p) for p in args.paths):
        files += sorted(p.glob("*.csv")) if p.is_dir() else [p]

    failed = []
    with get_session() as session:
        for path in files:
            parsed = parse_filter_filename(path)
            run_number = args.run or parsed.get("run_number")
            if run_number is None:
                parser.error(f"{path.name}: no RunNNNN_ prefix and no --run")
            segment = (args.segment if args.segment is not None
                       else parsed.get("segment_index", 0))
            try:
                outputs = ingest_filter_output(
                    session, run_number, path,
                    trap_falltime=args.falltime, label=args.label,
                    segment_index=segment,
                )
                session.commit()  # per file, so one failure loses nothing else
                total = sum(len(o.energies) for o in outputs)
                print(f"run {run_number} segment {segment}: "
                      f"{len(outputs)} pixel outputs "
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
