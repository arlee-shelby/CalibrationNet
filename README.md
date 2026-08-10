# CalibrationNet

Postgres database (SQLAlchemy 2.0 ORM + Alembic migrations) for detector
energy calibrations: runs, pixels, trap filter passes over the raw
waveforms, fitted source peaks, and the calibrations derived from them.

Repository layout — which directory holds what, and which script
produces which files/plots — is mapped in
[docs/repo_layout.md](docs/repo_layout.md); example output figures with
captions are in [docs/example_outputs.md](docs/example_outputs.md).
Development-era material (uploaded reference files, exploration
notebooks, superseded outputs) lives under
[development/](development/), never at the repo root.

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
                              │        └──> isotopes ──< isotope_decay_energies
                              │                              │
                              │                              └──< kev_peaks
                              │            ("known" keV values, versioned:
                              │             NNDC values are per isotope;
                              │             simulation updates are per
                              │             physical source)
                              │
                              ├──< trap_filter_outputs   (one filter pass:
                              │           │               settings + energies
                              │           │               array, many per pixel)
                              │           └──< spectrum_fits   (one fit of PART of
                              │                     │           an output's spectrum,
                              │                     │           e.g. CE window or
                              │                     │           Auger window)
                              │                     └──< adc_peaks (per-peak
                              │                            centroid/sigma in
                              │                            ADC, matched to an
                              │                            isotope_decay_energy)
                              │
                              └──< calibrations   (ADC→keV fit for the run
                                        │          pixel; its points draw on
                                        │          SEVERAL spectrum fits)
                                        └──< calibration_points
                                              (adc_peak + the exact
                                               kev_peak row used)
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
  "data/Current CAL2702 Sources.xlsx" via scripts/seed_sources.py).
  Many sources of the
  same isotope can exist, and runs record which one sat over which pixel.
- **source_installations** — the source frame's installation history
  (from the Source Installation History slides — the PDF and the
  corrected 7/21 figure live in data/provenance/ — seeded from
  data/source_installations.csv): which source sat in which frame slot
  from installed_on until removed_on (NULL = still installed). A run's
  active installation is selected by its start_time. Slot labels follow
  the convention below.
- **isotope_decay_energies** — the energy lines an isotope's decay
  produces; the count varies by isotope. This is the line's *identity*
  — emission type + rounded energy, e.g. "CE 976", "Auger 56" — plus its
  NNDC emission intensity (a stable property of the line, unlike the
  keV values, which live versioned in kev_peaks). Both tables are
  seeded from data/decay_energies.csv by scripts/seed_decay_energies.py
  (idempotent; a changed keV value becomes a new kev_peaks row, never an
  overwrite; intensities update in place).
- **kev_peaks** — versioned "known" keV values for a decay line — the keV
  side of a calibration point: origin ("nndc" or "simulation"), version
  label, error, created_at. source_id is NULL for generic literature
  values and set for simulation-updated values, which are specific to one
  physical source. Updated values are new rows, never overwrites.
- **trap_filter_outputs** — one application of the trapezoidal filter to a
  run_pixel's raw waveforms: trap_rise, trap_flattop, trap_falltime, and
  the resulting per-waveform energies (ADC) as an array. Applied many
  times with different settings; histograms are built from `energies` on
  demand.
- **spectrum_fits** — one fit of *part* of a filter output's spectrum; an
  output usually takes several (the six CE peaks over one ADC window, the
  Auger peaks over another), distinguished by `label` and
  fit_range_low/high. Stores chi2/ndf/reduced_chi2/success, all parameter
  values and errors as JSONB, the varied-parameter names (`var_names`, in
  covariance row order) with the covariance matrix, and the fit inputs
  (`config`) so any fit can be reproduced. Correlations are not stored —
  they're derived exactly from the covariance by `.correlations()`.
- **adc_peaks** — per-peak results broken out of a spectrum fit, in ADC —
  the ADC side of a calibration point: centroid ± error, sigma ± error,
  amplitude ± error, matched isotope_decay_energy.
