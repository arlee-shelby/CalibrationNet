from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .run_pixel import RunPixel
    from .run_segment import RunSegment


class Run(Base):
    """A data-taking run: one continuous acquisition with its detector and
    beamline settings. Pixels participate in a run through RunPixel."""

    __tablename__ = "runs"

    # Natural primary key: users query by run number, and run numbers are
    # never reused, so there is no surrogate id.
    run_number: Mapped[int] = mapped_column(primary_key=True)

    udet_bias: Mapped[Optional[float]]
    ldet_bias: Mapped[Optional[float]]
    hv: Mapped[Optional[float]]
    main: Mapped[Optional[float]]
    udet: Mapped[Optional[float]]

    # timestamptz: slow controls reports tz-aware times (US/Eastern).
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    number_subruns: Mapped[Optional[int]]  # lastsubrun in slow controls

    # Source-frame position is NOT here: a long rastering run holds many
    # positions, so positions live on run_segments (one per dwell).
    exb: Mapped[Optional[float]]

    udet_armor: Mapped[Optional[float]]
    ldet_armor: Mapped[Optional[float]]
    udet_ring: Mapped[Optional[float]]
    ldet_ring: Mapped[Optional[float]]
    udet_leakage: Mapped[Optional[float]]
    ldet_leakage: Mapped[Optional[float]]

    segments: Mapped[List["RunSegment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
        order_by="RunSegment.segment_index",
    )
    # Convenience read-only view of every run_pixel across the run's
    # segments (a pixel appears once per segment).
    run_pixels: Mapped[List["RunPixel"]] = relationship(
        primaryjoin="Run.run_number == foreign(RunPixel.run_number)",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"Run(run_number={self.run_number})"
