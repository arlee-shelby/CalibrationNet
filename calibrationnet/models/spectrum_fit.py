from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import Calibration
    from .peak import Peak
    from .trap_filter_output import TrapFilterOutput


class SpectrumFit(Base):
    """A fit of all the peaks in one trap filter output's spectrum. The
    number of parameters varies with how many peaks the source produces,
    so the full parameter set is stored as JSONB; the per-peak results
    used for calibration are broken out into Peak rows."""

    __tablename__ = "spectrum_fits"

    id: Mapped[int] = mapped_column(primary_key=True)
    trap_filter_output_id: Mapped[int] = mapped_column(
        ForeignKey("trap_filter_outputs.id"), index=True
    )

    chi2: Mapped[Optional[float]]
    ndf: Mapped[Optional[int]]
    pars: Mapped[Optional[dict]] = mapped_column(JSONB)
    par_errors: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    trap_filter_output: Mapped["TrapFilterOutput"] = relationship(
        back_populates="fits"
    )
    peaks: Mapped[List["Peak"]] = relationship(
        back_populates="spectrum_fit", cascade="all, delete-orphan"
    )
    calibrations: Mapped[List["Calibration"]] = relationship(
        back_populates="spectrum_fit", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"SpectrumFit(trap_filter_output_id={self.trap_filter_output_id}, "
            f"chi2={self.chi2})"
        )
