from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import Calibration
    from .run_pixel import RunPixel
    from .spectrum_fit import SpectrumFit


class TrapFilterOutput(Base):
    """One application of the trapezoidal filter to a run pixel's raw
    waveforms: the filter settings used, and the resulting energy (ADC
    units) for every waveform. The filter is typically applied many times
    with different settings to find the best ones, so each application is
    its own row. Histograms of the output are built from `energies` on
    demand rather than stored."""

    __tablename__ = "trap_filter_outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_pixel_id: Mapped[int] = mapped_column(
        ForeignKey("run_pixels.id"), index=True
    )

    # Units: 4 ns time bins (the DAQ sample width) — e.g. the standard nabPy
    # setting rise/flattop/fall = 1250/50/1250 bins = 5000/200/5000 ns.
    # Stored unconverted, as used in analysis and filenames.
    trap_rise: Mapped[Optional[float]]
    trap_flattop: Mapped[Optional[float]]
    trap_falltime: Mapped[Optional[float]]

    # Why this output is stored: the full rise/flattop scan (~520 settings
    # per run) lives in CSV files on disk, and only curated outputs are
    # ingested — e.g. "optimized" (per-pixel best settings, used for
    # calibration) or "comparison".
    label: Mapped[Optional[str]] = mapped_column(String(50))
    source_file: Mapped[Optional[str]] = mapped_column(String(255))

    # One entry per waveform: the trap filter's energy estimate, ADC units.
    # deferred: this column is ~99% of the table's bytes (TOASTed out of
    # line by Postgres), so ORM queries skip it unless explicitly loaded —
    # use session.query(...).options(undefer(TrapFilterOutput.energies))
    # or select specific columns / array_length() for counts.
    energies: Mapped[Optional[list]] = mapped_column(
        ARRAY(DOUBLE_PRECISION), deferred=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    run_pixel: Mapped["RunPixel"] = relationship(
        back_populates="trap_filter_outputs"
    )
    fits: Mapped[List["SpectrumFit"]] = relationship(
        back_populates="trap_filter_output", cascade="all, delete-orphan"
    )
    # A calibration is tied to ONE output: the ADC scale is a property
    # of the trap setting, so all of its points share this output.
    calibrations: Mapped[List["Calibration"]] = relationship(
        back_populates="trap_filter_output"
    )

    def __repr__(self) -> str:
        return (
            f"TrapFilterOutput(run_pixel_id={self.run_pixel_id}, "
            f"rise={self.trap_rise}, flattop={self.trap_flattop}, "
            f"fall={self.trap_falltime})"
        )
