from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func, text
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
    """An ADC -> keV calibration (linear or quadratic) for one run_pixel,
    fit from measured ADC centroids against known keV values.

    A calibration deliberately does NOT link to one spectrum_fit: its
    points come from SEVERAL fits (the CE window and the Auger window),
    so which fits contributed is recorded per point, through
    calibration_points -> adc_peak -> spectrum_fit. It DOES link to one
    trap_filter_output: the ADC scale is a property of the trap setting,
    so every point of a calibration must come from fits of the same
    output — and "calibrations for this pixel at these settings" becomes
    a direct join.

    Multiple attempts are kept — different trap settings, and
    recalibrations when the known energies are updated from simulation —
    distinguished by `label`; a partial unique index enforces at most one
    is_current per (run_pixel, calibration_type).

    Uncertainty bookkeeping follows the same pattern as spectrum_fits:
    the coefficients get dedicated columns (small, fixed set, directly
    queryable), `var_names` + `covariance` carry the full uncertainty
    structure, and correlations are derived on demand by correlations()
    (from CovarianceMixin), never stored. CONVENTION: every fit stored
    in this database uses lmfit with scale_covar=False — raw
    chi-square-weighted uncertainties, never rescaled by reduced chi2
    (lmfit's default WOULD rescale); whether and when to scale is
    always the analyst's later decision. See docs/fit_storage.md.
    """

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
    run_pixel_id: Mapped[int] = mapped_column(
        ForeignKey("run_pixels.id"), index=True
    )
    trap_filter_output_id: Mapped[int] = mapped_column(
        ForeignKey("trap_filter_outputs.id"), index=True
    )

    # Which calibration attempt this is, e.g. "nndc-2026", "sim-corrected".
    label: Mapped[Optional[str]] = mapped_column(String(50))
    calibration_type: Mapped[str] = mapped_column(String(20))  # "linear" | "quadratic"

    # The fitted coefficients: keV = constant + linear*ADC (+ quadratic*ADC^2).
    # UNITS: constant in keV; linear (the gain) in keV/ADC; quadratic in
    # keV/ADC^2. Errors share their coefficient's units.
    constant_term: Mapped[Optional[float]]
    constant_error: Mapped[Optional[float]]
    linear_term: Mapped[Optional[float]]
    linear_error: Mapped[Optional[float]]
    quadratic_term: Mapped[Optional[float]]
    quadratic_error: Mapped[Optional[float]]

    # Goodness of fit, straight from the minimizer.
    chi2: Mapped[Optional[float]]
    ndf: Mapped[Optional[int]]
    reduced_chi2: Mapped[Optional[float]]
    success: Mapped[Optional[bool]]

    # Varied-parameter names, in the order of covariance's rows/columns
    # (['constant', 'linear'] for linear, + 'quadratic' for quadratic).
    var_names: Mapped[Optional[list]] = mapped_column(JSONB)
    covariance: Mapped[Optional[list]] = mapped_column(JSONB)

    # Fit inputs that don't have a dedicated column (weighting choices,
    # minimizer settings, ...) so any calibration can be reproduced.
    config: Mapped[Optional[dict]] = mapped_column(JSONB)

    is_current: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run_pixel: Mapped["RunPixel"] = relationship(
        back_populates="calibrations"
    )
    trap_filter_output: Mapped["TrapFilterOutput"] = relationship(
        back_populates="calibrations"
    )
    points: Mapped[List["CalibrationPoint"]] = relationship(
        back_populates="calibration", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"Calibration(id={self.id}, label={self.label}, "
            f"type={self.calibration_type}, current={self.is_current})"
        )


class CalibrationPoint(Base):
    """One (x, y) point of a calibration fit: the measured ADC centroid
    (adc_peak) paired with the assumed keV value (kev_peak) for the same
    decay line. Linking to a specific KeVPeak row — not just the decay
    line — records which version of the known values (NNDC vs. a
    simulation correction for that physical source) was used, so every
    calibration stays reproducible."""

    __tablename__ = "calibration_points"
    __table_args__ = (UniqueConstraint("calibration_id", "adc_peak_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    calibration_id: Mapped[int] = mapped_column(
        ForeignKey("calibrations.id"), index=True
    )
    adc_peak_id: Mapped[int] = mapped_column(
        ForeignKey("adc_peaks.id"), index=True
    )
    kev_peak_id: Mapped[int] = mapped_column(
        ForeignKey("kev_peaks.id"), index=True
    )

    calibration: Mapped["Calibration"] = relationship(
        back_populates="points"
    )
    adc_peak: Mapped["ADCPeak"] = relationship(
        back_populates="calibration_points"
    )
    kev_peak: Mapped["KeVPeak"] = relationship(
        back_populates="calibration_points"
    )

    def __repr__(self) -> str:
        return (
            f"CalibrationPoint(calibration_id={self.calibration_id}, "
            f"adc_peak_id={self.adc_peak_id})"
        )
