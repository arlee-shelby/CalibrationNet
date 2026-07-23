from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import Calibration
    from .pixel import Pixel
    from .run import Run
    from .source import Source
    from .trap_filter_output import TrapFilterOutput


class RunPixel(Base):
    """One pixel's participation in one run (association object for the
    runs<->pixels many-to-many). Holds the per-run state: which source was
    centered over the pixel and the board channel (which changes run to
    run, unlike the quasi-static preamp/FET wiring stored on Pixel). The
    analysis chain (trap filter outputs -> spectrum fits -> peaks ->
    calibrations) hangs off this table."""

    __tablename__ = "run_pixels"
    __table_args__ = (
        UniqueConstraint("run_id", "pixel_id"),
        # A board channel maps to exactly one pixel within a run.
        UniqueConstraint("run_id", "board_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    pixel_id: Mapped[int] = mapped_column(ForeignKey("pixels.id"), index=True)
    # The source centered over this pixel for this run.
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id"), index=True
    )

    # Assigned per run, read from the run's data file.
    board_channel: Mapped[Optional[int]]

    run: Mapped["Run"] = relationship(back_populates="run_pixels")
    pixel: Mapped["Pixel"] = relationship(back_populates="run_pixels")
    source: Mapped[Optional["Source"]] = relationship(
        back_populates="run_pixels"
    )
    trap_filter_outputs: Mapped[List["TrapFilterOutput"]] = relationship(
        back_populates="run_pixel", cascade="all, delete-orphan"
    )
    calibrations: Mapped[List["Calibration"]] = relationship(
        back_populates="run_pixel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"RunPixel(run_id={self.run_id}, pixel_id={self.pixel_id})"
