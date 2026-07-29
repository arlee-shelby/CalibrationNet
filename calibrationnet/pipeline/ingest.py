"""Ingest runs into CalibrationNet from the slow-controls database."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Run, RunSegment
from ..positions import INCHES_2026, convention_for_date
from .motion_control import dwell_periods
from .slow_controls import fetch_run

# Everything fetch_run returns whose key matches a Run column gets stored;
# the rest (rundescription, errorcode, positions) is used elsewhere or
# ignored. Positions are not Run columns — they live on run_segments.
_RUN_COLUMNS = {c.name for c in Run.__table__.columns} - {"run_number"}


def ingest_run(session: Session, run_number: int) -> Run:
    """Create (or refresh) the Run row and its segments from slow controls.

    Idempotent: re-ingesting an existing run updates fields in place and
    never deletes a segment that already has analysis hanging off it.
    Does not commit — the caller controls the transaction.
    """
    data = fetch_run(run_number)
    if data is None:
        raise ValueError(
            f"Run {run_number} not found in the slow-controls database."
        )

    run = session.execute(
        select(Run).where(Run.run_number == run_number)
    ).scalar_one_or_none()
    if run is None:
        run = Run(run_number=run_number)
        session.add(run)

    for key, value in data.items():
        if key in _RUN_COLUMNS:
            setattr(run, key, value)

    session.flush()  # the run must exist before its segments reference it
    sync_segments(session, run, data)
    return run


def derive_segments(run: Run, data: dict) -> list:
    """The run's source-position segments, as dicts ready for RunSegment.

    Runs from 2026-07-24 get one segment per dwell in the motion-control
    archive (a rastering run has dozens). Earlier runs had one position
    for the whole run, recorded only in the run description.
    """
    convention = convention_for_date(run.start_time.date())
    if convention == INCHES_2026:
        periods = dwell_periods(run.start_time, run.end_time)
    else:
        periods = [{
            "start_time": run.start_time,
            "end_time": run.end_time,
            "linear_position": data.get("linear_position"),
            "horizontal_position": data.get("horizontal_position"),
        }]
    for period in periods:
        period["position_convention"] = convention
    return periods


def sync_segments(session: Session, run: Run, data: dict) -> list:
    """Match the run's segments to the derived ones, in place.

    Segments are matched by index, so re-ingesting refreshes times and
    positions without disturbing the run_pixels (and the analysis chain)
    already attached to them. A segment that no longer appears in the
    derivation is only removed if nothing hangs off it; otherwise it is
    kept and reported, since deleting it would discard real analysis.
    """
    derived = derive_segments(run, data)
    existing = {s.segment_index: s for s in run.segments}

    for index, period in enumerate(derived):
        segment = existing.pop(index, None)
        if segment is None:
            segment = RunSegment(run_number=run.run_number,
                                 segment_index=index)
            session.add(segment)
        for key, value in period.items():
            setattr(segment, key, value)

    for index, segment in sorted(existing.items()):
        if segment.run_pixels:
            print(f"note: run {run.run_number} segment {index} is no longer "
                  f"in the position archive but has "
                  f"{len(segment.run_pixels)} run_pixels — keeping it")
        else:
            session.delete(segment)

    return derived
