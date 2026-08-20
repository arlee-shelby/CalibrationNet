# How fit results are stored

Two tables hold fit results — **spectrum_fits** (peak fits of a trap
filter output's spectrum) and **calibrations** (ADC→keV fits) — and both
follow the same storage pattern. This page explains the pattern once,
with a worked example using real numbers from run 8622, pixel 60.

## Uncertainty convention: `scale_covar=False`, always

**Every fit stored in this database runs lmfit with
`scale_covar=False`.** This is NOT lmfit's default: by default lmfit
multiplies the covariance matrix (and therefore every stderr) by the
reduced chi-square, silently "fixing" over/under-dispersed fits. We
never store rescaled uncertainties — what goes in the database is the
raw weighted-least-squares covariance, so whether, when, and how to
scale is always the analyst's decision at analysis time, never baked in
at storage time. (Rescaling after the fact is trivial: multiply the
covariance by `reduced_chi2` — both are stored; undoing a baked-in
rescale would require re-fitting.)

This applies to the spectrum fits (`do_fit` in
calibrationnet/fit_functions.py — one of the frozen physics functions)
and to the calibration fits (scripts/calibrate.py). Any future fit that
writes to this database must follow the same convention.

## Units

Calibration coefficients: keV = constant + linear·ADC (+ quadratic·ADC²)
— **constant in keV, linear (the gain) in keV/ADC, quadratic in
keV/ADC²**; each error shares its coefficient's units. Spectrum-fit
centroids/sigmas are in ADC histogram bins; kev_peaks energies in keV;
decay-line intensities in percent.

## The pattern

| kind | where | examples |
|---|---|---|
| Things you'll query/filter on | dedicated columns | `label`, `fit_range_low/high`, `chi2`, `ndf`, `reduced_chi2`, `success`, coefficients |
| Variable-size fit results | JSONB columns | `pars`, `par_errors`, `var_names`, `covariance` |
| Inputs *without* a dedicated column | `config` (JSONB) | peak-finder parameters, initial width guesses, weighting choices |
| Correlations | **not stored** | derived on demand by `.correlations()` |

**Convention:** anything that has a dedicated column (the fit bounds
`fit_range_low`/`fit_range_high`, `n_peaks`, the calibration
coefficients) is stored **there and only there** — it is *not* repeated
inside `config`. `config` holds only the remaining inputs.

## Worked example: storing a spectrum fit

The usual 6-peak conversion-electron fit:

```python
import calibrationnet.fit_functions as f
from calibrationnet.models import SpectrumFit

peak_finder_parameters = (5, None, 20, 15, 1, None, 0.5, None)
initial_peak_width_guess = {'sig1': 3, 'sig2': 3, 'sig3': 3,
                            'sig4': 5, 'sig5': 5, 'sig6': 5}

results = f.get_fit(data, 1200, 3300, peak_finder_parameters, 6,
                    initial_peak_width_guess, plot=False)

fit = SpectrumFit.from_lmfit(
    results,
    trap_filter_output=tfo,
    label="ce-6peak",
    fit_range=(1200, 3300),          # -> fit_range_low / fit_range_high columns
    config={                          # only the inputs WITHOUT a column:
        "peak_finder_parameters": list(peak_finder_parameters),
        "initial_peak_width_guess": initial_peak_width_guess,
    },
)
session.add(fit)
```

`from_lmfit` copies everything else straight off the lmfit
`MinimizerResult`. For this fit the row holds:

- **`label`** = `"ce-6peak"` — which of the output's fits this is. One
  trap filter output takes several fits (the six CE peaks over one ADC
  window, the Auger peaks over another), each its own row.
- **`fit_range_low` / `fit_range_high`** = `1200` / `3300` — the fitted
  ADC window, as ordinary queryable columns.
- **`n_peaks`** = `6`.
- **`chi2` / `ndf` / `reduced_chi2`** = `results.chisqr` /
  `results.nfree` / `results.redchi`.
- **`success`** = `results.success` — lmfit's True/False convergence
  flag. False means the minimizer gave up and the values/errors in the
  row can't be trusted. Stored so bad fits can be filtered with a query:
  `WHERE success`.
- **`pars` / `par_errors`** — `{name: value}` / `{name: stderr}` for
  **all 34** parameters, including fixed ones (`num_peaks` is in `pars`
  with value 6.0 and no error).
- **`var_names`** — the **33 varied** parameters, in lmfit's order:
  `['slope', 'intercept', 'beta', 'amp1', 'cen1', 'sig1', ...]`.
  `num_peaks` is *not* in it, because it was fixed. This list exists for
  two reasons `pars` can't cover: it records which parameters were
  actually varied, and — the important one — it is the **row/column
  labels of the covariance matrix**, which is a bare grid of numbers
  with no labels of its own. `var_names[29] == 'cen6'` is the only thing
  that tells you what row 29 of the covariance means.
- **`covariance`** — `results.covar` as a 33×33 nested list, ordered
  exactly by `var_names`.
- **`config`** — the inputs above that have no dedicated column, so
  the fit can be reproduced exactly. Production fits (fit_spectra.py)
  record more keys there: `init` (`"find_peaks"` or a predicted-start
  rescue mode), `attempt` (which retry won, e.g. `"recipe"` or
  `"prominence=7, widths: measured"` — the quality-retry ladder,
  roadmap 4.3), the actual `initial_peak_width_guess` values that
  attempt used, and `scout_ratio` (the window scale actually fitted).

### Why `config` matters

This fit is sensitive to its inputs. With `upper_bound = 3200` the cen6
centroid error came out **±25556**; with `3300` it is **±0.49**. Same
data, same label — the *only* difference between those two rows is the
input. Without recording the inputs you could neither reproduce a stored
fit nor explain why two attempts disagree.

### Correlations: derived, never stored

There is **no correlations column**. A correlation is pure arithmetic on
the covariance:

```
corr(a, b) = cov(a, b) / sqrt(cov(a, a) * cov(b, b))
```

which is exactly how lmfit computes `.correl`. Storing correlations next
to the covariance would be the same information twice, with the risk of
the two copies disagreeing. Instead both models inherit
`correlations()` from `CovarianceMixin`
(`calibrationnet/models/covariance.py`):

```python
fit.correlations("cen6")          # {'sig6': -0.7432746973, 'amp6': ..., ...}
fit.correlations("cen6")["sig6"]  # -0.7432746973
fit.correlations()                # the full matrix as {name: {other: corr}}
```

Checked against lmfit on this exact fit:
`results.params['cen6'].correl['sig6']` = **−0.7432746973** and the
derived value from the stored covariance = **−0.7432746973** — identical
to every digit.

## Reading a fit back

```python
from sqlalchemy import select
from calibrationnet.models import SpectrumFit

fit = session.execute(
    select(SpectrumFit).where(SpectrumFit.label == "ce-6peak", ...)
).scalars().first()

fit.pars["cen6"], fit.par_errors["cen6"]   # value and stderr
fit.correlations("cen6")                   # same as lmfit's .correl

import numpy as np
cov = np.array(fit.covariance)             # rows/cols labeled by fit.var_names
i = fit.var_names.index("cen6")
cen6_variance = cov[i, i]
```

## Calibrations: the same pattern, smaller

A calibration is a 2-parameter (linear) or 3-parameter (quadratic) fit,
so the coefficients are small and fixed in number and get **dedicated
columns**: `constant_term ± constant_error`, `linear_term ±
linear_error`, `quadratic_term ± quadratic_error`. Everything else
mirrors spectrum_fits:

- `label` — which calibration attempt (e.g. `"nndc-2026"`,
  `"sim-corrected"`).
- `chi2`, `ndf`, `reduced_chi2`, `success` — same meanings.
- `var_names` — `['constant', 'linear']` or
  `['constant', 'linear', 'quadratic']`, labeling the 2×2 or 3×3
  `covariance`.
- `config` — inputs without a column (e.g. weighting choices).
- `correlations()` — same derived method; a linear calibration has one
  correlation, a quadratic has three.

```python
cal.correlations("constant")["linear"]   # e.g. 0.3794733192
```

A calibration deliberately has **no `spectrum_fit_id`**: its points come
from *several* fits of the same output (CE window + Auger window). Which
fits contributed is recorded per point, through
`calibration_points → adc_peak → spectrum_fit`; the assumed keV values
through `calibration_points → kev_peak`.

## Calibration label registry (bookkeeping ruling, AS 2026-08-20)

A calibration's identity is **(trap filter output, type, label)** —
the output pins run/segment/pixel/trap setting, `type` is
linear/quadratic, and the **label names the target family**. Labels
are permanent, coexisting families: storing one label NEVER touches
another; re-running the SAME label replaces in place (a correction,
not a version). There is no cross-label "current" — which label an
analysis uses is the analyst's explicit choice at query time
(`calibration_summary(label=...)`; default "jin2026a"). The
`is_current` column is dormant (always true) and carries no meaning.

| label              | targets & method                              |
|--------------------|-----------------------------------------------|
| `jin2026a`         | Jin 2026a simulated detected energies, per detector + per-run HV shift; UNWEIGHTED least squares. THE production family. |
| `jin2026a-ce-only` | Same targets, CE points only — the 2026-08-19 8-peak-vs-CE-only comparison (12 pixels). Diagnostic; deletable. |
| (future) `jin2026b`| Jin's frozen-fit-function refit of the simulation. New family, coexists with jin2026a — the before/after test of the target values. |
| (future) `nndc`    | NNDC physical energies, for comparison only — never the production calibration (wrong frame). |

Every new label gets a row here when it is first stored.

## Changing things later: the development ritual

Routine operation is purely additive (new runs: fit -> extract ->
calibrate; comparisons: a NEW label). The protections in force:
the database refuses to delete any fit's peaks that a calibration
references; the fit driver SKIPS such pixels on re-runs ("kept —
frozen"); extraction refuses to replace referenced peaks. Nothing
routine can disturb a calibrated result.

When fits themselves must change (recipe/threshold development —
target changes do NOT require this, they are just a new label):

1. Decide the scope (runs / segments / pixels / recipes).
2. Optionally export the affected calibrations to CSV for the record
   (they will be deleted; the database keeps no versions by ruling).
3. DELETE the affected calibrations — this is the deliberate
   unfreeze, and the only destructive step.
4. Refit the scope (the re-sweep replaces the now-unfrozen fits).
5. Re-extract (adc_peaks), recalibrate (same labels).

This is exactly the 2026-08 development sequence, formalized. It is
loud, ordered, and entirely recoverable up to step 3's export.
