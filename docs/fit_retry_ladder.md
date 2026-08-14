# The fit retry ladder

How one pixel's spectrum becomes an accepted fit (or an honest
failure). This documents `calibrationnet/fitting.py::run_recipe` — the
engine shared by the database pipeline (`scripts/fit_spectra.py`) and
the offline pipeline (`scripts/offline/fit_spectra.py`), so everything
here applies identically to both. The fit MODEL itself
(`calibrationnet/fit_functions.py`) is frozen and never edited; the
ladder only varies the model's INPUTS: the ADC window, the starting
peak positions, the starting widths, and the starting tail length.

The one rule that shapes everything: **attempts run in a fixed order
and the first one that passes the quality check wins.** A pixel whose
very first attempt is healthy is fitted exactly as the recipe is
written and never sees the rest of the ladder. A pixel where every
attempt fails stores NOTHING — a junk fit never enters the results —
and instead leaves a row in the failure CSV and a figure showing the
data with the best rejected attempt drawn on top.

## Before the ladder: gates and preparation

1. **Statistics gate.** A pixel is only fitted when its CE window
   carries enough signal (Bi-207: >= 20 000 counts in the window and a
   strongest peak >= 200 above the background median). Gated-out
   pixels are recorded as `statistics gate` skips, not failures.
2. **Gain scout.** The strongest line in the full histogram is located
   and compared with where it sits at nominal gain (Bi-207: the 976 K
   CE line at ~2885 ADC). If the pixel's gain is more than 5% off
   nominal, every recipe window, the finder's minimum peak separation,
   and the starting widths are scaled by the ratio. Within 5%, the
   recipe is used exactly as written. This is what lets genuinely
   low-gain pixels (e.g. 0.43x) be fitted with the same recipes.
3. **Line predictions.** Where the decay lines OUGHT to sit in ADC,
   used by the seeded rungs and the quality check. Preferred source:
   the pixel's OWN keV<->ADC relation from its two strongest CE
   anchors (482 K and 976 K — "two-anchor relation"). If the anchors
   cannot be identified, the fallback is the nominal relation scaled
   by the scout ratio. Prediction energies come ONLY from
   `data/decay_energies.csv` (NNDC physical values — never simulation
   values; mixing them silently disables every prediction-based rung).
4. **Recipe order.** Recipes run in the order listed in
   `fit_recipes.py::RECIPES` (Bi-207: CE first, then Auger), and the
   Auger fit only runs if the pixel's CE fit succeeded — the Auger
   window is low-statistics and needs the CE-derived relation to be
   trustworthy. Losing a CE fit therefore always costs the Auger fit
   with it.

## The ladder: up to three passes over different windows

Each PASS runs the same five rungs (below) on one ADC window. The
passes, in order:

- **Pass 1 — the recipe window**, scout-scaled if the scout fired.
  For Bi-207 today: CE (1200, 3400), Auger (100, 250).
- **Pass 2 — the predicted window**, built for THIS pixel around where
  its own relation puts the lines: first line minus 1.5x the first
  line gap, to last line plus 1.5x the last gap (clamped to (20,
  4490) — the low clamp keeps the hardware threshold region out).
  This pass exists because trap settings, HV, and detector differences
  shift where a line group sits: one fixed recipe window cannot fit
  every era of data (2025 Augers sit at ~82/120 ADC, 2026 at
  ~141-201), and the batch-1/batch-2 comparison of 2026-08-13 showed
  even same-era pixels need bottoms differing by 10-20 ADC. The pass
  is SKIPPED only when the window it would build is essentially the
  recipe window already (both edges within 5 ADC) — then it really
  would repeat the same attempts. (Until 2026-08-13 it was skipped
  whenever the predicted lines merely fit inside the recipe window;
  that stranded pixels whose peaks were in-window but whose background
  context was not — the Auger losses of batch 2.)
- **Pass 3 — the nominal window**: only when the gain scout had
  scaled pass 1, everything runs once more at the UNSCALED recipe
  window. This is a safety net against a WRONG scout: if the scout
  latched onto the wrong peak (it happens on odd spectra — e.g. a
  non-Bi source, or a dominant low-energy artifact line), pass 1 ran
  on a mis-scaled window and this pass restores the plain recipe.
  For a truly low-gain pixel this pass fails harmlessly; the scout
  can only ADD successes, never remove them.

