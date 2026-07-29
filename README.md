# CalibrationNet

Postgres database (SQLAlchemy 2.0 ORM + Alembic migrations) for detector
energy calibrations: runs, pixels, trap filter passes over the raw
waveforms, fitted source peaks, and the calibrations derived from them.

## Schema

The analysis chain for one pixel during one period of constant source
position:

```
runs ──< run_segments ──< run_pixels >── pixels
          (one dwell at        │          (physical pixel; quasi-static
           one source          │           preamp/FET wiring)
           position)           │
                              ├──> sources   (the specific physical source
                              │        │      — label + serial number —
                              │        │      centered over the pixel)
                              │        │
                              │        └──> isotopes ──< isotope_peaks
                              │                              │
                              │                              └──< peak_energies
                              │            ("known" keV values, versioned:
                              │             NNDC values are per isotope;
                              │             simulation updates are per
                              │             physical source)
                              │
                              └──< trap_filter_outputs   (one filter pass:
                                          │               settings + energies
                                          │               array, many per pixel)
                                          └──< spectrum_fits   (fit of all peaks
                                                    │           in one output's
                                                    │           spectrum)
                                                    ├──< peaks (per-peak
                                                    │      centroid/sigma in
                                                    │      ADC, matched to an
                                                    │      isotope_peak)
                                                    └──< calibrations
                                                              │
                                                              └──< calibration_points
                                                          (peak + the exact
                                                           known-energy row used)
```

- **runs** — one acquisition with its detector/beamline settings:
  run_number (primary key — run numbers are never reused, so there's no
  separate surrogate id), number_subruns, udet_bias, ldet_bias, hv, main,
  udet, start_time, end_time, exb, udet_armor, ldet_armor, udet_ring,
  ldet_ring, udet_leakage, ldet_leakage. Source position is *not* here — a
  long run holds many positions, so it lives on run_segments.
- **pixels** — the physical detector pixel: pixel_number (primary key,
  same reasoning as run_number), detector ("upper"/"lower"), and its
  quasi-static preamp/FET wiring (labels like "G6"/"F2" encode the
  channel; identical mapping on both detectors, seeded from
  data/pixel_wiring.csv by scripts/seed_pixels.py). Convention: upper
  pixels are 1-127, lower pixels are the same number + 1000 (1001-1127);
  enforced by check constraints.
- **run_segments** — one period of constant source-frame position within a
  run, keyed (run_number, segment_index). A run taken at a single position
  has exactly one segment (index 0); the long "rastering" runs step the
  frame through many positions with a ~30 min dwell at each, so each dwell
  is its own segment — the direct analogue of one of the older short
  single-position runs. start_time/end_time cover the dwell only, so the
  stage motion between dwells falls outside every segment and waveforms
  selected by a segment's time range were all taken at one position.
  Carries linear_position, horizontal_position and the
  position_convention those numbers are in (see below).
- **run_pixels** — a pixel's participation in one run segment: the source
  centered over it (which is exactly what changes when the frame moves)
  and board_channel, which is assigned per run and so repeats across the
  run's segments. run_number/pixel_number hold the natural keys directly,
  so `WHERE run_number = 8622 AND pixel_number = 63` needs no joins.
- **isotopes** — a calibration isotope (e.g. 207Bi, 113Sn).
- **sources** — a specific physical source: its isotope, label
  (e.g. "Bi-207-9176"), and serial number (e.g. "Y2-743", from
  data/sources.xlsx via scripts/seed_sources.py). Many sources of the
  same isotope can exist, and runs record which one sat over which pixel.
- **source_installations** — the source frame's installation history
  (from the Source Installation History slides, seeded from
  data/source_installations.csv): which source sat in which frame slot
  from installed_on until removed_on (NULL = still installed). A run's
  active installation is selected by its start_time. Slot labels follow
  the convention below.
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

### Source position conventions

A raw position number only means something together with the convention it
was recorded in, so every segment stores its `position_convention`
(`calibrationnet/positions.py`):

- **`legacy-units`** (before 2026-07-24) — positions appeared only in the
  run-description free text. Linear in inches; horizontal ("2D") in
  machine units of about half an inch each, with the centered position
  reading ~2.7.
- **`inches-2026`** (from 2026-07-24) — positions come from the
  motion-control archive (`BL13:Nab:RSIS:leftRightMPOS:MPOS` and
  `:downUpstreamMPOS:MPOS` in the `Test` database, read by
  `calibrationnet/pipeline/motion_control.py`, which also derives the
  dwell periods that become segments). BOTH axes are inches, and the stage
  was re-homed so the centered position now reads 0 horizontally.

Source assignment never converts between conventions and never assumes a
convention's zero is the detector center: each convention carries its own
verified anchor, and predictions are displacements from it. A future
re-homing therefore only needs a new anchor, not new arithmetic.

