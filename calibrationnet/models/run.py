from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .pixel import Pixel
    from .run_pixel import RunPixel


class Run(Base):
    """A data-taking run: one continuous acquisition with its detector and
    beamline settings. Pixels participate in a run through RunPixel."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_number: Mapped[int] = mapped_column(unique=True, index=True)

    udet_bias: Mapped[Optional[float]]
    ldet_bias: Mapped[Optional[float]]
    hv: Mapped[Optional[float]]
    main: Mapped[Optional[float]]
    udet: Mapped[Optional[float]]

    # timestamptz: slow controls reports tz-aware times (US/Eastern).
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    number_subruns: Mapped[Optional[int]]  # lastsubrun in slow controls

    linear_position: Mapped[Optional[float]]
    horizontal_position: Mapped[Optional[float]]
    exb: Mapped[Optional[float]]

    udet_armor: Mapped[Optional[float]]
    ldet_armor: Mapped[Optional[float]]
    udet_ring: Mapped[Optional[float]]
    ldet_ring: Mapped[Optional[float]]
    udet_leakage: Mapped[Optional[float]]
    ldet_leakage: Mapped[Optional[float]]

    run_pixels: Mapped[List["RunPixel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    # Convenience read-only view of the pixels in this run.
    pixels: Mapped[List["Pixel"]] = relationship(
        secondary="run_pixels", viewonly=True
    )

    def __repr__(self) -> str:
        return f"Run(run_number={self.run_number})"
