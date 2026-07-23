from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .run import Run
    from .run_pixel import RunPixel


class Pixel(Base):
    """A physical pixel on one of the two detectors. Exists once.

    Numbering convention: upper-detector pixels are 1-127; lower-detector
    pixels are the same number + 1000 (1001-1127). Wiring (board channel,
    preamp, FET) is stored per run on RunPixel, since it occasionally
    changes between runs."""

    __tablename__ = "pixels"
    __table_args__ = (
        CheckConstraint(
            "pixel_number BETWEEN 1 AND 127 "
            "OR pixel_number BETWEEN 1001 AND 1127",
            name="ck_pixels_number_range",
        ),
        CheckConstraint(
            "(detector = 'upper' AND pixel_number <= 127) "
            "OR (detector = 'lower' AND pixel_number >= 1001)",
            name="ck_pixels_detector_matches_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pixel_number: Mapped[int] = mapped_column(unique=True, index=True)
    detector: Mapped[str] = mapped_column(String(10))  # "upper" | "lower"

    # Quasi-static wiring (labels like "G6"/"F2" encode the channel).
    # Lives here, not on run_pixels, because remapping is rare; if it ever
    # happens, update these values — per-run history isn't kept.
    preamp: Mapped[Optional[str]] = mapped_column(String(50))
    fet: Mapped[Optional[str]] = mapped_column(String(50))

    run_pixels: Mapped[List["RunPixel"]] = relationship(
        back_populates="pixel", cascade="all, delete-orphan"
    )
    runs: Mapped[List["Run"]] = relationship(
        secondary="run_pixels", viewonly=True
    )

    def __repr__(self) -> str:
        return f"Pixel(pixel_number={self.pixel_number}, detector={self.detector})"
