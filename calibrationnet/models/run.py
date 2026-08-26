from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# Never executed at runtime — lets type checkers resolve the quoted class names below
# without circular imports.
if TYPE_CHECKING:
    from .run_pixel import RunPixel
    from .run_segment import RunSegment


class Run(Base):
    """A Nab calibration run: one continuous acquisition with its detector and
    general settings. Pixels participate in a run through RunPixel."""

    __tablename__ = "runs"

    # natural primary key: users query by run number, and run numbers are never reused
    run_number: Mapped[int] = mapped_column(primary_key=True)

    # units and sign conventions:
    # biases in volts (generally negative, e.g. -300); hv in kilovolts (generally negative,
    # e.g. -27); main/udet magnet currents in amps; exb in volts; temperatures in kelvin; leakage currents in micro amps
    udet_bias: Mapped[Optional[float]]
    ldet_bias: Mapped[Optional[float]]
    hv: Mapped[Optional[float]]
    main: Mapped[Optional[float]]
    udet: Mapped[Optional[float]]

    # timestamptz: slow controls reports time-zone-aware times (US/Eastern)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # lastsubrun in slow controls plus 1 (subrun number starts with 0)
    # the +1 increment is embedded during run ingestion, in slow_controls.py
    number_subruns: Mapped[Optional[int]]

    exb: Mapped[Optional[float]]
    udet_armor: Mapped[Optional[float]]
    ldet_armor: Mapped[Optional[float]]
    udet_ring: Mapped[Optional[float]]
    ldet_ring: Mapped[Optional[float]]
    udet_leakage: Mapped[Optional[float]]
    ldet_leakage: Mapped[Optional[float]]

    # a run segment is defined as a part of a calibration run where the source position was unchanged
    # i.e. in some 2026 runs, with the new automation, continuous runs have multiple positions within it
    segments: Mapped[List["RunSegment"]] = relationship(back_populates="run", cascade="all, delete-orphan",order_by="RunSegment.segment_index",)

    # convenience read-only join of every run_pixel across the run's segments
    # (a pixel appears once per segment). Skips join through run_segments
    # when requesting pixel information about a run
    run_pixels: Mapped[List["RunPixel"]] = relationship(primaryjoin="Run.run_number == foreign(RunPixel.run_number)",viewonly=True,)

    def __repr__(self) -> str:
        return f"Run(run_number={self.run_number})"
