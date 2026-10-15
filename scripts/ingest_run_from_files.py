"""Ingest a run whose row is MISSING from the slow-controls run log.

scripts/ingest_run.py takes a run's time window and subrun count from
runlog.status in the slow-controls database. A run that was aborted or
restarted before the DAQ closed it has no such row (first seen: run
10081, 2026-10), yet everything else ingest stores comes from archives
indexed by TIME, not by run number — the biases, HV and field settings
from the Test archive, the dwell segments from the motion archive — and
those have no gap. So this script supplies the window and lets the same
code do the rest:

    # the window from the data itself: the first and last waveform
    # timestamps of the run's h5 files (needs nabPy + the files, i.e.
    # the cluster, with the slow-controls tunnel open there)
    python scripts/ingest_run_from_files.py 10081 --h5-dir /storage/.../TempCal

    # or typed, when the window is known (ISO times WITH timezone):
    python scripts/ingest_run_from_files.py 10081 \\
        --start "2026-10-07 18:02:11-04:00" --end "2026-10-08 00:14:50-04:00" --subruns 1722

    # look before writing: everything is derived and printed, nothing stored
    python scripts/ingest_run_from_files.py 10081 --h5-dir ... --dry-run

The subrun count stored is the LAST subrun index, as the slow-controls
"lastsubrun" column is (a run with files 0..1722 stores 1722). The only
things a run-log row carries that this cannot reproduce are its event
totals and description, neither of which the runs table stores.
Re-running is safe: like ingest_run, an existing run is refreshed in
place and segments with analysis attached are never deleted.

Note for the cleanup merge: the two package imports below move from
calibrationnet.pipeline to calibrationnet.acquisition (ingest ->
run_metadata) on the cleanup branch; nothing else changes.
"""

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from calibrationnet.db import get_session
from calibrationnet.models import Run
from calibrationnet.pipeline.ingest import sync_segments
from calibrationnet.pipeline.motion_control import fetch_settings
from calibrationnet.pipeline.waveforms import (TICKS_PER_SECOND, available_subruns, subrun_span)
from calibrationnet.positions import INCHES_2026, convention_for_date

# the slow-controls timestamps are stored in this zone; any aware zone works
LOCAL_ZONE = ZoneInfo("America/New_York")


def ticks_to_datetime(ticks: float) -> datetime:
    """nabPy 'unix timestamp' ticks (4 ns since the epoch) -> aware datetime."""
    return datetime.fromtimestamp(ticks / TICKS_PER_SECOND, tz=LOCAL_ZONE)


def window_from_files(h5_dir: str, run_number: int, wave: str) -> dict:
    """{start_time, end_time, number_subruns} read from the run's h5 files:
    the first waveform timestamp of the first readable subrun and the last
    of the last readable one. Unreadable or empty files at either end are
    stepped over with a note (subrun_span prints it)."""
    subruns = available_subruns(h5_dir, run_number)
    if not subruns:
        raise SystemExit(f"no Run{run_number}_<subrun>.h5 files in {h5_dir}")
    first_ticks = None
    for subrun in subruns:
        span = subrun_span(h5_dir, run_number, subrun, wave)
        if span is not None:
            first_ticks = span[0]
            break
    last_ticks = None
    for subrun in reversed(subruns):
        span = subrun_span(h5_dir, run_number, subrun, wave)
        if span is not None:
            last_ticks = span[1]
            break
    if first_ticks is None or last_ticks is None:
        raise SystemExit(f"no readable '{wave}' waveforms in any subrun of run {run_number}")
    return {"start_time": ticks_to_datetime(first_ticks),
            "end_time": ticks_to_datetime(last_ticks),
            "number_subruns": max(subruns)}


def window_from_arguments(start: str, end: str, subruns: int) -> dict:
    start_time, end_time = datetime.fromisoformat(start), datetime.fromisoformat(end)
    for name, value in (("--start", start_time), ("--end", end_time)):
        if value.tzinfo is None:
            raise SystemExit(f"{name} must include the timezone, e.g. 2026-10-07 18:02:11-04:00")
    if end_time <= start_time:
        raise SystemExit("--end must be after --start")
    return {"start_time": start_time, "end_time": end_time, "number_subruns": subruns}


