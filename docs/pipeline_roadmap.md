# Fitting & calibration pipeline — roadmap

Agreed plan for finishing the analysis chain (fits → adc_peaks →
calibrations) and then making the fit routine flexible. Guiding rule
throughout, from AS:

> The fit PROCESS is fixed. `gaussian`, `background`,
> `lower_exponential`, `step_function`, `fit_model`,
> `residual_function`, `get_histogram_data_uncertainty` are rooted in
> physics / reliable lmfit routines and are not to be changed at this
> stage. `get_initial_peak_parameters`, `do_fit`, `get_fit` may evolve —
> structure, automation, flexibility — carefully and with minimal
> divergence, and only measured against a benchmark copy of the
> original.

## Phase 0 — benchmark protection — DONE 2026-07-31

- `calibrationnet/fit_functions_reference.py` is a byte-identical
  frozen copy of the original module
  (md5 `52c85de2409e284a8cdaf303369b82a9` — recorded in
  scripts/benchmark_fits.py, which verifies it on every invocation).
  It must never be edited; restore from git if it ever drifts.
- `scripts/benchmark_fits.py` is the gate for any fit-code change:
  - **integrity checks** (also `--check-only`): reference md5 intact;
    the seven frozen functions in the live module source-identical to
    the reference (verified to fail loudly on a tampered function);
    differing changeable functions listed informationally.
  - **fit comparison**: runs live and reference `get_fit` over the
    requested run pixels with the shared production recipes
    (calibrationnet/fit_recipes.py) and reports per fit the worst
    centroid/width PULL (|Δ|/stderr_ref), Δreduced-chi2, and success
    changes. Non-zero exit if any pull exceeds --max-pull (default
    0.5) or a success flag changes.
  - Baseline verified: with identical code, pulls are exactly 0.000.
- Workflow rule: any change to get_initial_peak_parameters/do_fit/
  get_fit is adopted only after
  `python scripts/benchmark_fits.py --runs 8622 9327` passes and the
  pull distribution is reviewed. The find_peaks-based initial-parameter
  scheme is the protected reference behavior.

## Phase 1 — line intensities (one-table schema change) — DONE 2026-07-31

- `intensity`, `intensity_error` (percent, NNDC, nullable) added to
  isotope_decay_energies (migration db80d607e393); CSV + seed script
  extended; all 19 lines seeded with intensities. Intensities are
  stable properties of the line (unlike keV values), so they live on
  the line — updated in place, not versioned — and not in kev_peaks.
- Bi-207 Augers: intensity NULL — NNDC reports only a combined 2.9%
  across the 56/68/80 keV Auger group (the 80 keV line is not fitted),
  with no split; noted on their kev_peaks rows.
- Corrections applied (confirmed by AS):
  - Bi-207 CE L of the 1064 transition is 1047.795 (matches 1063.656 −
    15.861 Pb L1 binding); the initially given 1047.975 was a typo —
    erroneous kev_peaks row deleted, correct one seeded.
  - Ce-139's fourth line added: "CE 166" = 165.5871 (14), 0.1085 (16) %.
- CHECK thresholds settled: centroid errors 5%, width errors 50%.

## Phase 2 — ADC peak extraction & matching — v1 DONE 2026-08-03

`scripts/extract_adc_peaks.py`. Validated on run 8622 pixels 60/67/80/
1051: clean pixels match with sub-keV CE residuals; pixel 80's junk
Auger fit and pixel 1051's scrambled CE fit (the LDET resolution
problem at standard trap settings) were caught — implausible peaks
stored with NULL line, never matched. Blend/partial matching (Cd-109,
Ce-139, and fits with fewer peaks than lines) is deliberately NOT in
v1; it lands with the phase-4 recipe work. Original design:

Per (run pixel, current fits):

1. **Anchors**: take the two highest-amplitude fitted CE peaks — for
   Bi-207 these are CE 482 and CE 976 (the K lines; intensity data now
   verifies the expected amplitude ordering).
2. **Two-point gain**: rough per-pixel ADC→keV line through the
   anchors. Per-pixel by construction, so low-gain pixels (e.g. UDET
   95/96 in the 2025 data) match correctly whatever their scale.
3. **Match**: predict every other known line's ADC position (including
   the Auger fit's peaks), match fitted centroids within a few peak
   widths, require uniqueness. Unmatched lines are recorded absent;
   unmatched fitted peaks are stored with isotope_decay_energy_id NULL
   and flagged.
4. **Sanity checks**: matched energies monotonic in ADC; fitted
   amplitude ratios roughly consistent with NNDC intensities.
5. Write adc_peaks: centroid/sigma/amplitude ± errors from the stored
   fit parameters, plus the matched line (or NULL).

