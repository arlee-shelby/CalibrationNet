# Development plan: offline + online analysis (updated 2026-08-13)

The working state of the calibration analysis and everything that still
needs to be done, in enough detail that a fresh session (or person) can
pick up any item without re-deriving the context. Companion documents:
`docs/pipeline_roadmap.md` (the original pipeline design),
`docs/cluster_resources.md` (GT vs NERSC job sizing),
`scripts/offline/README.md` (offline pipeline usage + NERSC setup),
`docs/fit_storage.md` (what a stored fit looks like),
`docs/fit_retry_ladder.md` (how the retry ladder decides every fit —
the passes, rungs, gates, and quality check, in plain language).

---

## 1. The two tracks and how they relate

- **Online (database) track**: the original pipeline. Postgres lives on
  the GT cluster (ssh tunnel on :5432, slow controls on :15432);
  `scripts/apply_trap_filter.py` filters and ingests;
  `scripts/fit_spectra.py` fits FROM the database and stores fits.
- **Offline track** (`scripts/offline/`): the same analysis with files
  instead of the database — built 2026-08 while GT was down, run at
  NERSC. It shares the exact same engine modules
  (`calibrationnet/fitting.py`, `fit_recipes.py`, `fit_functions.py`,
  `calibration.py`, `hitmap.py`), so a fix in the engine fixes both
  tracks. Validated identical to the DB pipeline to the digit
  (run 8622: same reduced chi2, same calibration constants).

The offline filter CSVs use the cluster staging format, so they are
directly ingestable (`scripts/ingest_filter_output.py`) — that is how
run 9416 entered the database.

## 2. Standing rulings and constraints (do not re-litigate)

1. **Frozen fit model.** `calibrationnet/fit_functions.py` +
   `fit_functions_reference.py` are byte-identical and never edited;
   `python scripts/benchmark_fits.py --check-only` must stay green.
   Everything tunable (windows, widths, finder settings, thresholds)
   is an INPUT, defined in `calibrationnet/fit_recipes.py`.
2. **NO blend/tied-peak fitting, ever** (AS group ruling 2026-08-10).
   Data fits must use the same fit function as the future simulation
   fits, where every peak resolves and is fitted individually. This
   rule also cancels the known tail-shape bias (see 3).
3. **The residual tail deficit is a model-shape limit.** One shared
   exponential tail (beta) per spectrum; tested alternatives
   (beta start variations, split beta for lower/upper CE groups,
   conditioned stage A/B) all rejected 2026-08-12/13 with evidence —
   they do not fix it, and the same-fit-function rule makes the bias
   cancel against simulation. Do not add beta retries back without a
   pixel in the failure file that demonstrably needs one (the recipe
   has a comment where `retry_beta` would go).
4. **2025 LDET data is an oddball** — never build development on it.
5. **The clean slate happened 2026-08-13**: every dev calibration and
   spectrum fit was deleted (see 6.0). `spectrum_fits`/`calibrations`
   now only ever hold output of the 2026-08-13-or-later pipeline;
   re-running a fit REPLACES the stored one, so recipe fixes are
   absorbed by resubmitting, not by another wipe.
6. **AS pushes git commits and submits SLURM jobs manually** — prepare
   scripts and commands, do not run them for them. No conda, ever.

## 3. What is DONE and verified

### Engine (shared by both tracks)
- Retry ladder in `calibrationnet/fitting.py::run_recipe`: finder
  ladder (peak-finder rungs x width options, dedup on identical
  inputs) -> fill-in (found<n peaks kept, missing seeded from
  predictions) -> predicted-start rescue (plain, then conditioned with
  centroid bounds + weak-peak tails frozen at the strong peaks' fitted
  values) -> extra window passes (predicted window when lines fall
  outside the recipe window; nominal window when the gain scout had
  scaled). Beta starts at the frozen default everywhere.