- **calibrations** — ADC→keV calibration (linear/quadratic) for one
  run_pixel. Deliberately not tied to one spectrum fit — its points come
  from several fits (CE + Auger windows), recorded per point via
  calibration_points. Coefficients ± errors as dedicated columns, plus
  the same fit-quality/uncertainty pattern as spectrum_fits: `label`,
  chi2/ndf/reduced_chi2/success, `var_names` + `covariance`, `config`,
  and derived `.correlations()`. Multiple attempts are kept; a partial
  unique index guarantees at most one `is_current` per (run_pixel, type).
- **calibration_points** — which (adc_peak, kev_peak) pairs fed a
  calibration, so it's always known whether NNDC or simulation values
  were used.

How fit results are stored — what `pars`, `var_names`, `covariance`,
`config`, and `success` each hold, why correlations are derived rather
than stored, and worked examples for both tables — is documented in
[docs/fit_storage.md](docs/fit_storage.md).

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
# one array over every segment still missing this filter setting
./scripts/submit_trap_filter.sh run_list.txt /storage/.../TempCal/

# non-default settings: risetime flattop falltime wave label
./scripts/submit_trap_filter.sh run_list.txt /path/to/h5/ 100 10 1250 singles scan
```

- [scripts/submit_trap_filter.sh](scripts/submit_trap_filter.sh) asks the
  database which segments still need the setting (via
  [scripts/pending_segments.py](scripts/pending_segments.py)), writes a
  manifest, and submits one array over it. The QOS caps *submitted* jobs
  per user (~50 on embers) and every array task counts against that cap
  at submission time, so batches bigger than `MAX_SUBMIT` (default 40)
  tasks are packed as several consecutive segments per task, with the
  walltime scaled to match. **Re-running it is how you finish an
  interrupted batch**: anything already ingested is left out of the new
  manifest, so nothing is redone. No submit-and-poll loop and nothing to
  keep alive in tmux.
- [scripts/apply_trap_filter.sh](scripts/apply_trap_filter.sh) is the array
  task: it works through its chunk of manifest lines, one segment at a
  time. `--qos=embers` is preemptible, so it sets `--requeue`; a
  preempted task's segments are redone on the next submission, and one
  failing segment doesn't stop the rest of its chunk.
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

## Fitting spectra

```bash
python scripts/fit_spectra.py --run 8622 --pixels 60
python scripts/fit_spectra.py --run 9327                 # every fitted pixel
python scripts/fit_spectra.py --run 8622 --pixels 60 --plot fit_plots/

# or the whole pipeline for a run in one command (each stage idempotent;
# needs the slow-controls tunnel for the ingest stage):
./scripts/with_sc_tunnel.sh python scripts/process_run.py 9402 \
    --h5-dir /path/to/h5/ --min-dwell 3
