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
    """Each row in this table is the output of the nabPy single trapezoidal filter for a run pixel's raw
    waveforms. It records the filter settings used and the resulting energy (ADC
    units) for every waveform. Various filter settings can be applied to the same run pixel's waveforms,
    so each application is its own row. Histograms of the output are built from "energies" on
    demand rather than stored.

    The "label" column is used to specify types of trap filter settings, for example "nabpy-standard" specifies
    the standard nabPy settings (risetime=1250,flat top=50,fall time=1250) which is different than the short trap
    filter settings used for the Fall 2025 data. The "source_file" column is used to specify the csv file either used
    or generated during the trap filter application. When applying the trap filter, a csv file with the energies of all
    the waveforms is made and after it is ingested into the database, it is deleted. If the ingestion fails, this column
    can be used to find the trap filter output file. In other cases, it can specify files stored on GT that were used to
    ingest the trap filter data.
    """

    __tablename__ = "trap_filter_outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_pixel_id: Mapped[int] = mapped_column(ForeignKey("run_pixels.id"), index=True)

    # units: 4 ns time bins (DAQ units) — i.e. the standard nabPy
    # setting rise/flattop/fall = 1250/50/1250 bins = 5000/200/5000 ns
    # and stored in the database unconverted (i.e. 1250,50,1250)
    trap_rise: Mapped[Optional[float]]
    trap_flattop: Mapped[Optional[float]]
    trap_falltime: Mapped[Optional[float]]

    # label specifies a trap filter identifier, i.e. "nabpy-standard" or "short-trap-Fall2025" etc. that
    # specifies groups of trap filter setting that were applied
    label: Mapped[Optional[str]] = mapped_column(String(50))

    # source_file gives the name of the csv file generated during the trap filter application
    # and ingestion. It can be used to name the trap filter output file when the ingestion fails
    source_file: Mapped[Optional[str]] = mapped_column(String(255))

    # one entry per waveform in the energy list (in ADC units)
    # note: this column is pretty big so ORM queries don't return the values in the "energies" column
    # unless explicitly requested: use session.query(...).options(undefer(TrapFilterOutput.energies))
    energies: Mapped[Optional[list]] = mapped_column(ARRAY(DOUBLE_PRECISION), deferred=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    run_pixel: Mapped["RunPixel"] = relationship(back_populates="trap_filter_outputs")
    fits: Mapped[List["SpectrumFit"]] = relationship(back_populates="trap_filter_output", cascade="all, delete-orphan")
    calibrations: Mapped[List["Calibration"]] = relationship(back_populates="trap_filter_output")

    def __repr__(self) -> str:
        return (
            f"TrapFilterOutput(run_pixel_id={self.run_pixel_id}, "
            f"rise={self.trap_rise}, flattop={self.trap_flattop}, "
            f"fall={self.trap_falltime})"
        )