- Quality gate `fit_recipes.py::fit_is_good`: convergence, all
  parameter errors present, centroid/width error thresholds (CE 5%/50%,
  Auger 25%/50%), reduced chi2 <= 10, peak-spacing check vs the line
  pattern (anchors peaks 1 and 4, tolerance 0.35 of the smallest gap).
  Hardened 2026-08-13 against degenerate covariance: any stderr
  exactly 0 rejects; any width below 2 ADC rejects (the same floor
  measure_peak_widths uses); any width stderr under 0.1% of its value
  rejects ("impossibly precise" — the 1041/1044 false passes sat at
  0.027%/0.0035% while the best genuine fit in the batch is 0.68%).
  Replayed over every accepted batch-1 fit: kills exactly the three
  known false passes (1041, 1044, 1023-CE's sig3=1.4), touches
  nothing else.
- Statistics gate (20k CE-window counts / 200 peak height); gated-out
  pixels produce no figures.
- Measured starting widths (FWHM/2.355) with median repair of
  half-prominence artifacts (< 2 ADC bins).
- Failure review: `fit_failures_summary.csv` (interesting failures
  only) + per-run detail; concurrency-safe via flock, with a sentinel
  fallback on filesystems that refuse flock (NERSC $HOME, OSError 524
  — found 2026-08-13: it had crashed every array task at its final
  step, so batches 1 and 2 have complete fits CSVs but NO failure
  summary; their failure info lives in the slurmout logs instead).
  GT home + scratch verified flock-clean.
- 2026 detector facts baked into understanding, not code: UDET tail
  beta ~8, LDET ~30-37; Auger offset ~+5 keV puts the 68 keV line at
  ~190-205 ADC (the Auger recipe window is (110, 250) as of
  2026-08-13, built for exactly these positions; 2025-style data at
  ~82/120 ADC falls back to the predicted-window pass, which rebuilds
  ~(25, 177) around the predicted lines — effectively the old window);
  LDET pixels normally resolve at fill-in/rescue, UDET at the finder.

### Offline pipeline + NERSC
- `scripts/offline/`: trap_filter.py (+ sbatch submit script, 32 cpu /
  100 GB shared QOS), fit_spectra.py (+ sbatch array submit script,
  1 cpu / 8 GB / 30 min per segment), calibrate.py, export_segments.py,
  show_hitmap.py, show_spectra.py. NERSC env at `$HOME/pyNabEnv`
  (see scripts/offline/README.md for the from-scratch build).
- 23 filter CSVs produced at NERSC: 9409 seg0-5, 9415 seg0-12,
  9416 seg0-3 (`$HOME/CalibrationNet/offline_output/filter/`).
- Full 23-segment fit array ran into `offline_output/fits_2026/`
  (plots in `plots/`, logs in `slurmout/`, one fits CSV per segment).
  First review done — findings in section 5.
- Hitmaps for all 23 segments: local `offline_output/hitmaps_2026/`.

### Database (GT back online 2026-08-13)
- Runs 9409/9415/9416/9464 seeded with correct segment counts
  (6/13/4/76).
- Trap filter data (`label='nabpy-standard'`): 9409 and 9415 were
  already ingested before the outage — verified equivalent to the
  NERSC CSVs (identical events to float precision; DB drops NaN-energy
  events and excludes pixel 0 + the 58 quirk by design). 9416 ingested
  2026-08-13 from the NERSC CSVs. **No 2026 fit results ingested — on
  purpose.** NERSC fit results live in files only.
- Jin simulation energies in `data/simulated_energies_Jin_simulations.csv`
  (see 5.4 — never merge into `data/decay_energies.csv`).

## 4. Review findings driving the next work (2026-08-13, from the
## 23-segment batch — figures in offline_output/review_batch1/)
## Status: findings 3 (Auger window), 4 (CE bound) and the
## 1041/1044/1023 false passes are addressed by the 5.1 fixes,
## pending confirmation on the NERSC rerun. 1 (multi-source) is now
## by-design (5.2 ruling). 2, 5, 6 unchanged.

1. **Multiple sources are installed, and the fitter assumes Bi-207
   everywhere.** 9415 seg0 pixel 29 is watching Cd-109 (everything
   below ~270 ADC, 84/87 blend at ~248); 9415 seg1 pixels 49 (UDET)
   and 1062 (LDET, facing it) are watching Sn-113 (two-peak K/L
   pattern, ratio 1.065). These pixels are NOT low gain and NOT
   broken — the gain scout latches onto the wrong peak and every
   Bi attempt fails honestly (nothing stored, correct outcome). The
   spacing check is the protection that kept wrong-source fits out.
2. **Pixel 96 (9415 seg1) is a genuine low-gain pixel (0.43x)** and its
   fits are RIGHT (full Bi pattern, conditioned rescue, chi2r 1.70) —
   the second validated low-gain case after 1106 (9409 seg0, 0.383x,
   fails everything — future low-gain work target).
3. **The Auger recipe cannot handle 2026 UDET conditions** (evidence:
   9409s2 p1023, 9415s0 p80/p1041/p1044, 9415s2 p73). The 68 keV line
   sits outside the (20,180) window so everything runs on the
   predicted-window pass, whose curved Compton background the
   linear-background model cannot represent. The fitter uses a wide
   gaussian as fake background: it either eats real peaks (73, 80 —
   good data failing with centroid errors in the thousands) or drapes
   over a smooth shoulder and passes with no real peaks (1023 — passed
   the spacing check by 0.1 ADC; 1041/1044 — accepted with
   sig2 stderr == 0.00 exactly, a degenerate-covariance artifact).
4. **CE window too tight at the top**: bound 3300 chops peak 6 on
   several pixels (1023: cen6=3289, sig6 blown to 26).
5. Minor: conditioned-rescue blends can undersize intermediate peaks
   (1027/1028) and once produced sig3=1.4 (1023) — same known blend
   amplitude-tradeoff family.
6. **1075-class hazard** (9409 seg0): stage C once accepted a 6-peak
   fit where the data has only 5 peaks (phantom peak, amp consistent
   with 0). No automatic gate separates it from the good blended case
   (1069) — amplitude significance ranks them the WRONG way. Eye
   review of stage-C acceptances is the answer; 1075 is an eye-review
   rejection if it recurs.

## 5. TO DO — offline track

### 5.1 Fit fixes (AS rulings 2026-08-13 — implemented same day)
- [x] CE recipe upper bound 3300 -> 3400. Verified on the 2025
      reference pixels: healthy pixels move <= 0.03 stderr (cen6
      unchanged, redchi slightly better from the extra background
      bins); only the two known-problem CE pixels (8622 p1051
      scrambled blend, 8718 p99 misplaced peak 3) shift, and their
      bare fits are unstable by definition.
- [x] `fit_is_good` hardened: stderr == 0 exactly rejects; width
      < 2 ADC rejects; width stderr < 0.1% of value rejects. The
      third check is the one that actually kills 1041/1044 — their
      stderrs were 0.0032/0.0006, tiny but NOT exactly zero, so the
      originally proposed exact-zero check alone missed them. The
      0.1% bar was chosen from the batch itself (best genuine fit
      0.68%, worst false pass 0.027% — 8x/4x margins); AS may want
      to revisit the number, it lives in
      `fit_recipes.py::MIN_SIG_RELATIVE_ERROR`.
- [x] Auger window (20, 180) -> (110, 250) (AS ruling: raise the
      bottom above the Compton shoulder — it dies out by ~100 ADC —
      and extend the top past the 68 keV line). The 2026 pair sits at
      ~141-201 ADC at nominal gain, so the recipe window now targets
      it directly; 2025-style data (~82/120) is rescued by the
      predicted-window pass, which rebuilds ~(25, 177). Confirmed on
      the reference pixels: bare 2025 Auger fits fail in the new
      window as expected (benchmark harness has no retry ladder), and
      the CE failure set is byte-identical before/after the change.
- [x] Batch-2 rerun done and reviewed (2026-08-13/14, local copies in
      `offline_output/fits_2026{,b}/`). Results: all three false
      passes dead (1041/1044/1023-Auger, plus 1032 — another
      impossibly-precise case the review never flagged); the 3400
      bound works (31 CE fits now hold a healthy peak 6 above 3250,
      e.g. 1023 sig6 26 -> 8.0); CE acceptance 125 -> 126. AS eye
      rulings on the width-floor CE losses: 1039/1051/96-s11/1091
      should never have passed in batch 1 (gate vindicated);
      1062/1079 borderline-acceptable-but-fine-to-lose. WATCH:
      9416s1 p1069's CE fit (batch 1: good-looking blend, chi2r 1.78)
      now fails the spacing check — AS judged the fit great by eye,
      so check whether it returns on the next rerun.
- [x] Auger window regression found and fixed (2026-08-14): batch 2
      lost 17 Augers because pointing the recipe window at the 2026
      peaks disabled the predicted-window pass (it only fired when
      lines fell OUTSIDE the recipe window), and no fixed bottom
      suits every pixel (bottom scan: 110 loses 1052/1053/106/62,
      95 recovers 1052 but breaks 1010). Fix: recipe bottom 100 +
      `predicted_window` now skips only on a same-window match, so
      the per-pixel pass always backs up a failed recipe pass.
      Validated from the DB: 1052/1053/62/106 all recover
      (chi2r 0.97-1.16), control pixel 77 unchanged; 1010 (a
      bottom-110-only catch, never a batch-1 fit) reverts to an
      honest failure. Also: 80/73 Augers still fail in batch 2 —
      genuinely hard, needs eye review of their failure figures.
- [x] **NERSC track RETIRED (AS ruling 2026-08-14)**: GT is back, the
      same data is ingested and verified equivalent, the engine is
      shared, and NERSC fit files were never going to be ingested —
      so all validation and production fitting moves to the DB on GT.
      NERSC remains the documented fallback for the next GT outage
      (scripts/offline/ + its README stay maintained). The fits_2026d
      run submitted 2026-08-14 (window/gate fix, pre-width-rules) can
      be glanced at when it lands but owes us nothing; fits_2026c was
      a batch-2 replica (stale checkout) and can be deleted.
      Historical note: 9409/9415/9416 join the GT campaign run list
      (run_list_2026.txt), which supersedes the "rerun at NERSC"
      loop and doubles as the DB-vs-offline parity check at scale.

### 5.1b Findings from the 9469 5-segment test review (2026-08-14,
### AS eye pass: "overall really really good"; plots in
### fit_plots_test/9469/)
- [x] **Statistics gate retuned (AS ruling 2026-08-14)**:
      20000/200 -> 15000/300. Admits the strong-peaked short-dwell
      pixels (9469 s0 p30, s26 p40 + ~9 similar across all recorded
      skips) and gates out the marginal-height pixels (245-269) that
      produced most narrow-width artifacts — removes exactly 4
      borderline 9469 fits among everything currently accepted.
      CAVEAT: of the four 2025 examples the original bar was chosen
      from, one flips — 1031 (66k counts, height 211) is now gated
      out by the height bar. It is 2025 LDET (the oddball, not
      fitted in any campaign), and NO currently-accepted fit outside
      the intended four is touched; if 200-300-height pixels matter
      in some future dataset, the bar is one number in STATS_GATE.
- [x] **"5 visible peaks" fits DECODED and FIXED — pancake phantoms
      (1075 family)**: in s26 p86 and s40 p1053 the weak peak 3 never
      separated from peak 2, so the fitter parked it as a huge
      near-flat pancake (sig 93 / 37 vs siblings 4-8) with amplitude
      consistent with ZERO (3.3+-9.2, 7.8+-11.8) — invisible in the
      figure, hence "5 peaks". AS ruling 2026-08-14: reject. Added
      `MAX_PEAK_WIDTH_RATIO = 3.0` to fit_is_good (widest peak vs the
      fit's median width; fits with >= 3 peaks only — for 2-peak fits
      the ratio is bounded < 2 by construction, so Augers are
      untouched). Replayed over EVERY accepted fit (126 batch-2 CE +
      all stored DB fits): rejects exactly the two 9469 pancakes,
      the three hidden batch-2 pancakes (sig 175-201: 9415s11 p1038,
      9416s1 p1049, 9416s2 p1031), and borderline 9415s10 p1027
      (widest 26.3 vs median 6.2 — the known smeared-blend family;
      AS may want to eye its batch-2 figure). Nothing else touched.
- [x] **Relative width floor added (AS ruling 2026-08-14)**:
      `MIN_PEAK_WIDTH_RATIO = 0.5` — narrowest peak must be >= 0.5x
      the fit's median width (>= 3 peaks only, same guard as the
      pancake cap; the absolute 2 ADC floor stays). Replay over all
      accepted fits: rejects exactly the two worst 9469 narrow cases
      (both p1054s, 0.39-0.41x) and four batch-2 fits (9409s5 p1015,
      9415s0 p1048, 9415s7 p1073, 9416s3 p1009 — narrowest 2.6-3.5).
      Rejected attempts retry through the ladder: demonstrated live
      on 9415s10 p1027, whose pancake attempts were rejected until
      the conditioned rescue produced a CLEAN accepted fit
      (chi2r 1.80) — the width rules upgrade fits, not just cull
      them. The remaining eye-borderline pixels (1043, 1062,
      1055/1089 at 0.55x) are either gated out by the new stats
      gate or survive as acceptable-borderline per AS.