```

[scripts/process_run.py](scripts/process_run.py) chains every stage —
ingest, trap filter (submits the SLURM array when on the cluster and
outputs are missing, then asks to be re-run), source assignment
(auto-applies non-CHECK rows), fits, peak extraction, calibrations —
per segment, skipping whatever is already done.

[scripts/fit_spectra.py](scripts/fit_spectra.py) pulls a run pixel's trap
filter output, fits its peaks with the developed physics code
([calibrationnet/fit_functions.py](calibrationnet/fit_functions.py)),
and stores every ACCEPTED fit in spectrum_fits via
`SpectrumFit.from_lmfit`. Which fits run comes from the pixel's assigned
source: each isotope has recipes
([calibrationnet/fit_recipes.py](calibrationnet/fit_recipes.py):
ADC window, peak count, peak-finder settings, starting widths), so Bi-207
produces the 6-peak CE fit and the 2-peak Auger fit per output.
Every attempt must pass the quality check (`fit_is_good`: converged,
all uncertainties present, centroid errors within 5% — 25% in the Auger
window — width errors within 50%, reduced chi2 <= 10, and the
peak-spacing check — fitted peaks must sit where the known line
energies place them relative to the anchor peaks, so a fit that
grabbed a threshold shoulder or background hump is rejected even when
its errors look fine); a failing fit is retried with the recipe's
retry starting widths (measured per peak from the data, then explicit
sets) and progressively gentler peak-finder settings, then the
predicted-start rescue. If every attempt fails,
NOTHING is stored — a junk fit never enters the database — and pixels
that had the statistics but still failed are listed in
`fit_plots/fit_failures_summary.csv` for review. Re-running replaces
the fit with the same (output, label). `--plot` saves a figure per fit
(failures included, with the closest-miss attempt drawn). Storage
details: [docs/fit_storage.md](docs/fit_storage.md).

After fitting,
[scripts/extract_adc_peaks.py](scripts/extract_adc_peaks.py) breaks each
stored fit into adc_peaks rows matched to the isotope's decay lines:
centroids pair with line energies by ascending order, then a per-pixel
two-anchor energy line (lowest-energy + highest-intensity CE line) must
confirm every match within `--tolerance-kev` — implausible peaks are
stored with no line and flagged, so bad fits can't feed a calibration.

Finally, [scripts/calibrate.py](scripts/calibrate.py) turns each run
pixel's matched peaks into stored ADC→keV calibrations: one linear and
one quadratic Calibration per trap filter output (never mixing outputs —
the ADC scale is a property of the trap setting), pairing each adc_peak
with its keV row (source-bound simulation values when seeded, else
newest generic NNDC — the exact row is recorded per point). At least 3
matched points are required (4 for quadratic); the newest calibration
becomes `is_current` per (run_pixel, type) unless `--no-current`.
Re-running replaces the same-labelled calibration; peaks referenced by a
calibration are frozen against re-extraction until it is rebuilt.

### Fitting code policy

The fit MODEL is never modified. The seven physics functions in
[calibrationnet/fit_functions.py](calibrationnet/fit_functions.py) —
`gaussian`, `background`, `lower_exponential`, `step_function`,
`fit_model`, `residual_function`, `get_histogram_data_uncertainty` —
are frozen: they encode the developed physics and are not to be edited
at all ([docs/pipeline_roadmap.md](docs/pipeline_roadmap.md) has the
full policy). Everything tunable is an *input* to `get_fit`, defined in
[calibrationnet/fit_recipes.py](calibrationnet/fit_recipes.py): the ADC
windows, peak counts, peak-finder settings, starting widths and their
retries, the quality-check thresholds, and the reduced-chi2 cap.
Optimizing fits means optimizing those inputs and retrying — never
changing the model.

Any change to the fitting code must pass
[scripts/benchmark_fits.py](scripts/benchmark_fits.py), which verifies a
byte-identical reference copy
([calibrationnet/fit_functions_reference.py](calibrationnet/fit_functions_reference.py))
plus frozen-function integrity, and compares live-vs-reference fit
results (centroid pulls, chi2, success) over real data — followed by
AS's plot review of the regenerated `fit_plots/` figures on the
reference pixels before adoption.

## Planning source positions

```bash
python scripts/optimal_positions.py            # plan for the current tray
python scripts/optimal_positions.py --holder 5-slot
```

[scripts/optimal_positions.py](scripts/optimal_positions.py) turns the
scan data into a **position plan**: the fewest stage positions that put
a well-centered source over every reachable pixel, for the run
automation to step through. It fits the readback→frame trend from all
ingested scanned segments (so it improves with every ingest), refines
the tray's slot offsets against the measured count centroids of every
scanned segment (the anchor alone carries up to a pixel of quantization
error), searches only the readback range the data proves reachable, and
grades every (position, pixel) pairing by the predicted offset of the
slot center from the pixel center — ≤2.6 mm counts as well-centered,
≤4.5 mm (the neighbor boundary) as covered. Plans are keyed by holder **slot**, never
by the source in it. Outputs: the plan CSV, a positions-only CSV for
automation, a saved summary (uncovered pixels always listed with their
best achievable offset), and per-detector coverage maps.
`--assume-horizontal LO HI` explores what a wider scan range would buy
(clearly-marked `_whatif` outputs, for scan planning only). Full
description with the band-geometry explanation of unreachable pixels:
[docs/position_planning.md](docs/position_planning.md).

## Setup

Two things vary by machine: **which python environment** you need, and
**which `.env` variables** you fill in. They depend only on what you plan
to run.

| What you want to do | Python packages needed | `.env` variables |
|---|---|---|
| Query the database, draw hit maps, assign sources | this package (`pip install -e .`) | `DATABASE_URL` |
| Ingest runs and their segments | same | `+ SC_DATABASE_URL` (and optionally `POSITIONS_DATABASE_URL`) |
| Apply the trap filter to raw waveforms | this package **and nabPy** | `DATABASE_URL` (`+ NABPY_PATH` if nabPy is not importable) |

Only the last row is awkward, because nabPy brings h5py/numba/dask with it.
Everything else is a plain `pip install -e .`.

### A database-only environment

Enough for ingesting runs, hit maps, and source assignment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # then fill in DATABASE_URL (and SC_DATABASE_URL)
```

