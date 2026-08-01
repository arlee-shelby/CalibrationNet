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
    """An isotope used for calibration (e.g. 207Bi, 113Sn). The peaks it
    produces are physics of the isotope, so they hang here; the physical
    sources of this isotope are Source rows."""

    __tablename__ = "isotopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)  # e.g. "207Bi"

    decay_energies: Mapped[List["IsotopeDecayEnergy"]] = relationship(
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
    kev_peaks: Mapped[List["KeVPeak"]] = relationship(
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
    the current installation.

    Slot convention (see README "Source frame slot convention"): labels
    are "R<row>C<col>" in the frame's Facing UP orientation — the view
    from the upper detector with the handle at the bottom — rows numbered
    from 1 at the top (farthest from the handle), columns from 1 at the
    left. The rule generalizes to any holder shape (a single vertical
    stick is R1C1, R2C1, ...). The lower detector sees this mirrored
    left-right, like everything else."""

    __tablename__ = "source_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id"), index=True
    )
    installed_on: Mapped[date]
    removed_on: Mapped[Optional[date]]  # NULL = still installed
    slot: Mapped[str] = mapped_column(String(20))
    # Which physical tray was mounted: "3-slot", "5-slot", "6-slot". The
    # spacing between slots is a property of the tray (the 6-slot one is
    # ~0.15 inch longer), so source assignment keys its frame geometry on
    # this, not on the date — a tray can be removed and re-installed later
    # and should reuse the geometry already measured for it.
    holder: Mapped[Optional[str]] = mapped_column(String(20))
    notes: Mapped[Optional[str]]

    source: Mapped["Source"] = relationship(back_populates="installations")

    def __repr__(self) -> str:
        return (
            f"SourceInstallation(source_id={self.source_id}, "
            f"slot={self.slot}, installed_on={self.installed_on})"
        )


class IsotopeDecayEnergy(Base):
    """One energy line an isotope's decay produces (e.g. 207Bi CE-K 976).
    How many lines varies by isotope. This is the line's IDENTITY only —
    the keV values we believe for it live in KeVPeak, which is versioned
    and may be specific to a physical source."""

    __tablename__ = "isotope_decay_energies"
    __table_args__ = (UniqueConstraint("isotope_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_id: Mapped[int] = mapped_column(
        ForeignKey("isotopes.id"), index=True
    )
    label: Mapped[str] = mapped_column(String(50))  # e.g. "CE 976"

    # NNDC emission intensity in percent — a stable property of the line
    # (unlike keV values, which are versioned in kev_peaks), used to
    # pick matching anchors and predict which lines low-statistics
    # pixels can see. NULL when not reported (e.g. the Bi-207 Auger
    # lines: NNDC gives only a combined 2.9% for the whole Auger group).
    intensity: Mapped[Optional[float]]
    intensity_error: Mapped[Optional[float]]

    isotope: Mapped["Isotope"] = relationship(
        back_populates="decay_energies"
    )
    kev_peaks: Mapped[List["KeVPeak"]] = relationship(
        back_populates="isotope_decay_energy", cascade="all, delete-orphan"
    )
    adc_peaks: Mapped[List["ADCPeak"]] = relationship(
        back_populates="isotope_decay_energy"
    )

    def __repr__(self) -> str:
        return (f"IsotopeDecayEnergy(isotope_id={self.isotope_id}, "
                f"label={self.label})")


class KeVPeak(Base):
    """A "known" energy value, in keV, for one isotope decay line — the
    keV side of a calibration point. NNDC/literature values apply to the
    isotope in general (source_id NULL); corrected values from simulation
    are specific to one physical source (source_id set). Old rows are
    never overwritten — new values are new rows — and each calibration
    records (via CalibrationPoint) exactly which keV rows it used, so any
    past calibration stays reproducible."""

    __tablename__ = "kev_peaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    isotope_decay_energy_id: Mapped[int] = mapped_column(
        ForeignKey("isotope_decay_energies.id"), index=True
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

    isotope_decay_energy: Mapped["IsotopeDecayEnergy"] = relationship(
        back_populates="kev_peaks"
    )
    source: Mapped[Optional["Source"]] = relationship(
        back_populates="kev_peaks"
    )
    calibration_points: Mapped[List["CalibrationPoint"]] = relationship(
        back_populates="kev_peak"
    )

    def __repr__(self) -> str:
        return (
            f"KeVPeak(isotope_decay_energy_id={self.isotope_decay_energy_id},"
            f" {self.energy_kev} keV, origin={self.origin})"
        )