### 5.2 Non-Bi sources (AS ruling 2026-08-13: NO source-aware fitting)
- Source-aware fitting (a per-segment source map feeding recipe
  selection) is DROPPED from the plan. Non-Bi pixels failing every
  Bi attempt honestly is the desired behavior — the failure row is
  itself a useful indicator that the pixel is watching a different
  source. Nothing is stored for them (the spacing check is the
  protection), and that stays the design until Cd-109/Sn-113 fitting
  is tackled as its own effort.
- [ ] (future, unchanged) Cd-109 recipe: ~40-280 ADC window seed, must
      fit 3 peaks (62.5/84.2/87.3 — the 84/87 pair never resolves; the
      simulation-fit-function rule applies) — needs the same-function
      simulation fits to exist first. Sn-113 recipe: 2-peak K/L
      (363.8/387.5) — not yet designed.

### 5.3 The remaining stages: adc_peaks -> kev_peaks -> calibrations
(Reframed 2026-08-14 for the DB track — the offline calibrate.py note
that used to live here is retired with the NERSC track. All stages
run on the fits the 6.1 campaigns are storing right now.)

**Stage 2 — adc_peaks (after fitting sign-off; tooling DONE):**
- [ ] `scripts/extract_adc_peaks.py` is built and encodes the design:
      centroids matched to lines in ascending order per window group,
      validated against a per-pixel two-anchor ADC->keV line (CE
      482/976, --tolerance-kev); unmatched peaks stored with NULL
      line, never guessed; freeze semantics; re-run REPLACES. Run it
      over every fitted run, then review match/NULL rates. Blends
      (Cd/Ce) unsupported by design — Bi-only for now.

