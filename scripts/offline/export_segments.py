"""Write the segments CSV for offline processing, straight from slow
controls — the same dwell derivation ingest_run uses, WITHOUT touching
the CalibrationNet database (usable while GT is down: the slow-controls
computer is a different machine, reached over its own tunnel).

Needs: the slow-controls tunnel open and SC_DATABASE_URL in .env —
run this on the machine that has them (e.g. the laptop), then transfer
the CSV to wherever scripts/offline/trap_filter.py runs.

    python scripts/offline/export_segments.py 9409 9415 9416 \\
        --out offline_output/segments.csv
"""

import argparse
import csv
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from calibrationnet.acquisition.ingest import derive_segments
from calibrationnet.acquisition.slow_controls import fetch_run


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_numbers", type=int, nargs="+")
    parser.add_argument("--min-dwell", type=float, default=None,
                        metavar="MINUTES",
                        help="shortest stationary stretch that counts as "
                             "a dwell (default 5) — must sit safely BELOW "
                             "the run's dwell length (see the README "
                             "ingestion note; run 9464 needed 2)")
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/segments.csv"))
    args = parser.parse_args()
    min_dwell = (timedelta(minutes=args.min_dwell)
                 if args.min_dwell is not None else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for run_number in args.run_numbers:
        data = fetch_run(run_number)
        if data is None:
            raise SystemExit(f"run {run_number} not found in the "
                             "slow-controls database — is the tunnel "
                             "open?")
        run = SimpleNamespace(run_number=run_number,
                              start_time=data["start_time"],
                              end_time=data["end_time"])
        segments = derive_segments(run, data, min_dwell=min_dwell)
        for index, seg in enumerate(segments):
            dwell = (seg["end_time"] - seg["start_time"]).total_seconds() / 60
            rows.append({
                "run": run_number, "segment": index,
                "start_time": seg["start_time"].isoformat(),
                "end_time": seg["end_time"].isoformat(),
                "dwell_min": f"{dwell:.1f}",
                "linear_position": seg.get("linear_position"),
                "horizontal_position": seg.get("horizontal_position"),
                "position_convention": seg.get("position_convention"),
            })
        print(f"run {run_number}: {len(segments)} segment(s), dwells "
              f"{min(float(r['dwell_min']) for r in rows if r['run'] == run_number):.1f}"
              f"..{max(float(r['dwell_min']) for r in rows if r['run'] == run_number):.1f} min")

    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"-> {args.out}: {len(rows)} segment(s) total")


if __name__ == "__main__":
    main()
