from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .covariance import CovarianceMixin

if TYPE_CHECKING:
    from .adc_peak import ADCPeak
    from .run_pixel import RunPixel
    from .source import KeVPeak
    from .trap_filter_output import TrapFilterOutput


class Calibration(CovarianceMixin, Base):
    """An ADC -> keV calibration (linear or quadratic) for one run_pixel.

    A calibration deliberately does not link to a single spectrum_fit,
    because the calibration points come from multiple fits (i.e. the conversion electron (CE)
    and the Auger fits). So, which fits contributed to one calibration is recorded per point, through
    calibration_points -> adc_peak -> spectrum_fit. Calibrations do link to a single
    trap_filter_output because every point of a calibration must come from fits of the same
    output — and "calibrations for this pixel at these settings" becomes
    a direct join.

    Multiple calibrations can be stored for the same run pixel, i.e. by using different trap settings,
    using different "known" keV energies (ex: from simulation vs. nndc values). They are
    distinguished by "label" (ex: "nndc", "jin2026a", etc.) which are permanent.
    Calibrations are distinguished by an identity: trap filter output, type, label. Re-running a
    label replaces its calibration in place, i.e. to store a new calibration, you must have a
    different label.

    The calibration parameter storage follows the same pattern as spectrum_fits.
    The coefficients and their error get dedicated columns, "covariance" gives the parameter
    covariance matrix and together with "var_names" can be used to calculate the correlations,
    which are derived on demand by correlations() (from CovarianceMixin), but not stored.
    Every fit stored in this database uses lmfit with scale_covar=False (so that uncertainty scaling
    by the square root of the reduced chi2 is manual, not a default). Whether and when to scale is
    up to the analyst's later decision. See docs/fit_storage.md.
    """

    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_pixel_id: Mapped[int] = mapped_column(ForeignKey("run_pixels.id"), index=True)
    trap_filter_output_id: Mapped[int] = mapped_column(ForeignKey("trap_filter_outputs.id"), index=True)

    # which calibration identity this is, e.g. "jin2026a"
    label: Mapped[Optional[str]] = mapped_column(String(50))

    calibration_type: Mapped[str] = mapped_column(String(20))  # "linear" | "quadratic"

    # the fitted calibration parameters: keV = constant + linear*ADC (+ quadratic*ADC^2)
    constant_term: Mapped[Optional[float]]
    constant_error: Mapped[Optional[float]]
    linear_term: Mapped[Optional[float]]
    linear_error: Mapped[Optional[float]]
    quadratic_term: Mapped[Optional[float]]
    quadratic_error: Mapped[Optional[float]]

    chi2: Mapped[Optional[float]]
    ndf: Mapped[Optional[int]]
    reduced_chi2: Mapped[Optional[float]]
    success: Mapped[Optional[bool]]

    # varied-parameter names, in the order they were added to lmfit and of
    # the covariance matrix rows/columns
    # (['constant', 'linear'] for linear, + 'quadratic' for quadratic)
    var_names: Mapped[Optional[list]] = mapped_column(JSONB)
    covariance: Mapped[Optional[list]] = mapped_column(JSONB)

    # the fit inputs that don't have a dedicated column (weighting choices,
    # minimizer settings, ...) so any calibration can be exactly reproduced
    config: Mapped[Optional[dict]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run_pixel: Mapped["RunPixel"] = relationship(back_populates="calibrations")
    trap_filter_output: Mapped["TrapFilterOutput"] = relationship(back_populates="calibrations")
    points: Mapped[List["CalibrationPoint"]] = relationship(back_populates="calibration", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"Calibration(id={self.id}, label={self.label}, "
            f"type={self.calibration_type})"
        )


class CalibrationPoint(Base):
    """One (x, y) point of a calibration fit: the measured ADC centroid
    (adc_peak, which comes from the spectrum fit) paired with the assumed keV
    value (kev_peak, which by default comes from nndc, but can also come from simulation)
    for the same decay line. The direct link to a KeVPeak row makes calibration reproducible.

    This class is grouped with "Calibration" as the two exist in tandem.
    """

    __tablename__ = "calibration_points"
    __table_args__ = (UniqueConstraint("calibration_id", "adc_peak_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    calibration_id: Mapped[int] = mapped_column(ForeignKey("calibrations.id"), index=True)
    adc_peak_id: Mapped[int] = mapped_column(ForeignKey("adc_peaks.id"), index=True)
    kev_peak_id: Mapped[int] = mapped_column(ForeignKey("kev_peaks.id"), index=True)
    calibration: Mapped["Calibration"] = relationship(back_populates="points")
    adc_peak: Mapped["ADCPeak"] = relationship(back_populates="calibration_points")
    kev_peak: Mapped["KeVPeak"] = relationship(back_populates="calibration_points")

    def __repr__(self) -> str:
        return (
            f"CalibrationPoint(calibration_id={self.calibration_id}, "
            f"adc_peak_id={self.adc_peak_id})"
        )