**Stage 3 — kev_peaks: IMPLEMENTED, SEEDED, AND VALIDATED END-TO-END
(2026-08-14).** Migration ee04eeb163b2 applied (after the campaigns
drained — DDL on kev_peaks blocks behind the fitters' open read
transactions; never migrate mid-campaign). 16 Jin values seeded as
family "Jin-2026a". Trial: extract -> calibrate on pixels 40/49/53 in
BOTH 9409 (HV 0, shift +30) and 9469 (HV 27, shift +3) — extraction
matched every peak (0 unmatched, sub-keV residuals), and the same
pixel calibrated in both runs gives the SAME gain (p40: 0.32722 vs
0.32727 keV/ADC, within errors; p53: 0.32462 vs 0.32473) with
constants ~0. The HV-shift design is confirmed end to end. QA plots
in fit_plots_test/calibration_trial/ (AS eye pass; note 9469s2 p49
chi2r 33 and 9409s0 p53 chi2r 11 — one pulling point each, visible
in the residual panels). Original design notes below for reference:
- A keV value varies along FOUR axes: line, physical source (or
  none), detector (or none), simulation HV (or none) — because
  source-DEPENDENT simulation values are still coming (the DB was
  built for them; capability stays), plus future detector sets at
  other HVs (e.g. UDET 27). AS ruling: represent the new axes as
  REAL COLUMNS (alembic migration), not encoded strings —
  `kev_peaks.detector` (upper/lower/NULL) and `kev_peaks.hv_kv`
  (integer, NULL = HV-independent). `origin` stays coarse
  ("nndc"/"simulation"); `version` names the family ("Jin-2026a").
- [ ] Migration: the two nullable columns (existing rows untouched).
- [ ] Seed the Jin CSV: source_id NULL, detector per origin string,
      hv_kv 30 (UDET) / 1 (LDET), origin "simulation",
      version "Jin-2026a" (adapt seed_decay_energies.py; its current
      verbatim-origin copy would overflow the 20-char column).
- [ ] calibrate.py selection (replaces "source-bound else NNDC"):
      candidates = simulation rows for the line, never bound to a
      DIFFERENT source; source+detector match beats detector-only;
      exact-HV match beats canonical-HV-plus-shift; newest family
      last. A line with NO simulation value is a LOUD ERROR (AS
      ruling: cannot happen today — all 8 Bi lines covered on both
      detectors; provision only if it ever fires). NNDC rows are
      NEVER mixed into a simulation-frame calibration.
