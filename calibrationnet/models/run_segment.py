from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .run import Run
    from .run_pixel import RunPixel


class RunSegment(Base):
    """One period of constant source-frame position within a run.

    A segment is the unit that has a single source configuration, and it
    is the direct analogue of one of the older short single-position runs:
    the long "rastering" runs step the frame through many positions with a
    ~30 minute dwell at each, so each dwell gets its own segment. A run
    taken at one position has exactly one segment (index 0).

    start_time/end_time cover the dwell only — the motion between dwells
    is deliberately left outside every segment, so waveforms selected by
    a segment's time range were all taken at one position.

    Positions live here, not on runs, because a run does not necessarily
    have one. They are raw readback in the convention named by
    position_convention (see calibrationnet.positions); linear is always
    inches, horizontal depends on the convention.
    """

    __tablename__ = "run_segments"

    run_number: Mapped[int] = mapped_column(
        ForeignKey("runs.run_number"), primary_key=True
    )
    # 0-based, in time order within the run.
    segment_index: Mapped[int] = mapped_column(primary_key=True)

    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    linear_position: Mapped[Optional[float]]  # inches
    horizontal_position: Mapped[Optional[float]]
    position_convention: Mapped[Optional[str]] = mapped_column(String(20))

    notes: Mapped[Optional[str]]

    run: Mapped["Run"] = relationship(back_populates="segments")
    run_pixels: Mapped[List["RunPixel"]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"RunSegment(run={self.run_number}, seg={self.segment_index}, "
            f"lin={self.linear_position}, hor={self.horizontal_position})"
        )
