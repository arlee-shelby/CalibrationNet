from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .run_pixel import RunPixel


class Pixel(Base):
    """A physical pixel on one of the two detectors in the Nab Experiment. Exists once.

    Numbering convention: upper-detector pixels are 1-127; lower-detector
    pixels are the same number + 1000 (1001-1127). Board channel mapping is stored per
    run on RunPixel, since it can occasionally change on a per run basis.

    Preamp/FET mapping is quasi-static and lives here (seeded from data/electronics_mapping.csv).
    It is unlikely preamp and FET maps will change, but if they were to, these values can be
    updated and the old maps survive through the electronics_mapping.csv file git history. The database
    doesn't store any per-run history of the preamp/FET maps.

    Note, pixel 0 which is in the raw hdf5 files is a catch-all for board channels
    with no pixel. It is not included in the database.
    """

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

    # natural primary key: pixel numbers are unique across both detectors
    # (1-127 upper, 1001-1127 lower) and never change
    pixel_number: Mapped[int] = mapped_column(primary_key=True)
    detector: Mapped[str] = mapped_column(String(10))  # "upper" | "lower"

    # quasi-static mapping (labels like "G6"/"F2" encode the channel numbers and preamp/FET label).
    # lives here, not on run_pixels, because re-mapping is rare; if it ever
    # happens, update these values — per-run history isn't kept.
    preamp: Mapped[Optional[str]] = mapped_column(String(50))
    fet: Mapped[Optional[str]] = mapped_column(String(50))

    run_pixels: Mapped[List["RunPixel"]] = relationship(back_populates="pixel", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Pixel(pixel_number={self.pixel_number}, detector={self.detector})"