- [ ] HV shift at calibration time, VALIDATED 2026-08-14 with the
      9409-vs-9469 same-pixel displacement check (UDET pixels
      40/49/53: +27.35/+28.23/+27.50 keV for a 27 kV HV difference;
      13 LDET pixels: ~0 -> LDET independent of main HV):
      **target(run) = jin_value + (sim_HV - run_HV), in magnitudes**
      (readback convention: reported +27 means -27 kV — AS), with
      run_HV = round(|runs.hv|) (readback jitter, not physics: 27.03
      -> 27). UDET: +30 keV at HV-off runs, +3 at 27 kV. LDET:
      constant +1 keV until LDET HV is ever powered (long away).
      Record run_HV and the shift in the calibration config.
- [ ] The Jin values are NOT final: his simulation used a different
      fit function. When the simulation is refit with the frozen fit
      function, reseed as NEW rows under a new version ("Jin-2026b")
      — old rows and their calibration_points stay for provenance —
      and calibrations re-run on unchanged ADC centroids.
- [ ] NNDC physical energies (data/decay_energies.csv) remain the ONLY
      source for fit predictions. Mixing simulation rows into that
      file silently disables all prediction-based retries (happened
      2026-08-13; the files are now separate — keep them separate).

**SPRINT RECORD (2026-08-14/15, the quick-turnaround phase after the
campaigns) + LINGERING ISSUES — see the end of this section.**
- Stages 2-4 EXECUTED AT SCALE via scripts/calibrate.sh (self-
  submitting SLURM array; extract-then-calibrate per segment).
  Database now holds: 1378 spectrum fits, 6856 adc_peaks (only 3
  unmatched — 99.96% match rate), 2074 calibrations (linear +
  quadratic, label "simulation", targets Jin-2026a + validated HV
  shifts; plus 12 CE-only comparison variants, label
  "simulation-ce", unblessed).
- **LDET HV BUG found and fixed 2026-08-14** (caught by the 8-peak vs
  CE-only comparison): the main HV was applied to LDET targets, so
  every HV-on LDET calibration had its offset shifted ~-27 keV. Fix:
  LDET effective HV = 0 until LDET HV is ever powered (calibrate.py
  pixel_hv). Everything recalibrated in place; offsets now physical
  in all four datasets.
- 8-peak vs CE-only comparison (12 pixels, both eras/detectors):
  IDENTICAL gains (5th decimal), offsets within 0.02 keV — detector
  linear 26 keV-1 MeV, Auger extraction + targets validated.
- Notebook query layer grew: calibration_summary (group tables; N+1
  perf bug fixed — column selects + one grouped count), peak_table.
- Findings for the group: gains 0.328-0.330 everywhere; UDET 976
  width ~1.4 keV both eras; LDET improved 2.95 -> 1.91 keV
  (2025 -> 2026) but is genuinely WIDER than UDET in 2026 (full-shape
  FWHM 5.76 vs 3.69 keV — verified 4 ways, contradicts expectation);
  low-gain census per era (95 stable-low both eras; 96 confirmed 0.42
  in 2026, unmeasurable in Fall — Cd overhead; 1028 EXONERATED:
  normal 1.00 in 2026, historic observation likely the Cd-overhead
  scout artifact; all five Fall LDET low-gain pixels normal in 2026).

**LINGERING ISSUES (statuses as of the 2026-08-20 review):**
1. Fitting closing review: AS reviewed the campaign plots; the
   remaining statistics pass is optional (lightweight funnels on
   request). The two specific pixels are resolved/staged (5, 6).
2. [DONE 2026-08-20] Calibration review at scale, over 1112 current
   linear calibrations:
   - Per-line residuals: anchors ~0 by construction; **Auger 56
     +0.58 keV** and **Auger 68 +0.18** systematically high (the
     low-ADC nonlinearity, now measured); **CE 1048 -0.14 keV**
     systematically low (tight IQR); CE 566 noisy (blend), and
     **CE 1060 EXONERATED** (median +0.03 — the trial's +2 keV was
     tail, not systematic). These patterns are the expected
     fit-function-mismatch signature — document as the pre-Jin-2026b
     baseline; the frozen-function refit is the fix, not tuning.
   - chi2 tail: median 2.63, q90 9.3, but q99 84 — ~2 dozen
     calibrations above ~30 need the eye pass (worst: 8627 p1026
     284, 8629 p1074 283, 9469 p1022 230, 9416 p29 180). QA figures
     exist for all (calibration_plots on GT + _recovered locally).
   - Quadratic term significant (>3 sigma) in 13% — consistent with
     the same low-end curvature. RULING NEEDED: which type
     downstream consumes (both stay stored/blessed per type).
   - **CALIBRATION WEIGHTING REMOVED (AS ruling 2026-08-20)**:
     fit_calibration is now plain UNWEIGHTED least squares (every
     point equal; point errors stored but unused; parameter errors
     from residual scatter, scale_covar=True — the one deliberate
     deviation from the unscaled convention, meaningless without
     weights). reduced_chi2 now = mean squared residual in keV^2,
     so sqrt(reduced_chi2) = RMS deviation in keV — the old
     chi2-tail list (weighted units) is obsolete; regenerate the
     review list in RMS-keV terms after the full recalibration.
     Validated: p53/p60 gains shift < 1e-3 keV/ADC vs weighted.
     REQUIRES one full ./scripts/calibrate.sh pass on GT (also
     re-blesses the handful of test calibrations stored unblessed
     during validation).
   - Gold standard 8622 p60: gain 0.32826 vs historical 0.32809
     (0.05%), chi2r 0.73 — PASSES.