def ingest_run_from_window(session, run_number: int, data: dict, min_dwell=None) -> Run:
    """The body of calibrationnet.pipeline.ingest.ingest_run with the
    slow-controls lookup replaced by the given window. Does not commit."""
    run = session.execute(select(Run).where(Run.run_number == run_number)).scalar_one_or_none()
    if run is None:
        run = Run(run_number=run_number)
        session.add(run)
    run_columns = {column.name for column in Run.__table__.columns} - {"run_number"}
    for key, value in data.items():
        if key in run_columns:
            setattr(run, key, value)
    # settings live in the Test archive for the 2026 epoch; fill-if-missing,
    # exactly as ingest_run does
    if convention_for_date(run.start_time.date()) == INCHES_2026:
        for key, value in fetch_settings(run.start_time, run.end_time).items():
            if getattr(run, key) is None:
                setattr(run, key, value)
    session.flush()
    sync_segments(session, run, data, min_dwell=min_dwell)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_number", type=int)
    parser.add_argument("--h5-dir", default=None, help="read the window from the run's h5 files in this directory")
    parser.add_argument("--start", default=None, help="run start, ISO with timezone (with --end and --subruns)")
    parser.add_argument("--end", default=None, help="run end, ISO with timezone")
    parser.add_argument("--subruns", type=int, default=None, help="last subrun index of the run")
    parser.add_argument("--wave", default="singles", choices=["singles", "pulsers"],
                        help="which waveforms' timestamps define the window (--h5-dir mode)")
    parser.add_argument("--min-dwell", type=float, default=None, metavar="MINUTES",
                        help="shortest stationary stretch that counts as a dwell segment (default 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="derive and print the run row and segments, then roll back")
    args = parser.parse_args()

    if args.h5_dir is not None:
        if args.start or args.end or args.subruns is not None:
            parser.error("give either --h5-dir or --start/--end/--subruns, not both")
        data = window_from_files(args.h5_dir, args.run_number, args.wave)
    elif args.start and args.end and args.subruns is not None:
        data = window_from_arguments(args.start, args.end, args.subruns)
    else:
        parser.error("give --h5-dir, or all three of --start, --end and --subruns")
    min_dwell = timedelta(minutes=args.min_dwell) if args.min_dwell is not None else None

    duration = data["end_time"] - data["start_time"]
    print(f"run {args.run_number}: {data['start_time']} -> {data['end_time']} "
          f"({duration}), last subrun {data['number_subruns']}")

    with get_session() as session:
        try:
            run = ingest_run_from_window(session, args.run_number, data, min_dwell=min_dwell)
            settings = {column: getattr(run, column) for column in
                        ("hv", "main", "udet", "exb", "udet_bias", "ldet_bias", "udet_leakage", "ldet_leakage")}
            print("settings from the archives:", settings)
            print(f"{len(run.segments)} segment(s):")
            for segment in sorted(run.segments, key=lambda s: s.segment_index):
                print(f"  {segment.segment_index}: {segment.start_time} -> {segment.end_time}  "
                      f"linear {segment.linear_position}  horizontal {segment.horizontal_position}  "
                      f"({segment.position_convention})")
            if args.dry_run:
                session.rollback()
                print("dry run — nothing stored")
            else:
                session.commit()
                print(f"ingested run {run.run_number}")
        except OperationalError as error:
            session.rollback()
            raise SystemExit(f"FAILED run {args.run_number}: a database is unreachable — the settings and "
                             "dwell segments come from the slow-controls archives, so the slow-controls "
                             "tunnel (:15432) must be open as well as the CalibrationNet one (:5432). "
                             f"({str(error).splitlines()[0]})")
        except Exception as error:
            session.rollback()
            raise SystemExit(f"FAILED run {args.run_number}: {error}")


if __name__ == "__main__":
    main()
