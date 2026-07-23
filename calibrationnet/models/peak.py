from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import CalibrationPoint
    from .source import IsotopePeak
    from .spectrum_fit import SpectrumFit


class Peak(Base):
    """One fitted peak extracted from a SpectrumFit, in ADC units, matched
    to the isotope peak it corresponds to. Its centroid +- error, paired
    with a known energy (PeakEnergy), is what feeds a calibration."""

    __tablename__ = "peaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    spectrum_fit_id: Mapped[int] = mapped_column(
        ForeignKey("spectrum_fits.id"), index=True
    )
    isotope_peak_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("isotope_peaks.id"), index=True
    )

    centroid_adc: Mapped[float]
    centroid_error_adc: Mapped[Optional[float]]
    sigma_adc: Mapped[Optional[float]]
    sigma_error_adc: Mapped[Optional[float]]
    amplitude: Mapped[Optional[float]]
    amplitude_error: Mapped[Optional[float]]

    spectrum_fit: Mapped["SpectrumFit"] = relationship(
        back_populates="peaks"
    )
    isotope_peak: Mapped[Optional["IsotopePeak"]] = relationship(
        back_populates="measured_peaks"
    )
    calibration_points: Mapped[List["CalibrationPoint"]] = relationship(
        back_populates="peak"
    )

    def __repr__(self) -> str:
        return f"Peak(centroid_adc={self.centroid_adc})"
