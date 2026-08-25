from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import CalibrationPoint
    from .source import IsotopeDecayEnergy
    from .spectrum_fit import SpectrumFit


class ADCPeak(Base):
    """One fitted peak extracted from a SpectrumFit, in ADC units — the
    ADC side of a calibration point. Its centroid value and error with the
    known keV value (KeVPeak) for the decay energy line they are matched to feeds a 
    calibration. Other relevant information about the peak (width and amplitude, 
    and their errors) is also stored with the peak centroid information. 
    """

    __tablename__ = "adc_peaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    spectrum_fit_id: Mapped[int] = mapped_column(ForeignKey("spectrum_fits.id"), index=True)
    isotope_decay_energy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("isotope_decay_energies.id"), index=True)

    centroid_adc: Mapped[float]
    centroid_error_adc: Mapped[Optional[float]]
    sigma_adc: Mapped[Optional[float]]
    sigma_error_adc: Mapped[Optional[float]]
    amplitude: Mapped[Optional[float]]
    amplitude_error: Mapped[Optional[float]]

    spectrum_fit: Mapped["SpectrumFit"] = relationship(back_populates="adc_peaks")
    isotope_decay_energy: Mapped[Optional["IsotopeDecayEnergy"]] = (relationship(back_populates="adc_peaks"))
    calibration_points: Mapped[List["CalibrationPoint"]] = relationship(back_populates="adc_peak")

    @property
    def run_pixel(self):
        """Shortcut through spectrum fit -> trap filter output -> run pixel when you 
        already have the ADCPeak, i.e. you cannot use this property to FILTER by run or pixel in SQL. 
        To do that, you'd need to use joins for the chain instead (see for examples calibrationnet/queries.py).
        """
        return self.spectrum_fit.trap_filter_output.run_pixel

    def __repr__(self) -> str:
        return f"ADCPeak(centroid_adc={self.centroid_adc})"
