"""Ingest runs and run segments into the CalibrationNet database. Note, run segments
are defined as periods of a run where the source position was constant. Run settings and
other run/segment specific information is pulled from two databases
that record the slow-controls metadata (essentially as shown on grafana).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Run, RunSegment
from ..positions import INCHES_2026, convention_for_date
from .epics_controls import dwell_periods, fetch_settings
from .slow_controls import fetch_run


# pulls the Run columns that are stored in the database, so only matching run information
# is stored
_RUN_COLUMNS = {c.name for c in Run.__table__.columns} - {"run_number"}


def derive_segments(run: Run, data: dict, min_dwell=None) -> list:
    """Returns the run's constant source position segments, as dicts ready to ingest
    as elements of RunSegment. Note, segments are defined as periods of a run where the source
    position is constant. Thus, the source position is an attribute of RunSegment, not
    of the Run (which can have multiple segments/source positions within it).
    """

    # convention for horizontal source position units depends on date, runs before 2026-07-24 have arbitrary
    # RSIS units, and after have inch units
    # all linear positions are in inches
    convention = convention_for_date(run.start_time.date())
    if convention == INCHES_2026:
        kwargs = {} if min_dwell is None else {"min_dwell": min_dwell}
        periods = dwell_periods(run.start_time, run.end_time, **kwargs)
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


def sync_segments(session: Session, run: Run, data: dict,min_dwell=None) -> list:
    """In order to allow for run and run segment ingestion to be idempotent,
    this function 1) derives run segments and 2) matches the run segments
    derived to the ones in the database already (if any). Segments are matched by index, so re-ingesting
    re-derives the start/end times and averaged position for the segments, without disturbing
    the run_pixels (and the analysis chain) already attached to them. A segment that no longer
    appears for the run is only removed if nothing hangs off it (ex: trap filter outputs, spectrum
    fits, etc.), otherwise it is kept and reported, since deleting it would discard real analysis.
    """
    derived = derive_segments(run, data, min_dwell=min_dwell)
    existing = {s.segment_index: s for s in run.segments}

    for index, period in enumerate(derived):
        segment = existing.pop(index, None)
        if segment is None:
            segment = RunSegment(run_number=run.run_number,segment_index=index)
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


def ingest_run(session: Session, run_number: int, min_dwell=None) -> Run:
    """Create (or update) the Run row and the RunSegment rows from the slow control
    metadata.

    Run segments are defined by periods of constant source position, the minimum "dwell"
    period default is 5 minutes (see epics_controls.py for details) but can be reduced
    for specific runs, ex: short raster runs.

    Ingesting runs and run segments is idempotent, i.e. re-ingesting an existing run updates
    the columns in place but never deletes a segment that already has analysis derived from it.
    This file contains the ingestion logic, but does not commit the ingestion to the database.
    That is done through files in the "scripts" folder.
    """
    data = fetch_run(run_number)
    if data is None:
        raise ValueError(f"Run {run_number} not found in the slow-controls database.")

    run = session.execute(select(Run).where(Run.run_number == run_number)).scalar_one_or_none()
    if run is None:
        run = Run(run_number=run_number)
        session.add(run)

    for key, value in data.items():
        if key in _RUN_COLUMNS:
            setattr(run, key, value)

    # several settings moved to the "Test" database after 2026-07-21 so newer
    # runs also pull additional settings from there (see epics_controls.py)
    if convention_for_date(run.start_time.date()) == INCHES_2026:
        for key, value in fetch_settings(run.start_time,run.end_time).items():
            # only fills values from new "Test" database that were not stored previously
            if getattr(run, key) is None:
                setattr(run, key, value)

    session.flush()  # the run must exist before its segments reference it
    sync_segments(session, run, data, min_dwell=min_dwell)
    return run
