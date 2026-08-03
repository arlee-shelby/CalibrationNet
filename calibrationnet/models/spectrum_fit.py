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
    """One fit of PART of a trap filter output's spectrum.

    A single output usually takes several fits: e.g. the six high-intensity
    conversion-electron peaks are fit simultaneously over one ADC window,
    and the low-energy Auger peaks separately over another. Each such fit
    is one row here — `label` says which fit it is and fit_range_low/high
    say which ADC window it covered — and the ADC peaks broken out of all of a
    run_pixel's fits together feed one calibration.

    Uncertainty bookkeeping: `pars`/`par_errors` cover every parameter
    (fixed ones like num_peaks have no error). `var_names` lists the VARIED
    parameters in lmfit's order, and `covariance` is the matrix over
    exactly those, in that order. Parameter correlations are NOT stored:
    correlations() (from CovarianceMixin) derives them on demand from the
    covariance, exactly matching lmfit's .correl, so nothing can ever
    disagree. See docs/fit_storage.md for a worked example.
    """

    __tablename__ = "spectrum_fits"

    id: Mapped[int] = mapped_column(primary_key=True)
    trap_filter_output_id: Mapped[int] = mapped_column(
        ForeignKey("trap_filter_outputs.id"), index=True
    )

    # Which of the output's fits this is, e.g. "ce-6peak", "auger-2peak".
    label: Mapped[Optional[str]] = mapped_column(String(50))
    # The fitted ADC window (histogram bin bounds).
    fit_range_low: Mapped[Optional[int]]
    fit_range_high: Mapped[Optional[int]]
    n_peaks: Mapped[Optional[int]]

    # Goodness of fit, straight from the minimizer.
    chi2: Mapped[Optional[float]]
    ndf: Mapped[Optional[int]]
    reduced_chi2: Mapped[Optional[float]]
    success: Mapped[Optional[bool]]

    # {name: value} / {name: stderr} for ALL parameters.
    pars: Mapped[Optional[dict]] = mapped_column(JSONB)
    par_errors: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Varied-parameter names, in the order of covariance's rows/columns.
    var_names: Mapped[Optional[list]] = mapped_column(JSONB)
    covariance: Mapped[Optional[list]] = mapped_column(JSONB)

    # Inputs that produced this fit (bounds, peak-finder settings, initial
    # widths, ...) so any fit can be reproduced exactly.
    config: Mapped[Optional[dict]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    trap_filter_output: Mapped["TrapFilterOutput"] = relationship(
        back_populates="fits"
    )
    adc_peaks: Mapped[List["ADCPeak"]] = relationship(
        back_populates="spectrum_fit", cascade="all, delete-orphan"
    )

    @classmethod
    def from_lmfit(cls, result, *, trap_filter_output=None, label=None,
                   fit_range=(None, None), config=None) -> "SpectrumFit":
        """Build a row from an lmfit MinimizerResult, storing exactly what
        the minimizer reported."""
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
        """Shortcut through the output — handy in plotting loops
        (fit.run_pixel.run_number / .pixel_number). To FILTER by run or
        pixel in SQL, join through TrapFilterOutput to RunPixel instead
        (see calibrationnet/queries.py)."""
        return self.trap_filter_output.run_pixel

    def __repr__(self) -> str:
        return (
            f"SpectrumFit(trap_filter_output_id={self.trap_filter_output_id},"
            f" label={self.label}, reduced_chi2={self.reduced_chi2})"
        )
