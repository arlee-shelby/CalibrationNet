"""Seed the pixels table: all 254 pixels (1-127 upper, 1001-1127 lower)
with their quasi-static preamp/FET wiring from data/pixel_wiring.csv.

Idempotent: re-running updates wiring in place (e.g. after a remap is
edited into the CSV).
"""

from calibrationnet.db import get_session
from calibrationnet.models import Pixel
from calibrationnet.pipeline.wiring import load_wiring


def main() -> None:
    wiring = load_wiring()
    created = updated = 0
    with get_session() as session:
        existing = {p.pixel_number: p for p in session.query(Pixel)}
        for pixel_number, wires in sorted(wiring.items()):
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
          f"{len(wiring) - created - updated} unchanged")


if __name__ == "__main__":
    main()
