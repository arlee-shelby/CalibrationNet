"""Seed the pixels table: all 254 pixels (1-127 upper, 1001-1127 lower)
with their quasi-static preamp/FET electronics mapping from data/electronics_mapping.csv
(hand-transcribed from the collaboration's pixel preamp/FET map figures,
kept in data/provenance/).

Idempotent: re-running updates the mapping in place (e.g. after a remap is
edited into the CSV). In practice this is a run-once seed — the mapping
is quasi-static.
"""

from calibrationnet.db import get_session
from calibrationnet.models import Pixel
from calibrationnet.acquisition.electronics_mapping import load_mapping


def main() -> None:
    mapping = load_mapping()
    created = updated = 0
    with get_session() as session:
        existing = {p.pixel_number: p for p in session.query(Pixel)}
        for pixel_number, wires in sorted(mapping.items()):
            detector = "upper" if pixel_number <= 127 else "lower"
            pixel = existing.get(pixel_number)
            if pixel is None:
                session.add(Pixel(pixel_number=pixel_number, detector=detector,
                                  preamp=wires["preamp"], fet=wires["fet"]))
                created += 1
            elif (pixel.preamp, pixel.fet) != (wires["preamp"], wires["fet"]):
                pixel.preamp, pixel.fet = wires["preamp"], wires["fet"]
                updated += 1
        session.commit()
    print(f"pixels: {created} created, {updated} updated, "
          f"{len(mapping) - created - updated} unchanged")


if __name__ == "__main__":
    main()
