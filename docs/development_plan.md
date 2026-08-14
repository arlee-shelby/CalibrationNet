# Development plan: offline + online analysis (updated 2026-08-13)

The working state of the calibration analysis and everything that still
needs to be done, in enough detail that a fresh session (or person) can
pick up any item without re-deriving the context. Companion documents:
`docs/pipeline_roadmap.md` (the original pipeline design),
`docs/cluster_resources.md` (GT vs NERSC job sizing),
`scripts/offline/README.md` (offline pipeline usage + NERSC setup),
`docs/fit_storage.md` (what a stored fit looks like).

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
  only) + per-run detail; concurrency-safe via flock (safe for SLURM
  arrays sharing one summary).
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
- [ ] Rerun the full 23-segment array at NERSC
      (`./scripts/offline/submit_fit_spectra_nersc.sh`, AS submits;
      push the recipe/gate commit first so NERSC pulls it). Rerun
      into a FRESH out dir (pass it as arg 2, e.g.
      `offline_output/fits_2026b`) so fits_2026 stays as the
      before-picture for acceptance-count comparison. Then review
      acceptance counts + failure summary, AS eye pass.
      Watch for: pixels whose previously-accepted fits the new gate
      now rejects (1041/1044/1023 will retry — they may land on a
      different attempt or fail honestly), and Auger acceptance on
      the pixels that used to fail on the predicted-window pass
      (80, 73 — good data that should now fit in-window).

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

### 5.3 Calibration step (blocked on fitting sign-off)
- [ ] `scripts/offline/calibrate.py` currently takes one --kev CSV.
      Needs: per-DETECTOR target selection from
      `data/simulated_energies_Jin_simulations.csv` (origin
      `Jin-simulation-UDET-30kV` for UDET pixels,
      `Jin-simulation-LDET-1kV` for LDET), plus the per-RUN HV shift
      **shift_keV = data_HV - simulation_HV** applied at calibration
      time (never edit the CSV): run 9409 ran UDET at 0 kV and LDET at
      0 kV -> +30 keV UDET, +1 keV LDET. 9415/9416 HV settings still
      unconfirmed — read them from slow controls per run rather than
      assuming.
- [ ] The Jin values are NOT final: his simulation used a different
      fit function. When the simulation is refit with the frozen fit
      function, only the CSV values change and calibration re-runs on
      the stored ADC centroids — fits never depend on target values.
- [ ] NNDC physical energies (data/decay_energies.csv) remain the ONLY
      source for fit predictions. Mixing simulation rows into that
      file silently disables all prediction-based retries (happened
      2026-08-13; the files are now separate — keep them separate).

### 5.4 Low gain
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

### 6.1 Fitting campaigns (AS request 2026-08-13)
- [ ] **Fall 2025 UDET refit** (runs in
      `development/outputs/run_list.txt`, 106 runs 8622-8865):
      `nabpy-standard` label, `--detector udet` (2025 LDET is the
      oddball — not fitted; UDET is never fitted at the short-trap
      label, existing ruling). Smoke-tested locally on 8622 seg0:
      --detector works, 2025 Auger lines fall back to the predicted
      window (27, 175) as designed, previously-frozen pixel 109
      refits after the calibration wipe. AS submits:
      `./scripts/submit_fit_spectra.sh development/outputs/run_list.txt
      fit_plots_fall2025/ --detector udet`
- [ ] **9469 (the optimized-positions segmented run, 54 segments,
      4 Bi + 1 Cd)** — the pipeline once the trap filter ingest
      finishes (in progress 2026-08-13, ~49/54 segments in):
      1. `python scripts/pending_segments.py --runs 9469 --summary`
      2. `python scripts/assign_sources.py` -> AS reviews/edits
         `source_assignment_review.csv` -> `--apply`
      3. fit BOTH detectors:
         `./scripts/submit_fit_spectra.sh run_list.txt fit_plots_9469/`
         (run_list.txt currently holds 9469)
- [ ] After the offline batch is signed off: confirm DB fitting
      reproduces the offline results on 9409/9415/9416 (expected:
      identical — the DB copies differ only by dropped NaN events,
      which never enter histograms).
- [ ] Fit-result ingest for the NERSC-era file-based fits: do NOT —
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
