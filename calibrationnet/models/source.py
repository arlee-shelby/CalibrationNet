from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .calibration import CalibrationPoint
    from .peak import Peak
    from .run_pixel import RunPixel


class Isotope(Base):
    """An isotope used for calibration (e.g. 207Bi, 113Sn). The peaks it
    produces are physics of the isotope, so they hang here; the physical
    sources of this isotope are Source rows."""

    __tablename__ = "isotopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)  # e.g. "207Bi"

    peaks: Mapped[List["IsotopePeak"]] = relationship(
        back_populates="isotope", cascade="all, delete-orphan"
    )
    sources: Mapped[List["Source"]] = relationship(back_populates="isotope")

    def __repr__(self) -> str:
        return f"Isotope(name={self.name})"


class Source(Base):
    """A specific physical calibration source: one manufactured item with
    its manufacturer id number. There can be many sources of the same
    isotope, and simulation-updated known energies are specific to one
    physical source, so runs record which actual source sat over which
    pixel."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_id: Mapped[int] = mapped_column(
        ForeignKey("isotopes.id"), index=True
    )
    label: Mapped[str] = mapped_column(String(50), unique=True)  # e.g. "Bi-207-9176"
    serial_number: Mapped[str] = mapped_column(String(50), unique=True)  # e.g. "Y2-743"
    notes: Mapped[Optional[str]]

    isotope: Mapped["Isotope"] = relationship(back_populates="sources")
    run_pixels: Mapped[List["RunPixel"]] = relationship(
        back_populates="source"
    )
    peak_energies: Mapped[List["PeakEnergy"]] = relationship(
        back_populates="source"
    )
    installations: Mapped[List["SourceInstallation"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Source({self.label}, serial={self.serial_number})"


class SourceInstallation(Base):
    """One source mounted in the source frame for an installation period
    (from the Source Installation History slides). removed_on is NULL for
    the current installation. slot uses the frame coordinates as seen in
    the "Facing UP" photos: rows 1 (top) - 2 (handle side), columns 1-3
    left to right, e.g. "R1C2"; the older 3-slot vertical holder uses
    "top"/"middle"/"bottom"."""

    __tablename__ = "source_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id"), index=True
    )
    installed_on: Mapped[date]
    removed_on: Mapped[Optional[date]]  # NULL = still installed
    slot: Mapped[str] = mapped_column(String(20))
    facing: Mapped[Optional[str]] = mapped_column(String(10))  # "up"|"down"
    notes: Mapped[Optional[str]]

    source: Mapped["Source"] = relationship(back_populates="installations")

    def __repr__(self) -> str:
        return (
            f"SourceInstallation(source_id={self.source_id}, "
            f"slot={self.slot}, installed_on={self.installed_on})"
        )


class IsotopePeak(Base):
    """One peak an isotope produces (e.g. 207Bi CE-K 976). The number of
    peaks varies by isotope. Its "known" energy in keV lives in PeakEnergy,
    which is versioned and may be specific to a physical source."""

    __tablename__ = "isotope_peaks"
    __table_args__ = (UniqueConstraint("isotope_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_id: Mapped[int] = mapped_column(
        ForeignKey("isotopes.id"), index=True
    )
    label: Mapped[str] = mapped_column(String(50))  # e.g. "CE-K 976"

    isotope: Mapped["Isotope"] = relationship(back_populates="peaks")
    energies: Mapped[List["PeakEnergy"]] = relationship(
        back_populates="isotope_peak", cascade="all, delete-orphan"
    )
    measured_peaks: Mapped[List["Peak"]] = relationship(
        back_populates="isotope_peak"
    )

    def __repr__(self) -> str:
        return f"IsotopePeak(isotope_id={self.isotope_id}, label={self.label})"


class PeakEnergy(Base):
    """A "known" energy (keV) for an isotope peak. NNDC/literature values
    apply to the isotope in general (source_id NULL); simulation-updated
    values are specific to one physical source (source_id set). Old rows
    are never overwritten — new values are new rows — and each calibration
    records (via CalibrationPoint) exactly which energy rows it used, so
    any past calibration stays reproducible."""

    __tablename__ = "peak_energies"

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_peak_id: Mapped[int] = mapped_column(
        ForeignKey("isotope_peaks.id"), index=True
    )
    # NULL = generic literature value; set = specific to that physical source.
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id"), index=True
    )

    energy_kev: Mapped[float]
    energy_error_kev: Mapped[Optional[float]]
    origin: Mapped[str] = mapped_column(String(20))  # "nndc" | "simulation"
    version: Mapped[Optional[str]] = mapped_column(String(50))  # e.g. "sim-2026a"
    notes: Mapped[Optional[str]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    isotope_peak: Mapped["IsotopePeak"] = relationship(
        back_populates="energies"
    )
    source: Mapped[Optional["Source"]] = relationship(
        back_populates="peak_energies"
    )
    calibration_points: Mapped[List["CalibrationPoint"]] = relationship(
        back_populates="peak_energy"
    )

    def __repr__(self) -> str:
        return (
            f"PeakEnergy(isotope_peak_id={self.isotope_peak_id}, "
            f"{self.energy_kev} keV, origin={self.origin})"
        )
