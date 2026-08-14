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

SIMULATION values (AS design, 2026-08-14): a CSV origin of the form
"Jin-simulation-UDET-30kV" / "...-LDET-1kV" is stored structured, not
verbatim — origin "simulation", detector upper/lower, hv_kv the HV
magnitude in kV (readback convention: reported positive means negative
kV), and the family name from --version (e.g. "Jin-2026a"; required
when the CSV contains simulation rows: a re-run of the simulation is a
NEW family, never an overwrite). Any other origin (e.g. "nndc") seeds
exactly as before. Rows with empty intensity fields never erase a
line's stored intensities (simulation CSVs do not report them).

    python scripts/seed_decay_energies.py
    python scripts/seed_decay_energies.py \
        data/simulated_energies_Jin_simulations.csv --version Jin-2026a
"""

import argparse
import csv
import re

from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.models import Isotope, IsotopeDecayEnergy, KeVPeak

DEFAULT_CSV = "data/decay_energies.csv"
SIMULATION_ORIGIN = re.compile(
    r"^[A-Za-z0-9]+-simulation-(UDET|LDET)-(\d+)kV$")
DETECTOR_OF = {"UDET": "upper", "LDET": "lower"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", nargs="?", default=DEFAULT_CSV)
    parser.add_argument("--version", default=None,
                        help="simulation family name stored on every "
                             "simulation row (e.g. Jin-2026a); required "
                             "when the CSV contains simulation origins")
    args = parser.parse_args()
    path = args.csv_path
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
            detector = hv_kv = version = None
            sim = SIMULATION_ORIGIN.match(origin)
            if sim:
                if args.version is None:
                    raise SystemExit(
                        f"{path} contains simulation origins ({origin!r}) "
                        "— pass --version with the family name "
                        "(e.g. Jin-2026a).")
                detector = DETECTOR_OF[sim.group(1)]
                hv_kv = int(sim.group(2))
                version = args.version
                origin = "simulation"

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
            # Stable line properties: updated in place, not versioned —
            # but only when the CSV actually reports them (simulation
            # CSVs leave intensities empty; never erase the NNDC ones).
            if intensity is not None:
                line.intensity = intensity
                line.intensity_error = intensity_error

            existing = session.scalars(
                select(KeVPeak)
                .where(KeVPeak.isotope_decay_energy_id == line.id,
                       KeVPeak.source_id.is_(None),
                       KeVPeak.origin == origin)
            ).all()
            if any(p.energy_kev == energy and p.energy_error_kev == error
                   and p.detector == detector and p.hv_kv == hv_kv
                   and p.version == version
                   for p in existing):
                skipped += 1
                continue
            same_slot = [p for p in existing
                         if p.detector == detector and p.hv_kv == hv_kv]
            if same_slot:
                print(f"note: {isotope.name} {label}: adding NEW {origin} "
                      f"value {energy} keV alongside "
                      f"{[p.energy_kev for p in same_slot]} (values are "
                      "versioned, old rows are kept)")
            session.add(KeVPeak(isotope_decay_energy=line, energy_kev=energy,
                                energy_error_kev=error, origin=origin,
                                detector=detector, hv_kv=hv_kv,
                                version=version, notes=notes))
            values_created += 1
        session.commit()
        print(f"{lines_created} decay line(s) created, "
              f"{values_created} keV value(s) added, "
              f"{skipped} already present ({len(rows)} rows in {path})")


if __name__ == "__main__":
    main()
