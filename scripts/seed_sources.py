"""Seed isotopes and physical sources from the current sources spreadsheet
("Current CAL2702 Sources.xlsx": Source ID like "Bi-207-9176", Serial
number like "Y2-743"). Idempotent: existing sources are matched by label
and their serial updated if it changed.
"""

import sys

import pandas as pd

from calibrationnet.db import get_session
from calibrationnet.models import Isotope, Source

DEFAULT_XLSX = "Current CAL2702 Sources.xlsx"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX
    df = pd.read_excel(path)

    with get_session() as session:
        isotopes = {i.name: i for i in session.query(Isotope)}
        sources = {s.label: s for s in session.query(Source)}
        created = 0
        for row in df.itertuples(index=False):
            label = str(row[0]).strip()          # e.g. "Bi-207-9176"
            serial = str(row[1]).strip()         # e.g. "Y2-743"
            isotope_name = label.rsplit("-", 1)[0]  # e.g. "Bi-207"

            isotope = isotopes.get(isotope_name)
            if isotope is None:
                isotope = Isotope(name=isotope_name)
                session.add(isotope)
                isotopes[isotope_name] = isotope

            source = sources.get(label)
            if source is None:
                session.add(Source(isotope=isotope, label=label,
                                   serial_number=serial))
                created += 1
            else:
                source.serial_number = serial
        session.commit()
        print(f"{len(isotopes)} isotopes, {created} sources created "
              f"({len(df)} in spreadsheet)")


if __name__ == "__main__":
    main()
