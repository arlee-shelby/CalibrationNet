from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .run import Run
    from .run_pixel import RunPixel


class RunSegment(Base):
    """Single period of constant source position within a run.

    A segment is the unit that has a single source position and it
    is the direct analogue of one of the 2025 short single-position runs.
    A "dwell" is defined as the period of time when the source position was unchanged.
    A run taken at one position has exactly one segment (index starts at 0).
    One segment has one dwell.

    start_time/end_time cover the dwell only — the motion between dwells
    is deliberately left outside every segment, so waveforms selected by
    a segment's time range were all taken at one position (i.e. transition periods where the
    sources are moving are not included in the segment time).

    The source position lives here, not on runs, because a run does not necessarily
    have one position (ex: 2026 runs). For all 2026 runs, source position is raw readback
    from the slow control database, in the convention named by
    position_convention (see calibrationnet.positions); linear is always
    inches (both before and after 2026), horizontal depends on the convention
    (i.e. it is different between 2025 and 2026). For 2025 runs, the source position was manually
    recorded and entered.
    """

    __tablename__ = "run_segments"

    run_number: Mapped[int] = mapped_column(ForeignKey("runs.run_number"), primary_key=True)

    # segment index starts with 0 and increases in time order within the run
    segment_index: Mapped[int] = mapped_column(primary_key=True)

    # timestamptz: slow controls reports time-zone-aware times (US/Eastern)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    linear_position: Mapped[Optional[float]]  # inches
    horizontal_position: Mapped[Optional[float]]

    # position convention defined in calibrationnet.positions
    position_convention: Mapped[Optional[str]] = mapped_column(String(20))

    # optional manual entry for notes
    notes: Mapped[Optional[str]]

    run: Mapped["Run"] = relationship(back_populates="segments")
    run_pixels: Mapped[List["RunPixel"]] = relationship(back_populates="segment", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"RunSegment(run={self.run_number}, seg={self.segment_index}, "
            f"lin={self.linear_position}, hor={self.horizontal_position})"
        )
