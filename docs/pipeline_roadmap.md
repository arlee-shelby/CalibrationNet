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

### Phase 4 completion plan (agreed 2026-08-04)

Ground truth driving it: the MODEL fits weak/unresolved peaks fine —
what fails is find_peaks needing distinct maxima ('amp1' failures; 8718
p99's visible 566 line drifting to a wide background hump). Verified by
hand-seeding peak 3 at the two-anchor predicted position (frozen model +
do_fit, zero code changes): it locked onto the real line (implied
568.0 keV vs 565.85) but with a degenerate covariance — so the real
initializer needs careful design. Every stage below: small, gated by
scripts/benchmark_fits.py AND by AS's eyeball verification, one commit
checkpoint each. The frozen functions are never touched; everything
composes add_parameters/do_fit (changeable) at script level.

- **4.1 Fixture set — DONE 2026-08-04** (no behavior change): reference
  pixels wired into benchmark_fits.py (`--fixtures`), from AS's lists:
  gold 8622p60/p1052, 8631p1067/p21, 8637p77/p1087/p1091; problem
  regimes 8718p99 (weak 566), 8718p95 + 8626p91 + 8715p1043 (low gain),
  8682p1028 (low gain + Cd, activates at 4.4), 8622p1051 (LDET blend),
  8718p84 (threshold Auger). The full low-gain registry is
  data/known_low_gain_pixels.csv — REFERENCE ONLY, because per AS:
  **low gain is not stationary** (pixels drift in and out of it), so
  the gain scout remains the detector and every scout-scaled fit is
  now flagged "LOW GAIN — verify" for human eyes regardless of fit
  quality. Also per AS: **the lower detector is physically different
  hardware in 2025 vs 2026**, so the 2026 detector may develop its own
  low-gain pixels — never assume the 2025 registry carries over.
- **4.2 Second-chance fit with computed starting guesses — implemented
  2026-08-04, awaiting AS visual verification.**
  `fit_from_predicted_start` in scripts/fit_spectra.py: each peak is
  seeded where its line must sit (NOMINAL_RELATION scaled by the scout
  ratio), amplitude read off the histogram, recipe widths; the exact
  frozen model runs via add_parameters + do_fit. It is the LAST attempt
  — only spectra that fail every find_peaks attempt reach it — and a
  health gate (converged + all centroid/width errors inside the CHECK
  thresholds, rejection reasons printed) means a junk fit is never
  stored. Plots are now on by default (fit_plots/, --no-plot to skip).
  Results on the reference pixels (module identity: BENCHMARK PASSED,
  all 14 previously-working fits bit-identical):
  - **8718 p95 (the low-gain flagship): both fits now succeed** — the
    CE fit resolves all six lines including both close pairs at 0.44x
    gain (chi2r 1.88, healthy errors). Its Auger acceptance is
    QUESTIONABLE (see below).
  - 8715 p1043 CE now fits via the retry ladder (errors flagged CHECK).
  - Still failing, correctly, with reasons printed: the LDET broad-
    resolution spectra (8637 p1087/p1091-class, 8631 p1067, 8626 p91 CE)
    — their peaks physically merge at standard trap settings, i.e. the
    BLEND class for 4.4/4.5 — and windows containing empty bins (NaN
    from zero uncertainty: 8718 p84 CE, several Augers).
  AS verification round 1 (2026-08-04) — observations and rulings:
  - **LDET Auger lines are BLENDED at standard trap settings** (both
    lines inside one peak; confirmed against the short-trap data). The
    2-free-peak Auger recipe is the wrong model on LDET — fitting the
    blend properly is 4.4's job. Seen on 8631p1067, 8637p1091,
    8718p1018/1030/1031, and suspected everywhere on LDET.
  - **Ruling: a fit without uncertainties IS a failed fit.** Now
    enforced at every attempt: non-converged or stderr-less results
    are never stored and the attempt ladder continues (possibly into
    the second-chance fit).
  - **Ruling: the 5% centroid bar is too strict for the Auger window**
    — raised to 25% there (per-recipe error_thresholds; CE stays 5%).
  - **Every fit attempt must be reviewable**: failures previously
    saved NO figure (why AS couldn't find the CE fits for 91, 84, 85,
    109, 1017, 1018, 1031). Now every failure saves a data-only figure
    with the predicted line positions marked.
  - **Threshold peak centered at ~0 ADC** distorts the Auger window;
    AS added an optional fixed-zero gaussian component to the model
    (verified numerically identical when unused; comparison copy
    updated). VERDICT after trials (2026-08-05): it did not help on the
    tested windows (drives its amplitude to 0 or soaks degeneracy), and
    AS's past experience agrees — extra parameters without fit-quality
    gain. It STAYS AN UNUSED OPTION during development and is expected
    to be REVERTED from the model before production. **REVERTED
    2026-08-10:** both fit-function files restored byte-identically to
    the pre-threshold original (md5 52c85de2409e284a8cdaf303369b82a9,
    from commit af590ef), the benchmark md5 updated back, and the
    same-numbers check re-run — the model is again exactly the
    physics original.
  - Open confusions to resolve with the new complete plot set: the
    sharp ~60 ADC peak on 8718 p84/p85 (ties to the ~62 ADC mystery
    line), p1043's Auger window contents, p109's fit region, and p95's
    Auger shape (expected: at 0.44x gain the Augers sit at ~35-50 ADC
    inside the threshold tail — they cannot look like 8622 p60's).
  - p99's missed 566 stays the flagship case FOR 4.3: its find_peaks
    fit passes every health check, so the rescue never runs — only a
    quality-retry (e.g. triggered by extraction refusing a line) can
    reach it.

  AS verification round 2 (2026-08-05): per-pixel verdicts established
  that the one-ratio prediction is biased in both directions, the LDET
  Auger region is a blend (likely Augers + Pb X-rays together), some
  pixels need a statistics gate or are hidden by the hardware
  threshold, and X-ray lines belong in the line tables. Full evidence,
  candidate strategies (AS's three ideas + six more), proposed order,
  and open questions: **docs/initial_guess_plan.md** — under
  discussion, nothing implemented yet beyond failure figures starting
  at 0 ADC. AS's model got its threshold component this same day
  (optional, bounded, comparison copy updated twice with same-numbers
  checks passing).

  Implemented after AS's round-3 rulings (2026-08-05, this session,
  awaiting AS plot review):
  - **Statistics gate (C-3)**: STATS_GATE in fit_recipes.py — a pixel
    is fitted only when its CE window (scaled by the scouted gain
    ratio) has >= 20,000 counts AND a strongest-peak height > 200
    above the window's median. Cutoffs chosen by AS from the test-case
    numbers: 1017 in, 1018 and (deliberately, at standard trap) 1030
    out. The CE window is the ONLY gatekeeper — a huge low-energy
    signal can mean a WORSE pixel. Skipped pixels print the numbers
    and save a data-only figure.
  - **Suspect flag**: when a gated-in pixel's low-energy-window peak
    exceeds its strongest CE peak, a SUSPECT warning prints — by the
    literature intensities that signal cannot be Auger lines.
  - **Auger only after CE**: the Auger recipe is skipped unless the CE
    fit succeeded (it provides the anchors). Failure figures reverted
    to the fit window only (plotting from 0 let the threshold peak
    dominate the y-scale — AS ruling).
  - **LDET testing moved to the short-trap outputs (AS ruling; UDET
    stays nabpy-standard).** Probe first: short-trap gain scale is the
    SAME as standard (976 K at ratio 1.00-1.04 of nominal 2885), so
    scout + two-anchor relation work unchanged. The former LDET blend
    class RESOLVES at short trap — all six CE lines separate, and the
    two-anchor predictions sit on every structure.
  - **Conditioned second-chance fit (C-5)**: the resolved spectra
    still failed — a weak peak (few dozen counts, e.g. the 566-M line)
    cannot determine its own tail shape or roam the window without
    going degenerate (singular covariance; peak 3 drifting into
    no-man's land or collapsing onto peak 2). New LAST rung of the
    ladder, tried only after the plain predicted-start is rejected so
    every previously accepted fit stays numerically identical: each
    centroid bounded to its prediction +- half the gap to the
    neighbouring prediction, and weak peaks' (amp < 15% of max) tail
    shape n/h held at 0.2/0.01 — bounds and initial values only, the
    frozen model untouched. Chosen over shape-tying (expr to the
    strongest peak) by trial: fixed shape passed 5/6 failing pixels,
    ties 4/6. Config records init="predicted-start-conditioned".
  - **Short-trap LDET results**: CE now fits cleanly on 1017 (plain
    rescue, 0.39x gain), 1087, 1091, 1030, 1031, 1043 (0.34x) — all
    conditioned rescue, chi2r 1.2-2.3, healthy errors. Still open:
    1067 (566 region too weak under the big K-976 tail — honest
    failure), 1051/1052/1027 (finder fits stored WITH absurd errors
    before the rescue can run — the CHECK-flag/quality-retry class,
    4.3), 1018 (gated, correctly). Standard-trap regression: p60/p99
    identical via recipe path (and correctly REFUSED overwrite —
    frozen calibrations), p21/p77/p95-CE identical via plain rescue;
    where the conditioned rung fired on p95's below-threshold Auger it
    was correctly rejected.
  - **OPEN for AS — the Auger recipe window at short trap**: LDET
    short-trap offsets are ~+5 keV (vs ~+29 at standard), so the Auger
    pair predicts to ~155-195 ADC — the 68 keV line lands OUTSIDE the
    (20,180) window and the rescue correctly refuses. The finder then
    fits threshold/mystery structures instead (1043: 15/26 ADC,
    chi2r 6.0; 1031: 31/88 ADC — stored, physics-wrong, flagged for
    review). Pb K X-rays (72.8/75.0/84.9 keV) would sit at ~205/212/242
    ADC as SEPARATE peaks at short-trap resolution. AS to set the
    short-trap Auger window (and peak count / X-ray handling — ties to
    C-6).
- **4.3 Quality retry — DONE 2026-08-05** (supersedes the original
  "keep the better fit" sketch; AS decisions same day). Every fit
  attempt now passes through ONE quality check
  (fit_recipes.fit_is_good): converged, all uncertainties present,
  centroid/width errors within the recipe thresholds, reduced chi2 <=
  10 (per-recipe max_redchi; the bar AS's original scripts used). A
  failing fit is RETRIED instead of stored-and-flagged, walking
  fit_recipes.fit_attempts: the recipe exactly as written first (a
  healthy pixel is untouched), then the recipe's retry_widths —
  starting sigmas MEASURED per peak from the data (find_peaks width
  output / 2.355; AS's insight that starting widths are the biggest
  lever), then explicit sets — then the same width options at each
  gentler peak-finder rung, then the 4.2 rescue (plain, conditioned),
  then everything once more at nominal windows if the scout had
  scaled. First attempt to pass wins; config records it (attempt +
  actual widths). If EVERY attempt fails: nothing is stored, any
  previously stored same-label fit is deleted (junk never kept), the
  failure figure shows the data + the closest-miss attempt dashed,
  and pixels that passed the statistics gate but failed all attempts
  are listed in fit_plots/fit_failures_summary.csv (per-run detail via
  --failures-detail). The CHECK flag is gone — a stored fit passes the
  check by construction; LOW GAIN — verify remains. Results on the
  reference pixels: 8622 p1051/p1052 short-trap junk (cen errors 4e3 /
  4e6) replaced by healthy conditioned-rescue fits (chi2r 1.53/2.31);
  p99's Auger rescued by the measured-width retry (its CE still passes
  statistically with the wrong 566 — the peak-spacing check, next,
  owns that class); 8718 p95's questionable Auger honestly removed.
  Auger retry evidence so far supports AS: measured widths came out
  5.7-8.4 where the recipe said 3.

  **Agreed sequence after 4.3 (AS, 2026-08-05).** Until ALL of these
  are done and vetted, nothing in spectrum_fits is final — it is
  development output we are free to overwrite: (1) the PEAK-SPACING
  check — **DONE 2026-08-05, same day** (see below); (1b) the AS-1
  FILL-IN — **DONE 2026-08-05** (fill_in_seeds/fit_seeded, new attempt
  stage between the finder ladder and the rescue: find_peaks found
  SOME peaks -> keep them at their real positions, seed only the
  missing ones at the line predictions shifted onto the found peaks,
  same width options as the retries; first proof 8622 p109's Auger —
  the finder's prominence escalation steps 3 peaks -> 1 there, every
  pure rescue was degenerate at any width, fill-in fits it healthily,
  chi2r 1.28, spacing check passed); (2) fit-range variation —
  **window part DONE 2026-08-05**: the PREDICTED-WINDOW pass
  (predicted_window in fit_spectra.py). When the pixel's predicted
  line positions do NOT all fit inside the recipe window (short-trap
  offsets push Auger 68 past (20,180); low gain can pull lines below
  it), the whole attempt sequence runs once more on a window built
  around the predictions: first line minus 1.5x the first gap, last
  line plus 1.5x the last gap (the same margins the trusted recipe
  windows have at standard settings), clamped above the threshold
  region. Fires ONLY when needed, so healthy pixels pay nothing;
  config records window="predicted window". Verified on 8622 p1052
  short-trap Auger: the pass fires at (99,248) and the finder/rescue
  aim at the right region — and the fits still fail HONESTLY, because
  at LDET short-trap the Auger 56/68 + Pb X-ray region is ONE broad
  unresolved hill (~60-250 ADC): a 2-free-peak model is
  under-determined there. That was a MODEL decision for AS — settled
  2026-08-10: NO blend fitting of any kind (see the ruling under
  4.3/4.4); the Fall-2025 LDET data is an oddball not to build
  around. Peak-count recipe variants: CLOSED as not-needed (AS,
  2026-08-10) — their motivation was the 2025-LDET blend class, now an
  oddball; revisit only if a 2026 failure class demands it. Also
  closed as 2025-oddball-tied: the short-trap Auger window numbers and
  the +8..13 ADC Auger offset observation. Fitting policy for data
  selection (AS, 2026-08-10): raster segments lack statistics — fit
  only runs/segments with dwells of roughly 20-30 minutes or more
  (may change later). Development speed (AS, 2026-08-05): whole-run
  fitting was far too slow for iteration — `fit_spectra.py --dev`
  fits only data/dev_pixels.csv (one representative pixel per known
  class per trap label, AS-editable), minutes instead of an hour;
  additionally, attempts whose starting inputs (found peaks + width
  guesses) are identical to an earlier attempt are SKIPPED — gentler
  finder rungs usually land on the same peaks, so doomed blend pixels
  stopped burning 16+ identical fits per window;
  (3) blend model — **REVERTED IN FULL, AS group ruling 2026-08-10:
  NO blend/tied-peak fitting of any kind, for any source, peaks, or
  data — including the Bi-207 Auger pair.** Reasons: (a) these fits
  must use the SAME fit function as the future SIMULATION fits, and
  in simulation every peak is resolved and fitted individually (e.g.
  Sn-113's 387/391 always resolve there); (b) the Fall-2025 LDET
  data, whose extreme resolution problems motivated the blends, is an
  ODDBALL — a separate case the fitting routines and pipeline must
  NOT be built around; the 2026 LDET data does not show it. History
  for the record: an Auger constrained pair (cen2/sig2 tied,
  2026-08-05) and general blend_groups (2026-08-10) were built,
  verified (they fixed the 2025-LDET blur pixels; intensity-tied
  amplitudes turned p1055-std CE from FAIL to PASS at identical
  chi2), and then removed the same day per this ruling. The auger
  error thresholds, briefly cen 75% / sig 150% to save blur fits,
  are back at cen 25% / sig 50%. Fits stored by the blend paths were
  removed when their pixels were refit. Still-open observation for
  AS (independent of blends): short-trap auger structures sat
  systematically +8..13 ADC ABOVE the CE-derived two-anchor
  predictions — possibly the Auger-vs-CE energy-loss offset
  difference; (4) the short-trap Auger window numbers (AS) — largely
  superseded by the predicted-window pass; **AS ruling 2026-08-05:
  UDET is NEVER calibrated with the short trap — UDET pixels are not
  fitted at short-trap labels at all (LDET_ONLY_TF_LABELS in
  fit_spectra.py)**; (5) LOW-GAIN VALIDATION (AS,
  2026-08-05): development runs on 8622 ONLY, which has NO low-gain
  pixels — before anything is final, validate against a low-gain run
  (8718 UDET p95 at standard trap, 8715 LDET p1043 at short trap);
  (6) CLEAN SLATE: delete all calibrations (un-freezes the fits),
  then all spectrum_fits, then run the vetted fitting fresh — the
  database then holds only good, vetted fits and the calibrations
  built from them. (Stale development fits of excluded / gate-skipped
  pixels — e.g. 8626 p91's Auger, chi2r 74 — sit in the table until
  that wipe. Development-frozen pixels — 8622 p60/67/80/99/109/1051
  hold calibrations from the phase-3 validation — print REFUSED and
  keep their old rows until then too; their figures and printed
  values are still current.)

  **Peak-spacing check — DONE 2026-08-05**
  (fit_recipes.peak_spacing_check, last step of fit_is_good; per-recipe
  spacing_tolerance, default 0.35). Fits with >= 3 peaks are checked
  gain-free: a line through the two anchor peaks (recipe anchor_peaks —
  CE 482 K and 976 K, the same anchors extraction uses) predicts every
  other peak's position from the line energies; each may be off by at
  most 0.35 of the smallest neighbouring predicted gap. 2-peak fits
  use the pixel's own two-anchor keV<->ADC relation (the one that
  seeds the rescue): each peak within 0.35 of the pair's predicted
  separation. Tolerance chosen from the reference pixels: correct
  fits sit <= 26% of a gap off the pattern, wrong peaks 40%+.
  Verified same-day: 8718 p99's CE 566 — the roadmap's flagship —
  is FIXED (attempt 1 spacing-rejected at 58.9 ADC off; the
  measured-width retry locks cen3=1638.2+-2.6, matching p60, chi2r
  1.24; still REFUSED overwrite while frozen); 8637 p77's peak-6
  regression caught (off 14.6, allowed 12.7) and the measured-x-2
  retry recovers the correct fit (cen6=3114.5, chi2r 1.43); the
  short-trap LDET Auger fits of threshold/mystery structures (8718
  p1030, 8719 p1017, 8715 p1043-class) now rejected with the printed
  reason showing predicted vs fitted positions — junk removed, nothing
  stored; 8622 p1051's merged 554/566 CE honestly rejected (off 14.7,
  allowed 12.7 — genuinely blended, a 4.4/range-work case); healthy
  fits (p60 bit-identical, p1052, p1087, p1091, p1043 CE, p95 CE,
  8718/8719 p1017 CE) all pass unchanged.
- **4.4 Blend fitting (strategies B/C) — DEAD, AS group ruling
  2026-08-10: NO blend/tied-peak fitting of any kind** (same fit
  function as the future simulation fits, where every peak resolves;
  see the ruling under 4.3 above). Cd-109 / Ce-139 recipes are still
  wanted, but with every peak fitted individually and free. Where a
  measured spectrum genuinely cannot resolve a pair (Cd's
  87.32+87.94), the treatment happens at EXTRACTION level, not fit
  level — strategy A (a single free fitted peak matched to an
  intensity-weighted derived kev_peak) remains available and does not
  touch the fit function. AS to decide when those recipes are
  written.
- **4.5 Short-trap validation** (label short-trap-Fall2025, whole 2025
  set available): fit 8622 p60 + p1051 at the short setting (the gain
  scout rescales windows), compare LDET 554/566 resolution vs standard
  — the pixel-1051 scrambled-fit test case. Gate: AS compares fits/
  calibrations side by side; decision on default LDET treatment.
- **4.6 Parallel physics checks (AS-driven)**: Auger long-dwell
  comparison (9402 segs 78-80, 3 h each) once keV corrections exist;
  identify the strong ~62 ADC line in 8718 skirt-pixel Auger windows;
  ops answer on horizontal +0.55/0.6" -> the two-dwell stripe fix and a
  plan regeneration.

Dropped: restructuring get_fit into composable steps — everything above
is achieved at script level without it.

**RESOLVED (2026-08-04):** source assignment and position planning now
pool per (holder, convention, FIELD) — field_key(run) captures magnet
currents + ExB (100 V bins). Validation: field-blind derivation had
anchor run 9326's own placements a full pixel column off its
eye-verified pixels; field-aware pools reproduce them exactly, and the
corrected assignments are applied. The review CSV carries a `field`
column and is tracked, so re-derivations diff cleanly. Details:
docs/source_assignment.md.

## Resolved questions (2026-07-31)

1. Bi-207 CE L = 1047.795 confirmed (1047.975 was a typo; fixed).
2. Cd-109's stray "0.0372 8" was NNDC's Dose column bleeding into the
   copy; intensity is 44.2 (9) %.
3. Auger lines stored with NULL intensity + combined-2.9% note.
4. CHECK thresholds: centroids 5%, widths 50%.
