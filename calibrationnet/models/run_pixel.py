from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import Calibration
    from .pixel import Pixel
    from .run_segment import RunSegment
    from .source import Source
    from .trap_filter_output import TrapFilterOutput


class RunPixel(Base):
    """One pixel's participation in one run segment — i.e. in one period of
    constant source position (association object for the
    run_segments<->pixels many-to-many).

    Holds the per-segment state: which source was centered over the pixel
    (that is exactly what changes when the frame moves) and the board
    channel, which is assigned per run and so repeats across a run's
    segments. The quasi-static preamp/FET wiring lives on Pixel instead.
    The analysis chain (trap filter outputs -> spectrum fits -> peaks ->
    calibrations) hangs off this table.

    run_number/pixel_number hold the natural keys of runs/pixels directly,
    so `WHERE run_number = 8622 AND pixel_number = 63` needs no joins.
    """

    __tablename__ = "run_pixels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_number", "segment_index"],
            ["run_segments.run_number", "run_segments.segment_index"],
        ),
        UniqueConstraint("run_number", "segment_index", "pixel_number"),
        # A board channel maps to exactly one pixel within a segment.
        UniqueConstraint("run_number", "segment_index", "board_channel"),
    )

    # Surrogate key: the analysis chain below hangs off this one column
    # rather than repeating a three-part natural key everywhere.
    id: Mapped[int] = mapped_column(primary_key=True)
    run_number: Mapped[int] = mapped_column(index=True)
    segment_index: Mapped[int] = mapped_column(default=0, index=True)
    pixel_number: Mapped[int] = mapped_column(
        ForeignKey("pixels.pixel_number"), index=True
    )
    # The source centered over this pixel during this segment.
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id"), index=True
    )

    # Assigned per run, read from the run's data file.
    board_channel: Mapped[Optional[int]]

    segment: Mapped["RunSegment"] = relationship(back_populates="run_pixels")
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
        return (
            f"RunPixel(run={self.run_number}, seg={self.segment_index}, "
            f"pixel={self.pixel_number})"
        )