Attempts whose starting inputs (window + found peaks + width guesses)
are identical to an earlier attempt are skipped — gentler finder
settings often find exactly the same peaks, and refitting identical
inputs can only repeat the same rejection.

## The five rungs within each pass

1. **The recipe as written.** `find_peaks` locates bumps in the
   windowed histogram with the recipe's finder settings; the fit
   starts from those bumps with the recipe's starting widths.
2. **Width retries.** The SAME found peaks, different starting widths
   — the proven lever, tried before anything changes which bumps seed
   the fit. Options come from the recipe's `retry_widths`: each peak's
   own width measured from the data (full width at half height /
   2.355, with a median repair for the sub-2-ADC artifacts that
   half-prominence measurement produces on blended peaks), optionally
   scaled ("measured x 2"), then any explicit width sets (Auger: 5,5
   then 8,8 — Auger peaks want larger starting widths).
3. **Finder ladder.** Progressively gentler peak-finder settings
   (prominence 10, 7, 5 — the last rung also lowers the height
   threshold), each rung trying all the width options again. These
   change WHICH bumps seed the fit.
4. **Fill-in.** When the finder found SOME of the peaks but not all
   (noisy windows make its raise-prominence loop step over the wanted
   count), the found peaks are KEPT — local information from the data
   beats a pure prediction — and only the missing peaks are seeded, at
   the predicted positions shifted onto the found peaks (their average
   offset). Same width options; found peaks use their own measured
   widths, missing peaks the found peaks' average.
5. **Predicted-start rescue.** The finder is abandoned entirely: every
   peak is seeded where the lines are predicted to sit, amplitudes
   read off the smoothed histogram right there. Two escalations:
   - *plain*: seeds only, everything free;
   - *conditioned*: only after plain was rejected. Each centroid is
     fenced to its prediction plus or minus half the gap to the
     neighbouring prediction (peaks cannot swap or collapse onto each
     other), and the weak peaks' tail-shape parameters are frozen at
     the values the strong peaks of the plain fit converged to (a
     few-dozen-count peak cannot determine its own tail shape without
     going degenerate).
   The seeded rungs (4 and 5) also try any `retry_beta` starting tail
   lengths from the recipe — the tail decay length is a detector
   property (2026: UDET ~8, LDET ~30-37) that no width retry can
   compensate for. Deliberately unset today: every accepted 2026 fit
   reaches its detector's beta from the default start on its own.

NO blend or tied-peak fitting exists anywhere in the ladder (AS group
ruling 2026-08-10): every peak is fitted individually and free,
because data fits must use the same fit function as the future
simulation fits, where every peak resolves and is fitted individually.

## The quality check (`fit_recipes.py::fit_is_good`)

Every attempt must pass ALL of, in order:

1. the fit converged;
2. every varied parameter has an uncertainty, and none is exactly 0
   (a zero uncertainty is a collapsed covariance, not precision);
3. no fitted width is narrower than 2 ADC bins (a real peak never is;
   narrower "peaks" are spikes riding another structure) and no width
   is known to better than 0.1% of its value ("impossibly precise" —
   the other face of degenerate covariance; genuine fits bottom out
   near 0.8%);
4. centroid and width errors are within the recipe thresholds
   (CE 5%/50%, Auger 25%/50% — the Auger window is low-statistics and
   honest centroid errors run larger);
5. reduced chi2 <= 10;
6. the peak-spacing check: the fitted peaks must sit where the known
   line energies place them relative to each other. Fits with >= 3
   peaks are checked gain-independently (a line through the two
   anchor peaks predicts every other peak's position; each may be off
   by at most 0.35 of the smallest neighbouring gap); 2-peak fits use
   the pixel's own relation and the pair's predicted separation. This
   is the protection that keeps wrong-source spectra (Cd/Sn/Ce pixels
   fitted as Bi) from ever storing a fit.

## When everything fails

Nothing is stored — and in the database pipeline any previously stored
fit with the same (output, label) is removed rather than left stale.
The pixel gets a figure (raw windowed spectrum, predicted line
positions, best rejected attempt dashed on top, with the rejection
reason) and a row in `fit_failures_summary.csv` (pixels that passed
the statistics gate yet failed everything — the interesting failures)
plus, with `--failures-detail`, the per-run detail CSV including every
skip. Failure review is by eye, from those figures — that is the
designed workflow, not an afterthought.
