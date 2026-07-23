# CalibrationNet

Postgres database (SQLAlchemy 2.0 ORM + Alembic migrations) for detector
energy calibrations: runs, pixels, trap filter passes over the raw
waveforms, fitted source peaks, and the calibrations derived from them.

## Schema

The analysis chain for one pixel in one run:

```
runs ──< run_pixels >── pixels     (many-to-many; threshold + board channel
              │    \                live on run_pixels; quasi-static
              │    \                preamp/FET wiring lives on pixels)
              │     └──> sources   (the specific physical source — with its
              │              │      manufacturer id — centered over the pixel)
              │              │
              │              └──> isotopes ──< isotope_peaks ──< peak_energies
              │                                ("known" keV values, versioned:
              │                                 NNDC values are per isotope;
              │                                 simulation updates are per
              │                                 physical source)
              │
              └──< trap_filter_outputs   (one filter pass: settings +
                          │               energies array, many per pixel)
                          └──< spectrum_fits   (fit of all peaks in one
                                    │           filter output's spectrum)
                                    ├──< peaks (per-peak centroid/sigma
                                    │           in ADC, matched to an
                                    │           isotope_peak)
                                    └──< calibrations ──< calibration_points
                                                          (peak + the exact
                                                           known-energy row
                                                           used)
```

- **runs** — one acquisition with its detector/beamline settings: run_number,
  udet_bias, ldet_bias, hv, main, udet, start_time, end_time,
  linear_position, horizontal_position, exb, udet_armor, ldet_armor,
  udet_ring, ldet_ring, udet_leakage, ldet_leakage.
- **pixels** — the physical detector pixel: pixel_number, detector
  ("upper"/"lower"), and its quasi-static preamp/FET wiring (labels like
  "G6"/"F2" encode the channel; identical mapping on both detectors,
  seeded from data/pixel_wiring.csv by scripts/seed_pixels.py). Convention:
  upper pixels are 1-127, lower pixels are the same number + 1000
  (1001-1127); enforced by check constraints.
- **run_pixels** — a pixel's participation in a run: the source centered
  over it, threshold, and board_channel (unique within a run), which is
  reassigned run to run and read from the run's data file.
- **isotopes** — a calibration isotope (e.g. 207Bi, 113Sn).
- **sources** — a specific physical source: its isotope plus the
  manufacturer id number. Many sources of the same isotope can exist,
  and runs record which one sat over which pixel.
- **isotope_peaks** — the peaks an isotope produces; the count varies by
  isotope.
- **peak_energies** — versioned "known" keV values for an isotope peak:
  origin ("nndc" or "simulation"), version label, error, created_at.
  source_id is NULL for generic literature values and set for
  simulation-updated values, which are specific to one physical source.
  Updated values are new rows, never overwrites.
- **trap_filter_outputs** — one application of the trapezoidal filter to a
  run_pixel's raw waveforms: trap_rise, trap_flattop, trap_falltime, and
  the resulting per-waveform energies (ADC) as an array. Applied many
  times with different settings; histograms are built from `energies` on
  demand.
- **spectrum_fits** — a fit of all peaks in one filter output's spectrum:
  chi2, ndf, full parameter/error sets as JSONB (parameter count varies
  with the source's peak count).
- **peaks** — per-peak results broken out of a spectrum fit, in ADC:
  centroid ± error, sigma ± error, amplitude ± error, matched isotope_peak.
- **calibrations** — ADC→keV calibration (linear/quadratic) from one
  spectrum fit: coefficients ± errors, chi2, is_current, created_at.
  Multiple attempts are kept; a partial unique index guarantees at most one
  `is_current` per (run_pixel, type).
- **calibration_points** — which (measured peak, known-energy row) pairs
  fed a calibration, so it's always known whether NNDC or simulation
  values were used.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env   # then edit with your Postgres credentials

# create the initial migration from the models, then apply it
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

## Usage

```python
from datetime import datetime

from calibrationnet.db import get_session
from calibrationnet.models import (
    Isotope, Pixel, Run, RunPixel, Source, TrapFilterOutput,
)

with get_session() as session:
    run = Run(run_number=4501, start_time=datetime(2026, 7, 16, 9, 30))
    pixel = Pixel(pixel_number=63, detector="upper", preamp="E2", fet="D8")
    source = Source(isotope=Isotope(name="207Bi"), manufacturer_id="BB-1182")
    rp = RunPixel(run=run, pixel=pixel, source=source, threshold=50,
                  board_channel=12)
    tfo = TrapFilterOutput(run_pixel=rp, trap_rise=1250, trap_flattop=50,
                           trap_falltime=1250, energies=[512.3, 977.1])
    session.add(tfo)
    session.commit()
```
