from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .adc_peak import ADCPeak
    from .calibration import CalibrationPoint
    from .run_pixel import RunPixel


class Isotope(Base):
    """An isotope used for calibration (ex: Bi-207, Sn-113). The peaks it
    produces are physics of the isotope. The physical
    sources of this isotope are Source rows.

    This class is grouped with the others in this file as they are all directly related
    to source specific information.
    """

    __tablename__ = "isotopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)  # ex: "Bi-207"

    decay_energies: Mapped[List["IsotopeDecayEnergy"]] = relationship(back_populates="isotope", cascade="all, delete-orphan")
    sources: Mapped[List["Source"]] = relationship(back_populates="isotope")

    def __repr__(self) -> str:
        return f"Isotope(name={self.name})"


class Source(Base):
    """A specific physical calibration source, i.e. the manufactured item
    from EzIsotope. The "label" and "serial_number" store the identifying information as recorded
    in the source control application. There can be many sources of the same isotope, and
    simulation-updated known energies are specific to one physical source because each physical source
    can have different Mylar, aluminum, and carrier thicknesses, i.e. different loss corrections. So, runs
    attempt to record which actual source sat over which pixel (see assign_sources.py for details).

    This class is grouped with the others in this file as they are all directly related
    to source specific information.
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_id: Mapped[int] = mapped_column(ForeignKey("isotopes.id"), index=True)

    # the label plus serial number specify the identification properties of one physical
    # source, as reported in the source control application
    label: Mapped[str] = mapped_column(String(50), unique=True)
    serial_number: Mapped[str] = mapped_column(String(50), unique=True)

    notes: Mapped[Optional[str]]

    isotope: Mapped["Isotope"] = relationship(back_populates="sources")
    run_pixels: Mapped[List["RunPixel"]] = relationship(back_populates="source")
    kev_peaks: Mapped[List["KeVPeak"]] = relationship(back_populates="source")
    installations: Mapped[List["SourceInstallation"]] = relationship(back_populates="source", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Source({self.label}, serial={self.serial_number})"


class SourceInstallation(Base):
    """Each row of this table is one source mounted in the source frame for a
    specific installation. The "removed_on" column is NULL for
    the current installation.

    The slot convention (see README "Source frame slot convention") uses labels
    "R<row>C<col>" in the frame's Facing UP orientation, i.e. the view
    from the upper detector with the handle at the bottom (which comes from the convention used
    in the elog for source installations starting from 10/2025 to present). Rows are numbered
    from 1 at the top (farthest from the handle) and columns from 1 at the left. The rule generalizes
    to any holder shape (a single vertical stick is R1C1, R2C1, ...). The lower detector sees this mirrored
    left-right.

    This class is grouped with the others in this file as they are all directly related
    to source specific information.
    """

    __tablename__ = "source_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    installed_on: Mapped[date]
    removed_on: Mapped[Optional[date]]
    slot: Mapped[str] = mapped_column(String(20))

    # the physical tray that the sources were installed with, i.e. "3-slot", "5-slot", "6-slot"
    # the spacing between slots is a property of the tray (the 6-slot one is
    # ~0.15 inch longer) and the source assignment uses the frame geometry to predict which source
    # sat on a particular pixel
    holder: Mapped[Optional[str]] = mapped_column(String(20))
    notes: Mapped[Optional[str]]

    source: Mapped["Source"] = relationship(back_populates="installations")

    def __repr__(self) -> str:
        return (
            f"SourceInstallation(source_id={self.source_id}, "
            f"slot={self.slot}, installed_on={self.installed_on})"
        )


class IsotopeDecayEnergy(Base):
    """One energy line an isotope's decay produces (ex: Bi-207 CE-K 976).
    This is the line's reference only — the exact keV values used for calibrations
    live in KeVPeak table, which is versioned and may be specific to a physical source.

    This class is grouped with the others in this file as they are all directly related
    to source specific information.
    """

    __tablename__ = "isotope_decay_energies"
    __table_args__ = (UniqueConstraint("isotope_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_id: Mapped[int] = mapped_column(ForeignKey("isotopes.id"), index=True)
    label: Mapped[str] = mapped_column(String(50))  # e.g. "CE 976"

    # intensity is copied from the nndc reported values (in percent), which is a "stable" property
    # of the line because the simulated values have not yet been included (and are
    # unlikely to be)
    # used in part to predict which lines have too low-statistics to be fit
    intensity: Mapped[Optional[float]]
    intensity_error: Mapped[Optional[float]]

    isotope: Mapped["Isotope"] = relationship(back_populates="decay_energies")
    kev_peaks: Mapped[List["KeVPeak"]] = relationship(back_populates="isotope_decay_energy", cascade="all, delete-orphan")
    adc_peaks: Mapped[List["ADCPeak"]] = relationship(back_populates="isotope_decay_energy")

    def __repr__(self) -> str:
        return (f"IsotopeDecayEnergy(isotope_id={self.isotope_id}, "
                f"label={self.label})")


class KeVPeak(Base):
    """A "known" energy value, in keV, for one isotope decay line — the
    keV side of a calibration point. The nndc/literature values are applied to the
    isotope by default, but the corrected values, from simulation,
    can be specific to one physical source. Old rows are
    never overwritten and new values are new rows. Each calibration
    records exactly which keV rows it used, so any past calibration can be reproduced.

    This class is grouped with the others in this file as they are all directly related
    to source specific information.
    """

    __tablename__ = "kev_peaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_decay_energy_id: Mapped[int] = mapped_column(ForeignKey("isotope_decay_energies.id"), index=True)

    # source_id specifies a specific physical source, if NULL = generic literature value (nndc)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sources.id"), index=True)

    # simulated keV values can be detector-dependent (i.e. in the NabSim)
    # specified as upper or lower
    detector: Mapped[Optional[str]] = mapped_column(String(10))

    # simulated keV values can be HV dependent (i.e. in the NabSim)
    hv_kv: Mapped[Optional[int]]

    energy_kev: Mapped[float]
    energy_error_kev: Mapped[Optional[float]]
    origin: Mapped[str] = mapped_column(String(20))  # "nndc" | "simulation"
    version: Mapped[Optional[str]] = mapped_column(String(50))  # ex: "Jin-2026a"
    notes: Mapped[Optional[str]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    isotope_decay_energy: Mapped["IsotopeDecayEnergy"] = relationship(back_populates="kev_peaks")
    source: Mapped[Optional["Source"]] = relationship(back_populates="kev_peaks")
    calibration_points: Mapped[List["CalibrationPoint"]] = relationship(back_populates="kev_peak")

    def __repr__(self) -> str:
        return (
            f"KeVPeak(isotope_decay_energy_id={self.isotope_decay_energy_id},"
            f" {self.energy_kev} keV, origin={self.origin})"
        )