### Source frame slot convention

Slots are labeled `R<row>C<col>`, always in the frame's **"Facing UP"
orientation** (the view from the upper detector, handle at the bottom):

```
      C1     C2     C3
R1  [    ] [    ] [    ]      row 1 = top row, farthest from the handle
R2  [    ] [    ] [    ]      rows increase toward the handle
         handle               columns increase left to right
```

The same rule covers any future holder, whatever its size or shape:
orient it handle-down as seen from the upper detector, then number rows
top-to-bottom and columns left-to-right (a single vertical stick is
`R1C1`, `R2C1`, `R3C1`, ...). Empty slots simply have no row in
data/source_installations.csv for that period.

Note the two faces: the "Facing DOWN" photos in the installation slides
show the same frame flipped, so they appear left-right mirrored — slot
labels always come from the Facing UP view. Likewise the lower detector
sees the frame mirrored; analysis code applies the same left-right mirror
it uses for lower-detector pixel coordinates (calibrationnet.geometry).

## Producing trap filter outputs

The filtering itself runs on the cluster that hosts the database (no
tunnel needed there, and the ingest is fast over the local network). The
unit of work is a **segment**, not a run, which is what keeps every job
short: a ~30 min dwell takes ~10 min to filter, so a 37-segment rastering
run becomes 37 short array tasks instead of one multi-hour job that could
hit the 7:59 wall limit.

```bash
# one array task per segment still missing this filter setting;
# SLURM's %50 throttle respects the 50-job account limit itself
./scripts/submit_trap_filter.sh run_list.txt /storage/.../TempCal/

# non-default settings: risetime flattop falltime wave label
./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/ 100 10 1250 singles scan
```

- [scripts/submit_trap_filter.sh](scripts/submit_trap_filter.sh) asks the
  database which segments still need the setting (via
  [scripts/pending_segments.py](scripts/pending_segments.py)), writes a
  manifest, and submits one array over it — splitting into several arrays
  if the list exceeds `MAX_ARRAY`. **Re-running it is how you finish an
  interrupted batch**: anything already ingested is left out of the new
  manifest, so nothing is redone. No submit-and-poll loop and nothing to
  keep alive in tmux.
- [scripts/apply_trap_filter.sh](scripts/apply_trap_filter.sh) is the array
  task: it reads its manifest line and runs the segment. `--qos=embers` is
  preemptible, so it sets `--requeue`; a preempted task simply redoes its
  own segment.
- [scripts/apply_trap_filter.py](scripts/apply_trap_filter.py) does one
  segment: looks up that segment's dwell window, binary-searches which
  subruns overlap it, filters only those waveforms, ingests the energies,
  and **deletes the intermediate CSV** — the .h5 files are the archive, so
  keeping filter output would only waste storage.
- [calibrationnet/pipeline/waveforms.py](calibrationnet/pipeline/waveforms.py)
  holds the nabPy work. It is the only module that needs nabPy, so nothing
  else imports it; set `NABPY_PATH` if nabPy is not already importable, and
  `CALNET_VENV` to point the batch script at the right environment.

Waveforms are selected by wall-clock time, using nabPy's `unix timestamp`
header column (4 ns ticks since the epoch — divide by 2.5e8 for seconds).
Since a segment's window covers only its dwell, waveforms taken while the
stage was moving belong to no segment and are simply never filtered.

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
    Isotope, Pixel, Run, RunPixel, RunSegment, Source, TrapFilterOutput,
)
from calibrationnet.positions import INCHES_2026

with get_session() as session:
    run = Run(run_number=9999, start_time=datetime(2026, 7, 25, 19, 34))
    # One segment per period of constant source position.
    segment = RunSegment(run=run, segment_index=0,
                         linear_position=33.047, horizontal_position=-0.294,
                         position_convention=INCHES_2026)
    pixel = Pixel(pixel_number=63, detector="upper", preamp="E2", fet="D8")
    source = Source(isotope=Isotope(name="Bi-207"), label="Bi-207-9176",
                    serial_number="Y2-743")
    rp = RunPixel(segment=segment, pixel=pixel, source=source,
                  board_channel=12)
    tfo = TrapFilterOutput(run_pixel=rp, trap_rise=1250, trap_flattop=50,
                           trap_falltime=1250, energies=[512.3, 977.1])
    session.add(tfo)
    session.commit()
```

Querying is by run number, segment and pixel number directly:

```python
from sqlalchemy import select

outputs = session.scalars(
    select(TrapFilterOutput)
    .join(TrapFilterOutput.run_pixel)
    .where(RunPixel.run_number == 8622, RunPixel.pixel_number == 63)
).all()
```
