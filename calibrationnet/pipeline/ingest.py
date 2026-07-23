"""Ingest runs into CalibrationNet from the slow-controls database."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Run
from .slow_controls import fetch_run

# Everything fetch_run returns whose key matches a Run column gets stored;
# extra keys (lastsubrun, rundescription, errorcode) are ignored.
_RUN_COLUMNS = {c.name for c in Run.__table__.columns} - {"id", "run_number"}


def ingest_run(session: Session, run_number: int) -> Run:
    """Create (or refresh) the Run row for run_number from slow controls.

    Idempotent: re-ingesting an existing run updates its fields in place.
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

    return run
