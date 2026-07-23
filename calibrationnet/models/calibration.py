from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .peak import Peak
    from .run_pixel import RunPixel
    from .source import PeakEnergy
    from .spectrum_fit import SpectrumFit


class Calibration(Base):
    """An ADC -> keV calibration (linear or quadratic) fit from one
    SpectrumFit's peak centroids against known energies. Multiple attempts
    are kept — different trap settings, and recalibrations when the known
    energies are updated from simulation. run_pixel_id duplicates what the
    spectrum_fit chain already implies, so that "the current calibration
    for this run pixel" is a single indexed query and so a partial unique
    index can enforce at most one is_current per (run_pixel, type).
    Exactly which (measured peak, known energy) pairs went in is recorded
    by CalibrationPoint."""

    __tablename__ = "calibrations"
    __table_args__ = (
        Index(
            "ix_calibrations_one_current",
            "run_pixel_id",
            "calibration_type",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spectrum_fit_id: Mapped[int] = mapped_column(
        ForeignKey("spectrum_fits.id"), index=True
    )
    run_pixel_id: Mapped[int] = mapped_column(
        ForeignKey("run_pixels.id"), index=True
    )

    calibration_type: Mapped[str] = mapped_column(String(20))  # "linear" | "quadratic"
    constant_term: Mapped[Optional[float]]
    constant_error: Mapped[Optional[float]]
    linear_term: Mapped[Optional[float]]
    linear_error: Mapped[Optional[float]]
    quadratic_term: Mapped[Optional[float]]
    quadratic_error: Mapped[Optional[float]]
    chi2: Mapped[Optional[float]]

    is_current: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    spectrum_fit: Mapped["SpectrumFit"] = relationship(
        back_populates="calibrations"
    )
    run_pixel: Mapped["RunPixel"] = relationship(
        back_populates="calibrations"
    )
    points: Mapped[List["CalibrationPoint"]] = relationship(
        back_populates="calibration", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"Calibration(id={self.id}, type={self.calibration_type}, "
            f"current={self.is_current})"
        )


class CalibrationPoint(Base):
    """One (measured centroid, assumed known energy) pair that fed a
    calibration. Linking to PeakEnergy — not just the isotope peak —
    records which version of the known values (NNDC vs. a simulation
    update for that specific physical source) was used, so every
    calibration stays reproducible."""

    __tablename__ = "calibration_points"
    __table_args__ = (UniqueConstraint("calibration_id", "peak_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    calibration_id: Mapped[int] = mapped_column(
        ForeignKey("calibrations.id"), index=True
    )
    peak_id: Mapped[int] = mapped_column(ForeignKey("peaks.id"), index=True)
    peak_energy_id: Mapped[int] = mapped_column(
        ForeignKey("peak_energies.id"), index=True
    )

    calibration: Mapped["Calibration"] = relationship(
        back_populates="points"
    )
    peak: Mapped["Peak"] = relationship(back_populates="calibration_points")
    peak_energy: Mapped["PeakEnergy"] = relationship(
        back_populates="calibration_points"
    )

    def __repr__(self) -> str:
        return (
            f"CalibrationPoint(calibration_id={self.calibration_id}, "
            f"peak_id={self.peak_id})"
        )
