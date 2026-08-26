from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .covariance import CovarianceMixin

if TYPE_CHECKING:
    from .adc_peak import ADCPeak
    from .trap_filter_output import TrapFilterOutput


class SpectrumFit(CovarianceMixin, Base):
    """A fit from a trap filter output spectrum (i.e. conversion electron CE and Auger fits
    are stored as separate SpectrumFit objects even though they come from the same
    trap filter output). The role of this table is to store all the results from the fits (i.e.
    not just the peak information, which is extracted from the fit, but also other relevant
    information).

    Information stored in a row of this table: "pars"/"par_errors" are the fitted values and errors
    for the parameters in the fit (fixed parameters like num_peaks have no error).
    "var_names" lists the VARIED parameters in the order they were added to lmfit. "covariance" is the
    covariance matrix for the parameters in the fit, in that same order. Parameter correlations are NOT stored
    but are calculated using correlations() from CovarianceMixin which derives them on demand from the
    covariance matrix stored here, exactly matching lmfit's .correl, so nothing can ever
    disagree. See docs/fit_storage.md for a worked example. Each SpectrumFit row also stores the chi2, reduced chi2,
    and number of degrees of freedom for the fit.
    """

    __tablename__ = "spectrum_fits"

    id: Mapped[int] = mapped_column(primary_key=True)
    trap_filter_output_id: Mapped[int] = mapped_column(ForeignKey("trap_filter_outputs.id"), index=True)

    # specific label for fit type, ex: "ce-6peak", "auger-2peak"
    label: Mapped[Optional[str]] = mapped_column(String(50))

    # the fit range used for the fit (ADC)
    fit_range_low: Mapped[Optional[int]]
    fit_range_high: Mapped[Optional[int]]

    n_peaks: Mapped[Optional[int]]
    chi2: Mapped[Optional[float]]
    ndf: Mapped[Optional[int]]
    reduced_chi2: Mapped[Optional[float]]

    # boolean which indicates if the fit failed or succeeded
    # used during the fit process to only add fits which have succeeded to the database
    success: Mapped[Optional[bool]]

    # {name: value} / {name: stderr}
    pars: Mapped[Optional[dict]] = mapped_column(JSONB)
    par_errors: Mapped[Optional[dict]] = mapped_column(JSONB)

    # varied-parameter names, in the order added to lmfit and of the covariance matrix rows/columns.
    var_names: Mapped[Optional[list]] = mapped_column(JSONB)
    covariance: Mapped[Optional[list]] = mapped_column(JSONB)

    # all the inputs that produced this fit (bounds, peak-finder settings, initial widths, ...)
    # so any fit can be reproduced exactly as it was made
    config: Mapped[Optional[dict]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    trap_filter_output: Mapped["TrapFilterOutput"] = relationship(back_populates="fits")
    adc_peaks: Mapped[List["ADCPeak"]] = relationship(back_populates="spectrum_fit", cascade="all, delete-orphan")

    @classmethod
    def from_lmfit(cls, result, *, trap_filter_output=None, label=None, fit_range=(None, None), config=None) -> "SpectrumFit":
        """Build a database row from an lmfit Minimizer result, storing exactly what the minimizer reported."""
        covariance = getattr(result, "covar", None)
        return cls(
            trap_filter_output=trap_filter_output,
            label=label,
            fit_range_low=fit_range[0],
            fit_range_high=fit_range[1],
            n_peaks=(int(result.params["num_peaks"].value)
                     if "num_peaks" in result.params else None),
            chi2=float(result.chisqr),
            ndf=int(result.nfree),
            reduced_chi2=float(result.redchi),
            success=bool(result.success),
            pars={name: param.value for name, param in result.params.items()},
            par_errors={name: param.stderr
                        for name, param in result.params.items()},
            var_names=list(result.var_names),
            covariance=(covariance.tolist()
                        if covariance is not None else None),
            config=config,
        )

    @property
    def run_pixel(self):
        """Shortcut through the trap filter output to run pixel when you already
        have the SpectrumFit, i.e. you cannot use this property to FILTER by run or pixel
        in SQL. To do that, you'd need to use joins for the chain instead (see calibrationnet/queries.py).
        """
        return self.trap_filter_output.run_pixel

    def __repr__(self) -> str:
        return (
            f"SpectrumFit(trap_filter_output_id={self.trap_filter_output_id},"
            f" label={self.label}, reduced_chi2={self.reduced_chi2})"
        )