3. Fall 2025 LDET offsets run warm (median +2.0 keV, tail to +5.9)
   — unexplained; correlate with the residual patterns above during
   the eye pass (2025 LDET oddball / short-trap scale?).
4. LDET-vs-UDET 2026 resolution gap (AS: "come back later").
4b. Jin-2026b refit: AS defers — post-development, revisit when
   sub-keV absolute accuracy matters. The per-line residual baseline
   above is the ready-made before/after test.
5. [RESOLVED 2026-08-20, AS eye-verified "looks good"] 9416 s1
   p1069 CE fit ACCEPTED on rerun
   (chi2r 1.78, conditioned rescue — the old spacing rejection no
   longer reproduces), stored, extracted, calibrated. Eye-verify its
   figure during the calibration pass.
6. [RULED 2026-08-20] 80/73 Augers: accepted loss. The 50%-threshold
   test (547 candidates, no-store, 9469 + Fall LDET) settled it:
   raising the Auger centroid bar 25% -> 50% admits ONE marginal fit
   in the whole dataset — the failure population is bimodal (clean
   <25% or catastrophic 1000s%), so 25% sits in a natural gap.
   **AS ruling: 25% stays.** (80's best attempt: 43% = +-23 keV on a
   56 keV line — not a measurement.)
7. [FOUND + FIXED 2026-08-20] The same test exposed 14 CLEAN Augers
   (errors 1-17%) on pixels with no stored Auger fit: the re-sweep's
   per-pixel transaction rolled back a frozen-CE replacement TOGETHER
   with the fresh Auger from the same pass — silently discarding it.
   Fix: SKIP-FROZEN per recipe in fit_spectra.py (a
   calibration-frozen fit is KEPT, never re-fitted; a kept CE still
   anchors the Auger). Validated: 8844s0 p1037's Auger now fits at
   PRODUCTION thresholds (chi2r 1.11, ~4% errors) and stores. Bonus:
   re-sweeps stop burning the ladder on frozen pixels — they get
   fast. Recovery of the remaining lost Augers (incl. any in the
   untested Fall UDET / 9409/9415/9416): one re-sweep + calibrate.sh
   after the commit.
8. [RESOLVED 2026-08-20 — bookkeeping ruling, done NOW at AS
   request] NO versioning, NO cross-label "current". A calibration's
   identity is (trap filter output, type, LABEL); labels are
   permanent coexisting target families that never interact; which
   one an analysis uses is an explicit query-time choice. Renamed:
   "simulation" -> jin2026a (2226 rows), "simulation-ce" ->
   jin2026a-ce-only (24 rows — the CE-only comparison; deletable on
   AS's word). calibrate.py: cross-label demotion and --no-current
   REMOVED (is_current dormant); queries label-explicit (default
   jin2026a). The LABEL REGISTRY + the DEVELOPMENT RITUAL (export ->
   delete calibrations = the deliberate unfreeze -> refit ->
   re-extract -> recalibrate) are written in docs/fit_storage.md —
   routine operation is purely additive; future target families
   (jin2026b, nndc) are new labels requiring NO refits.
7. Pixel 91 unexcluded but never refit (campaigns ran before the
   change): refit runs 8626/8685/8837. Pixel 1106 low-gain target
   still fails everything.
8. Jin refit with the frozen fit function (external) -> reseed as
   Jin-2026b -> recalibrate; only then are targets final.
9. Auger implied-energy residuals up to ~2 keV in extraction —
   quantify the low-ADC nonlinearity they gauge.
10. gain_map/low_gain_report should distinguish measured-normal /
    measured-low / NO-MEASUREMENT (the 96/1028 lesson).
11. **THE 9469 UDET COVERAGE FAILURE — diagnosed 2026-08-15** (47 of
    76 promised pixels): the planner's UPPER readback->frame trend
    was corrupted (d(y)/d(horizontal) fit -5.15 hex/inch vs the true
    -4.33 measured by AS's ground-truth dwells and matched by the
    lower fit -4.38), mis-pointing every UDET slot by up to ~4 mm.
    UDET's centered rate is ~900 counts/min (SAME in 2025 and 2026 —
    LDET ~4300; the sources have always favored LDET ~5x), so
    30-minute dwells work ONLY when truly centered — 2025 was, 9469
    wasn't. Root cause of the bad fit: circularity — weak, displaced
    UDET excess maps biased the located frames that feed the trend.
    Fixed in scripts/optimal_positions.py as `--shared-trend` (rigid
    tray = one physical trend, AS ruling: lower slopes for both
    detectors, upper intercepts refit). FOLLOW-UPS:
    a. [RESOLVED 2026-08-15] Shared trend is now the DEFAULT
       everywhere: `share_trend_slopes()` in
       calibrationnet/pipeline/source_assignment.py, applied inside
       locate_all_frames (share_slopes=True default), inherited by
       the planner; `--independent-trends` is the debug-only escape
       (stem marker "_indeptrends"). Verified: flagless planner
       reproduces the corrected trend and plan exactly. Plan files
       predating 2026-08-15 still carry the broken UDET pointing —
       AS's locked sequence for the data opportunity stands
       (recovery_schedule2.csv); regenerate anything else before use.
    b. [RESOLVED 2026-08-15] Source assignments REGENERATED under the
       corrected trend and applied as-is (AS: no review — CHECKs skip
       by design, fixable later): 15784 claims, 1703 CHECK rows
       skipped; only 95 pixels changed source identity (the rest is
       ring-edge churn) — diff in plans/assignment_diff_report.csv.
    c. Left-edge distortion zone: AS's 38->40 observation (same slot,
       DOUBLE the displacement of the other slots for the same stage
       move) means the field-line mapping is locally nonlinear near
       the far-left column — a linear trend cannot capture it; plan
       predictions there carry extra uncertainty. Characterize with
       test dwells / future scan data; possibly a per-region
       correction in the planner.
    d. The 5x UDET/LDET rate asymmetry is a standing fact of the
       source installations (both eras). Raise source orientation
       (UDET-facing or double-sided) before the NEXT installation —
       it sets every calibration plan's duration (~0.75 h/position).

