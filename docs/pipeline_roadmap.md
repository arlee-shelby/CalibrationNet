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

## Phase 2 — ADC peak extraction & matching (`scripts/extract_adc_peaks.py`)

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

## Phase 3 — calibration builder (`scripts/calibrate.py`)

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

## Phase 4 — fit-routine flexibility (changeable functions only)

All benchmarked against phase 0 before adoption:

- **Gain scout**: before fitting, find the strongest peak in the full
  0–4500 histogram, ratio to its nominal position, scale the recipe
  windows — fixes fixed-window failures on low-gain pixels (test case:
  run 8718, UDET pixel 95).
- **Initial parameters**: keep the find_peaks scheme as the base — it
  is the proven core. Make its inputs adaptive (prominence relative to
  histogram scale, distances relative to estimated gain) rather than
  replacing the method.
- **Retry ladder**: when a fit fails or trips the error flags, retry
  with a small bounded set of variations (window nudge, width rescale),
  recording whatever produced the accepted fit in `config` so it is
  reproducible.
- Possibly restructure `get_fit` into composable steps (histogram /
  window / init / fit) with identical numerics.

## Resolved questions (2026-07-31)

1. Bi-207 CE L = 1047.795 confirmed (1047.975 was a typo; fixed).
2. Cd-109's stray "0.0372 8" was NNDC's Dose column bleeding into the
   copy; intensity is 44.2 (9) %.
3. Auger lines stored with NULL intensity + combined-2.9% note.
4. CHECK thresholds: centroids 5%, widths 50%.
