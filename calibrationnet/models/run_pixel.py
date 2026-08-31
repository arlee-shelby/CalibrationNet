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
    """Each row in this table is a pixel recorded in the board-channel-pixel map for the run from
    the raw hdf5 files, and recorded per segment, i.e. in one period of
    constant source position (acts as an association object for the
    run_segments<->pixels many-to-many relationship).

    Holds the per-segment state, i.e. the board channel (which is assigned per run so repeats
    across a run's segments) and a nullable prediction of which source was centered over the pixel
    (which is what changes when the sources move between segments). Note, the prediction of which source
    was centered over the pixel comes from source_assignment.py and acts only as a predictor for future analysis work.
    The quasi-static preamp/FET mapping lives on Pixel instead. The analysis chain
    (trap filter outputs -> spectrum fits -> peaks -> calibrations) comes from this table, i.e. all analysis works with run_pixels.

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
        # A board channel maps to exactly one pixel within a run and thus a segment.
        UniqueConstraint("run_number", "segment_index", "board_channel"),
    )

    # surrogate key: the analysis chain below hangs off this one column which identifies a
    # (run, run_segment, pixel) row, rather than repeating a three-part natural key everywhere
    id: Mapped[int] = mapped_column(primary_key=True)
    run_number: Mapped[int] = mapped_column(index=True)
    segment_index: Mapped[int] = mapped_column(default=0, index=True)
    pixel_number: Mapped[int] = mapped_column(ForeignKey("pixels.pixel_number"), index=True)

    # The physical source centered over this pixel during this segment (i.e. not just specific isotope,
    # but the exact physical source)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), index=True)

    # derived and assigned per run, read from the raw hdf5 files
    board_channel: Mapped[Optional[int]]

    segment: Mapped["RunSegment"] = relationship(back_populates="run_pixels")
    pixel: Mapped["Pixel"] = relationship(back_populates="run_pixels")
    source: Mapped[Optional["Source"]] = relationship(back_populates="run_pixels")
    trap_filter_outputs: Mapped[List["TrapFilterOutput"]] = relationship(back_populates="run_pixel", cascade="all, delete-orphan")
    calibrations: Mapped[List["Calibration"]] = relationship(back_populates="run_pixel", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"RunPixel(run={self.run_number}, seg={self.segment_index}, "
            f"pixel={self.pixel_number})"
        )
