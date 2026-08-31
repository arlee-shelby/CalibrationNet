"""The current pixel -> preamp/FET maps, from data/electronics_mapping.csv.

The CSV file was derived from the "Pixel Prioritized Preamp Map" and "Pixel Prioritized
FET Map" figures (pixel_preamp_map.png/pixel_fet_map.png in the "data/provenance/" folder), which
were pulled from the Nab wiki. The CSV covers pixels 1-127; the lower detector (1001-1127) uses
the identical mapping, so the loader does the conversion for the lower detector pixels (i.e. pixel
N+1000 gets the same labels as pixel N). Note, each detector has its own electronics but labels are
only unique within a detector. Pixel 58 is unmapped on both figures and has empty entries.

The CSV is the source of the quasi-static mapping stored in the
pixels table (scripts/seed_pixels.py materializes it). Since the preamp/FET maps are unlikely to change,
the current configuration (as depicted in the maps on the wiki) is used. If a remap ever happens,
edit the CSV and re-run the seed script.
"""

import csv
from pathlib import Path
from typing import Optional

# define absolute path of CSV mapping file, parents[2] is the repo root
_DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "electronics_mapping.csv"


def load_mapping(csv_path: Optional[Path] = None) -> dict:
    """Return {pixel_number: {"preamp": str | None, "fet": str | None}}
    for all 254 pixels (1-127 upper, 1001-1127 lower).
    """
    path = Path(csv_path) if csv_path else _DEFAULT_CSV
    mapping = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            entry = {
                "preamp": row["preamp"] or None,
                "fet": row["fet"] or None,
            }
            number = int(row["pixel_number"])
            mapping[number] = entry
            mapping[number + 1000] = dict(entry)
    return mapping
