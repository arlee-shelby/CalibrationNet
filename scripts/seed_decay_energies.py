"""Seed isotope decay lines and their known keV values from
data/decay_energies.csv (columns: isotope, label, energy_kev,
energy_error_kev, intensity, intensity_error, origin, notes).

The label is the line's IDENTITY — emission type + rounded energy, e.g.
"CE 976" or "Auger 56" — and never changes; the exact keV values live in
kev_peaks, which is versioned: a changed value in the CSV is stored as a
NEW kev_peaks row (source_id NULL = generic literature value), the old
row is kept, and calibration_points keep pointing at whichever row they
used. Intensities (NNDC emission %, stable properties of the line) live
on the line itself and ARE updated in place. Empty error/intensity
fields mean "not reported" and are stored NULL, not 0.

Idempotent: decay lines are matched by (isotope, label); a kev_peaks row
is only added if no identical one exists.

    python scripts/seed_decay_energies.py
    python scripts/seed_decay_energies.py path/to/other.csv
"""

import csv
import sys

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import Isotope, IsotopeDecayEnergy, KeVPeak

DEFAULT_CSV = "data/decay_energies.csv"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))

    with get_session() as session:
        isotopes = {i.name: i for i in session.scalars(select(Isotope))}
        lines_created = values_created = skipped = 0
        for row in rows:
            isotope = isotopes.get(row["isotope"].strip())
            if isotope is None:
                raise SystemExit(
                    f"isotope {row['isotope']!r} is not in the database — "
                    "seed it first (scripts/seed_sources.py)."
                )
            label = row["label"].strip()
            energy = float(row["energy_kev"])
            error = (float(row["energy_error_kev"])
                     if row["energy_error_kev"].strip() else None)
            intensity = (float(row["intensity"])
                         if row["intensity"].strip() else None)
            intensity_error = (float(row["intensity_error"])
                               if row["intensity_error"].strip() else None)
            origin = row["origin"].strip()
            notes = row["notes"].strip() or None

            line = session.scalars(
                select(IsotopeDecayEnergy)
                .where(IsotopeDecayEnergy.isotope_id == isotope.id,
                       IsotopeDecayEnergy.label == label)
            ).first()
            if line is None:
                line = IsotopeDecayEnergy(isotope=isotope, label=label)
                session.add(line)
                session.flush()
                lines_created += 1
            # Stable line properties: updated in place, not versioned.
            line.intensity = intensity
            line.intensity_error = intensity_error

            existing = session.scalars(
                select(KeVPeak)
                .where(KeVPeak.isotope_decay_energy_id == line.id,
                       KeVPeak.source_id.is_(None),
                       KeVPeak.origin == origin)
            ).all()
            if any(p.energy_kev == energy and p.energy_error_kev == error
                   for p in existing):
                skipped += 1
                continue
            if existing:
                print(f"note: {isotope.name} {label}: adding NEW {origin} "
                      f"value {energy} keV alongside "
                      f"{[p.energy_kev for p in existing]} (values are "
                      "versioned, old rows are kept)")
            session.add(KeVPeak(isotope_decay_energy=line, energy_kev=energy,
                                energy_error_kev=error, origin=origin,
                                notes=notes))
            values_created += 1
        session.commit()
        print(f"{lines_created} decay line(s) created, "
              f"{values_created} keV value(s) added, "
              f"{skipped} already present ({len(rows)} rows in {path})")


if __name__ == "__main__":
    main()
