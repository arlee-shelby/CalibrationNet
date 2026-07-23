"""Seed source_installations from data/source_installations.csv (the
Source Installation History slides, with corrections). Slot coordinates
are frame positions as seen in the "Facing UP" photos: R<row>C<col>,
row 1 = top, columns left to right.

Idempotent: wipes and re-inserts all rows from the CSV (the table is a
pure materialization of the file).
"""

import csv
from datetime import date
from pathlib import Path

from calibrationnet.db import get_session
from calibrationnet.models import Source, SourceInstallation

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "source_installations.csv"


def main() -> None:
    with get_session() as session:
        sources = {s.label: s for s in session.query(Source)}
        session.query(SourceInstallation).delete()
        n = 0
        with open(CSV_PATH, newline="") as f:
            for row in csv.DictReader(f):
                source = sources.get(row["source_label"])
                if source is None:
                    raise ValueError(
                        f"Unknown source {row['source_label']!r} — seed "
                        "sources first (scripts/seed_sources.py)."
                    )
                session.add(SourceInstallation(
                    source=source,
                    installed_on=date.fromisoformat(row["installed_on"]),
                    removed_on=(date.fromisoformat(row["removed_on"])
                                if row["removed_on"] else None),
                    slot=row["slot"],
                    facing=row["facing"] or None,
                    notes=row["notes"] or None,
                ))
                n += 1
        session.commit()
        print(f"{n} installation rows seeded from {CSV_PATH.name}")


if __name__ == "__main__":
    main()