### An environment that can also apply the trap filter

This needs nabPy in the *same* environment as this package, since
`scripts/apply_trap_filter.py` both filters waveforms and writes to the
database. Two ways to get there:

**If you already have a working nabPy environment** (likely on a cluster —
it is whatever you activate before running nabPy), just add this package to
it. This pulls in only sqlalchemy/psycopg/alembic/python-dotenv, none of
which depend on numpy or numba, so the nabPy stack is undisturbed:

```bash
source <your nabPy env>/bin/activate
pip install -e <path to this repo>
```

**If you have no nabPy environment yet**, build a combined one from source
checkouts of pyNab and deltarice:

```bash
./scripts/setup_env.sh <new env dir> <pyNab checkout> <deltarice checkout>
```

It pins numpy/llvmlite/numba to a tested-good set (newer llvmlite often has
no wheel and fails to build), installs nabPy and deltaRice, then installs
this package, and finally verifies that everything imports.

Either way, check the result:

```bash
python -c "from calibrationnet.pipeline.waveforms import import_nabpy; \
import_nabpy(); from calibrationnet.db import get_engine; \
get_engine().connect(); print('nabPy + database OK')"
```

If `import nabPy` fails but nabPy exists as a source checkout, set
`NABPY_PATH` in `.env` to the directory containing the `nabPy` package
(the `src/` directory of a pyNab checkout) rather than installing it.

### Applying migrations

The schema is managed by Alembic. On a database that already has the
tables, bring it up to date with:

```bash
alembic upgrade head
```

Creating a fresh database instead: `alembic upgrade head` builds the whole
schema from the migrations in `alembic/versions/`. After changing a model,
generate a migration with
`alembic revision --autogenerate -m "what changed"`, read the generated
file, then apply it.

### Running on a cluster

The batch scripts assume you activate your environment and then submit —
SLURM copies the submitting environment into the job, so nothing extra is
configured. `scripts/apply_trap_filter.sh` checks at task start that both
nabPy and this package import, and fails immediately with a clear message
if not. Set `CALNET_VENV` only if you want a job to activate a specific
environment regardless of how you submitted.

On the machine that hosts the database, point `DATABASE_URL` straight at
the host instead of `localhost` — no tunnel, and much faster for bulk
ingest.

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

For the questions the analysis asks constantly — one pixel across every
run, optionally pinned to a trap setting or to run conditions —
[calibrationnet/queries.py](calibrationnet/queries.py) has ready-made
helpers so the join chains never need rebuilding:

```python
from calibrationnet.queries import (calibrations_for_pixel,
                                    fits_for_pixel, peaks_for_pixel)

# CE 976 centroid vs run for pixel 60 at the standard trap setting:
peaks_for_pixel(session, 60, line_label="CE 976", trap=(1250, 50, 1250))
# every fit for pixel 60 across runs:
fits_for_pixel(session, 60)
# calibrations for pixel 60 from runs at -300 V detector bias:
calibrations_for_pixel(session, 60, udet_bias=-300.0)
```