**Unresolved blends** (Cd-109 87.32+87.94; Ce-139 164.50+165.59; with
the CURRENT trap filter setting some Bi-207 peaks blend too — a better
setting resolves them and those filter outputs will be ingested after
development). Three strategies, all representable in the schema because
a calibration records exactly which kev_peak row it used:

  A. **Intensity-weighted mean** (interim, first to implement): match
     the single fitted peak to a derived kev_peak at the
     intensity-weighted mean energy (origin "derived", version
     recorded), coexisting with the pure NNDC rows.
  B. **Constrained two-peak fit** (AS's plan, post-pipeline): fit both
     peaks with their energy separation and amplitude ratio constrained
     by the NNDC values/intensities; each fitted peak then matches its
     own line normally.
  C. **One free + one tied peak** (AS's variant): fit one peak, with
     the second's position and amplitude fixed relative to it by the
     energy/intensity ratios.

Start with A; B/C become recipe options once the fit-routine
flexibility work (phase 4) exists — they only touch the changeable
functions (parameter setup), not the frozen model.

## Phase 3 — calibration builder — DONE 2026-08-03

`scripts/calibrate.py`, validated end to end on run 8622 pixels
60/67/80/1051 (7 calibrations): replace semantics, freeze semantics
(re-extraction of calibrated peaks refused gracefully), is_current
uniqueness, and full read-back through calibrationnet/queries.py all
verified. Calibrations carry trap_filter_output_id (the ADC scale is a
property of the trap setting — migration 571a40f12016). First physics
from the chain: the linear terms (0.328–0.338 keV/ADC) match the
extraction anchor gains; the constant term sits near +29 keV on healthy
pixels — expected to encode CE energy-loss/threshold physics and to
improve with source-specific corrected keV values; per AS the metric
that matters for the precision goals is the constant term's
UNCERTAINTY, not its size. Including the Auger points raises reduced
chi2 to ~6–9.5 (CE-only: 1.3) with the quadratic term NOT absorbing the
deviation. Whether that is a statistics effect or a real reason to keep
Augers out of calibrations until keV corrections arrive should be
decided by comparing the same pixels in a much longer dwell (e.g.
8622's ~30 min vs a multi-hour parked segment) — deferred. Units and
the scale_covar=False convention: docs/fit_storage.md.
Original design:

- Per run pixel: gather matched adc_peaks across its current fits, pair
  with the chosen keV values (default: latest generic NNDC row; a
  source-specific simulation row when one exists), and fit linear and
  quadratic ADC→keV with uncertainties.
- **Hard rule: at least 3 matched points** (2 or fewer of 8 is never
  enough — AS); configurable upward.
- Store Calibration (label, coefficients ± errors, chi2/ndf/
  reduced_chi2, var_names + covariance, config) + one CalibrationPoint
  per (adc_peak, kev_peak).
- Fits are disposable until a calibration references their peaks; the
  FKs then freeze them, and refitting means a NEW calibration row
  (is_current moves). Different fit-combination calibrations coexist as
  separate, fully documented rows.

## Phase 4 — fit-routine flexibility — v1 DONE 2026-08-03

Implemented entirely at the SCRIPT level (fit_spectra.py +
fit_recipes.py): the scout and ladder vary only get_fit's INPUTS
(windows, peak-finder settings, width guesses), so fit_functions.py is
untouched and the phase-0 benchmark stays identically green.

- **Gain scout** (`SCOUT_ANCHORS`): locate the strongest peak above the
  threshold region, ratio to its nominal ADC (Bi-207: CE 976 at 2885),
  scale the recipe windows, the peak-finder distance, and the width
  guesses together. Within 5% of nominal the recipe is used exactly.
  If the scaled attempt fails outright, one fallback at nominal windows
  guarantees the scout can only ADD successes (added after skirt pixels
  1021/1031 briefly regressed from misfired scouts).
- **Retry ladder** (`peak_finder_ladder`): recipe settings first, then
  progressively gentler prominence (15 -> 10 -> 7 -> 5, finally with
  height 3). Whatever attempt succeeded is recorded in the fit's
  `config` (scout_ratio + attempt), so every fit stays reproducible.
- Measured on run 8622: 11 pixels that always failed now fit (24
  pixels with stored fits vs 15 baseline).
- **Low-gain pixels** (run 8718 UDET 95, gain 0.443x nominal): the
  scout finds them correctly and the CE structure is visible, but at
  that gain the 554/566 and 1048/1060 pairs compress to ~16 ADC and
  partially merge — a 6-distinct-peak model is wrong there. These
  pixels wait for the BLEND recipes (constrained pair fits), the
  remaining phase-4 item, deliberately deferred by AS until after the
  pipeline build.

Still open in phase 4: blend recipes (strategies A/B/C above), and
possibly restructuring `get_fit` into composable steps (benchmarked).

**Known issue (2026-08-03):** source assignment pools segments per
(holder, convention) — it already keys on the installation (holder),
but NOT on the magnet field. Field epochs on record so far: fall 2025 =
5-slot tray at 137 A (runs 8622–8865, incl. 8718); July 2026 scans =
6-slot at 110 A (9326–9378); run 9402 = 6-slot back at 137 A. So the
inches-2026 pool now mixes 110 A and 137 A segments in one set of
baselines and one trend — the same field-mixing the position planner
solved with --runs. Assignment needs (holder, convention, field)
separation before global re-assignment (scripts/assign_sources.py,
also run by process_run.py) becomes routine; until then, review the
assignment CSV after processing runs at a new field setting.

## Resolved questions (2026-07-31)

1. Bi-207 CE L = 1047.795 confirmed (1047.975 was a typo; fixed).
2. Cd-109's stray "0.0372 8" was NNDC's Dose column bleeding into the
   copy; intensity is 44.2 (9) %.
3. Auger lines stored with NULL intensity + combined-2.9% note.
4. CHECK thresholds: centroids 5%, widths 50%.
