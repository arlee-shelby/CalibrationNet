"""The current pixel -> preamp/FET wiring maps, from data/pixel_wiring.csv.

Transcribed from the "Pixel Prioritized Preamp Map" and "Pixel Prioritized
FET Map" figures (pixel_preamp_map.png / pixel_fet_map.png in
data/provenance/ of the repo
root). The CSV covers pixels 1-127; the lower detector (1001-1127) uses
the identical mapping, so the loader mirrors it — pixel N+1000 gets the
same labels as pixel N (each detector has its own electronics; labels are
only unique within a detector). Pixel 58 is unmapped on both figures and
has empty entries.

This CSV is the source of truth for the quasi-static wiring stored on the
pixels table (scripts/seed_pixels.py materializes it). If a remap ever
happens, edit the CSV and re-run the seed script.
"""

import csv
from pathlib import Path
from typing import Optional

_DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "pixel_wiring.csv"


def load_wiring(csv_path: Optional[Path] = None) -> dict:
    """Return {pixel_number: {"preamp": str | None, "fet": str | None}}
    for all 254 pixels (1-127 upper, 1001-1127 lower)."""
    path = Path(csv_path) if csv_path else _DEFAULT_CSV
    wiring = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            entry = {
                "preamp": row["preamp"] or None,
                "fet": row["fet"] or None,
            }
            number = int(row["pixel_number"])
            wiring[number] = entry
            wiring[number + 1000] = dict(entry)
    return wiring