**Stage 4 — calibrations (tooling DONE, waits on stage 3):**
- [ ] `scripts/calibrate.py` already does weighted LSQ (linear +
      quadratic, scale_covar=False per convention), >= 3 points,
      per-point provenance (each CalibrationPoint records its
      adc_peak AND kev_peaks row), is_current blessing, REPLACE
      semantics. After the stage-3 pairing lands: run at scale,
      verify against the 8622 p60 gold standard
      (docs/example_outputs.md), AS review. The clean slate already
      happened (6.0), so this first full calibration set is produced
      entirely by the vetted pipeline.

### 5.3b Query library for notebook analysis (ongoing workstream,
### started 2026-08-14 at AS request)
- `calibrationnet/queries.py` now has a NOTEBOOK layer: functions that
  open their own session and return pandas DataFrames (or plottable
  arrays), so a Jupyter notebook is just imports + plots. Live-tested:
  `runs_overview()` (what's in the DB), `fit_overview(runs)`
  (acceptance picture incl. winning attempt/window), `gain_map(runs)`
  (per-pixel gain vs nominal from CE anchors), `spectrum(run, pixel)`
  + `stored_fit_curve(run, pixel)` (reproduce any fit figure),
  `source_map(run, segment)`; ready for later stages:
  `centroid_trend(pixel, line)` (needs adc_peaks),
  `calibration_map(runs)` + `calibration_points_table(run, pixel)`
  (need calibrations). THE MODEL: this layer grows ON REQUEST — AS
  describes the plot/question, the query gets added here; AS does not
  need to write SQL/ORM.

### 5.3c GATE-ONLY FIT SELECTION (AS ruling 2026-08-15 — supersedes
### assignment-driven selection everywhere)
- Every pixel passing the statistics gate is FITTED, whatever its
  assignment: the assigned isotope's recipes when one exists, else
  Bi-207. Source assignment now influences NOTHING in fitting — it is
  the prediction/bookkeeping layer it was designed to be. Implemented
  in scripts/fit_spectra.py; every fit's config records
  recipe_isotope + assigned_isotope and the log prints
  "assigned=none/Cd-109/..." so fallback fits stay identifiable.
- WHY (measured 2026-08-15): the old skip-unassigned rule silently
  lost real coverage — 25 gate-passing unassigned 9469 dwells never
  attempted (incl. missing "unreachable" holes 116, 1111, 1119), and
  10/10 gate-passing Cd-ASSIGNED dwells fit cleanly as mis-claimed Bi
  light (CE chi2r 1.38-1.91; Augers failed honestly where Cd floods
  the low window — one genuine Auger pass at Bi-consistent
  centroids). Pure-foreign spectra never reach the ladder anyway:
  347/357 Cd dwells stop at the gate. The quality gate + spacing
  check remain the wrong-source protection (unchanged since batch-1).
- The recovered 9469 dwells were fitted immediately (2026-08-15) and
  the three campaigns were re-swept on GT.
- **Extraction had the same disease (fixed 2026-08-20)**: unassigned
  pixels were fitted but silently skipped by extract_adc_peaks.py
  ("skipped (no source)") — fits with zero adc_peaks and no
  calibration. Fix: extraction takes the isotope from the FIT's own
  config (recipe_isotope, recorded since gate-only fitting), source
  only as fallback for pre-change fits. After the fix + one
  calibrate.sh pass: 0 peak-less fits, 7426 adc_peaks, 2248
  calibrations; 9469 coverage 52 UDET (holes 44->39: recovered
  10/11/38/79/116) and 81 LDET (holes 16->13: 1101/1111/1119).
  Freeze refusals in fit_spectra.py now also record their own honest
  failure stage instead of nothing.
- LESSON, recorded: the gate-only ruling must hold at EVERY stage
  that selects work — fitting, extraction (both done); calibration
  never depended on assignment. When adding a pipeline stage, its
  selection rule is "does the input exist", never "is a source
  assigned".

### 5.4 Low gain (reworked 2026-08-14: identify from results, avoid
### nothing)
- [x] **No pixel is avoided anymore (AS ruling)**: pixel 91 removed
      from `data/excluded_pixels.csv` (now empty — the mechanism
      stays for genuine hardware cases). NOTE: the running campaigns
      started with 91 still excluded; refit the runs where 91 holds a
      source claim (8626/8685/8837 per the historical registry) after
      GT pulls the change, or catch it on any future full pass.
      `data/known_low_gain_pixels.csv` stays as historical reference
      only — low gain is not stationary and nothing enforces it.
- [x] **`scripts/low_gain_report.py`**: per fitted pixel, gain ratio
      from the fitted CE 482/976 anchor centroids vs the nominal
      relation (robust — unlike the stored scout_ratio it cannot be
      fooled by which window pass won), cross-checked against
      scout_ratio, and against the calibration linear term once
      calibrations exist (the eventual official number). First run
      over the in-flight campaigns: every historical registry pixel
      confirmed from results (1021 0.27, 1043 0.34, 1017 0.38,
      1032 0.39, 95/96 0.42-0.44, 1054 0.65 — stable across runs),
      PLUS a previously unknown one: 9469 s39 p100 at 0.386,
      successfully fitted. This report is the general-checking answer:
      run it after any campaign, eyeball the flagged tail.
- [ ] Pixel 1106 (9409, 0.383x) still fails everything — the low-gain
      validation target. Pixel 96 (9415, 0.431x) works and is the
      reference for what success looks like.

## 6. TO DO — online (database) track

### 6.0 State changes 2026-08-13 (second half of the day)
- **Clean slate EXECUTED** (AS ruling — everything stored so far was
  development): all 14 calibrations, 82 calibration points, 62
  spectrum fits, and 78 adc_peaks deleted. The DB now holds NO fits
  and NO calibrations; nothing is frozen. Everything stored from here
  on comes from the 2026-08-13 recipes (and re-running REPLACES
  same-label fits, so a future recipe fix just means resubmitting).
- **9464 is a RASTER run** (~2-minute dwell points): statistics are
  below the fitting gate everywhere, and it is NOT a fitting target.
  Its purpose was per-pixel optimal positions, which were derived and
  then USED for run 9469's segmented installation. Its source
  assignment is done (5 sources: 4 Bi + 1 Cd, 2325 claims).
- **HV bookkeeping is already solved in the schema**: runs carries an
  `hv` column populated at seeding (9464/9469: 27.0, 9402: 0.0) —
  calibrate-time code should read it from there, not slow controls.
- New tooling: `scripts/fit_spectra.py --detector {udet,ldet}` filter;
  `scripts/submit_fit_spectra.sh` + `scripts/fit_spectra.sh` (PACE
  SLURM array for DB fitting, same manifest/chunking pattern as the
  trap filter batch, 1 cpu / 8 GB / embers per task).

### 6.1 The full fitting campaigns (GO 2026-08-14 — the "full test")
Both eye-pass tests approved (Fall 2025 5-run test "very good";
9469 5-segment test "really really good"). 9469 source assignment
applied (1977 claims; seg 46 has zero claims and 29/49/50 nearly
none — AS may revisit the review CSV later). Two submissions, both
on GT from the repo root, env active, after pulling the final
recipe commit. Run TOGETHER with MAX_SUBMIT=24 each (the embers QOS
caps ~50 SUBMITTED tasks per user, and the two arrays would total
~76 tasks at the default 40) — or sequentially with defaults:
- [ ] **Fall 2025 UDET** (106 runs 8622-8865, one segment each):
      `MAX_SUBMIT=24 ./scripts/submit_fit_spectra.sh
       development/outputs/run_list.txt fit_plots_fall2025
       --detector udet`
- [ ] **2026 runs, both detectors** (9409/9415/9416/9469 = 77
      segments; run_list_2026.txt; non-Bi pixels skip via their
      assigned sources; doubles as the DB-vs-offline parity check):
      `MAX_SUBMIT=24 ./scripts/submit_fit_spectra.sh
       run_list_2026.txt fit_plots_2026`
- [ ] Review: acceptance counts + `fit_failures_summary.csv` in each
      plot dir, AS eye pass (incl. the 1069 watch item and 80/73
      failure figures), then FITTING SIGN-OFF.
- Housekeeping done 2026-08-14: stored dev fits deleted for the four
  9469 pixels the retuned gate now excludes (s0 p1055/p1089,
  s26 p1054/p1074) — gate-skipped pixels are never refit, so those
  would have lingered stale.
- Fit-result ingest for the NERSC-era file-based fits: do NOT —
  superseded by refitting from DB data.

## 7. Quick-start for a fresh session

- Python env: `source ~/NabEnv_db/bin/activate` (never system python,
  never conda). DB needs the GT tunnel on :5432 (slow controls
  :15432); check with a 1-line get_session query.
- NERSC: `ssh -i ~/.ssh/nersc ashelby@perlmutter.nersc.gov` (sshproxy
  cert; re-run sshproxy when it expires). Repo at
  `$HOME/CalibrationNet`, env at `$HOME/pyNabEnv`, everything under
  `$HOME` deletable folders. /tmp is per-login-node — use $HOME for
  file handoff between commands.
- Integrity check before/after any engine change:
  `python scripts/benchmark_fits.py --check-only` (function md5s) and
  `--reference-pixels` (live-vs-reference fits, pulls must be 0.000).
- Local review artifacts from the 2026-08 batch:
  `offline_output/review_9409s0/` (first 2026 fits + tail_fix_check +
  experiments), `offline_output/review_batch1/` (the 11-pixel review
  of the 23-segment batch, with per-pixel attempt logs),
  `offline_output/hitmaps_2026/` (all 46 hitmaps).
- The user is a physicist; prefers plain language over jargon, wants
  the WHY of every mechanism, reviews figures personally, and makes
  all recipe/threshold decisions. Present evidence, propose, wait for
  the ruling.
