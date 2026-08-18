# Fitting spectra in a Jupyter notebook

How to run the spectrum-fitting machinery interactively — your own
windows, your own starting parameters, single peaks, recipe variants —
and how to compare any of it against the production retry ladder.

The engine was built as importable functions with **no database side
effects**: nothing you fit in a notebook is stored, replaced, or
deleted. The fit MODEL (`calibrationnet/fit_functions.py`) is frozen —
these tools only vary its INPUTS, exactly like the production ladder
(`docs/fit_retry_ladder.md`).

## Setup: get the data a fit sees

```python
import numpy as np
import matplotlib.pyplot as plt
import calibrationnet.fit_functions as ff
from calibrationnet.fit_recipes import RECIPES, SCOUT_ANCHORS, fit_is_good
from calibrationnet.fitting import (run_recipe, fit_seeded, gain_scout,
                                    pixel_relation)
from calibrationnet.queries import raw_energies, spectrum

data = raw_energies(9469, 61, segment=7)    # the fit input: raw energies
edges, counts = spectrum(9469, 61, segment=7)   # the histogram, for plots
```

`raw_energies` returns the exact array every production fit was given;
all fitting functions histogram it internally with the standard 1-ADC
bins over (0, 4500).

## Level 1 — one fit, your inputs, no ladder

`ff.get_fit(data, lo, hi, peak_finder, n_peaks, widths)` runs the peak
finder on your window and fits once. This is what
`scripts/benchmark_fits.py` calls.

```python
recipe = RECIPES["Bi-207"][0]                       # ce-6peak, for its settings
result = ff.get_fit(data, 1200, 3400,
                    recipe["peak_finder"], 6, recipe["widths"])
print(result.redchi, result.success)
result.params.pretty_print()                        # values + errors

x = np.arange(1200, 3400)
plt.stairs(counts[1200:3400], edges[1200:3400 + 1])
plt.plot(x, ff.fit_model(result.params, x))
```

Change the window, the finder settings (an 8-tuple: height, threshold,
distance, prominence, width, wlen, rel_height, plateau_size), the peak
count, or the starting widths freely — every argument is yours.

NOTE: get_fit RAISES (a KeyError like 'amp1') when the peak finder
finds NOTHING to seed a fit with — production logs this as "fit not
started". Some perfectly good pixels are like that (their peaks only
resolve via the ladder's fill-in/rescue rungs — e.g. 9469 s33 p53):
for those, use gentler finder settings, or level 2 below.

## Level 2 — your exact starting centroids (single peaks too)

`fit_seeded` bypasses the peak finder entirely: each peak starts where
YOU say, amplitudes read off the smoothed histogram there.

```python
# fit ONE peak in a narrow window, seeded by hand:
result = fit_seeded(data, (2850, 3000), 1, seeds=[2917],
                    widths={"sig1": 4.5}, tag="notebook")

# or all six, custom starts and widths:
result = fit_seeded(data, (1200, 3400), 6,
                    seeds=[1395, 1619, 1657, 2917, 3140, 3178],
                    widths={f"sig{i}": 5 for i in range(1, 7)},
                    tag="notebook", beta_start=9)
```

It returns the lmfit result (or None with the reason printed — e.g. a
seed outside the window). `beta_start` sets the tail-length starting
value, the detector property (2026: UDET ~8, LDET ~35).

Starting values for experiments are one query away:
`fit_parameters(run, pixel, segment=...)` gives every stored
parameter of the production fit, so you can perturb from a known-good
solution and watch what moves.

## Level 3 — the full production ladder, for comparison

`run_recipe` is the exact sequence production runs (all passes and
rungs, the quality gate, everything — see docs/fit_retry_ladder.md):

```python
# assemble the inputs the drivers assemble:
scout = gain_scout(data, SCOUT_ANCHORS["Bi-207"])
scout = 1.0 if abs(scout - 1.0) <= 0.05 else scout
relation = pixel_relation(data, SCOUT_ANCHORS["Bi-207"])

from calibrationnet.db import get_session
from calibrationnet.queries import line_energies
with get_session() as s:
    groups = line_energies(s, "Bi-207")             # NNDC prediction energies
prediction = (groups["CE"], relation, 1.0, "two-anchor")

result, bounds, config = run_recipe(data, recipe, scout_ratio=scout,
                                    prediction=prediction)
print(config["attempt"], config["window"], result.redchi)
```

Every attempt's rejection reason prints as it runs — the same log
production writes. `result` is None if everything failed (then
`config` holds the attempt count and closest miss).

To test a RECIPE VARIANT without touching `fit_recipes.py`, copy and
override:

```python
variant = dict(recipe, bounds=(1300, 3200))          # e.g. narrower window
result, bounds, config = run_recipe(data, variant, scout_ratio=scout,
                                    prediction=prediction)
```

## The production quality gate, on any result

```python
ok, reason = fit_is_good(result, recipe)             # or your variant
```

Runs every production check (convergence, uncertainty health, width
consistency, chi2, spacing when a prediction is supplied:
`fit_is_good(result, recipe, prediction)`), so you can see exactly
which gate a hand-made fit would fail — or verify that a variant
would have been accepted.

## Comparing against what production stored

- `fit_parameters(run, pixel, ...)` — stored values/errors + chi2.
- `stored_fit_curve(run, pixel, ...)` — the stored model evaluated
  over its window, ready to overlay.
- `peak_table(run, pixel, ...)` — extracted per-peak numbers with
  the matched decay lines.

A typical experiment: fit your variant at level 1 or 2, then overlay
its curve, the stored curve, and the data on one axis; compare
parameters against `fit_parameters` and judge both with `fit_is_good`.

## Ground rules (the same ones production lives by)

- The fit model is frozen: if an experiment seems to need a MODEL
  change, that is a `benchmark_fits.py` conversation, not a notebook
  tweak (`docs/pipeline_roadmap.md` has the policy).
- Notebook results are throwaway by design. Anything worth keeping
  becomes a recipe/threshold ruling in `fit_recipes.py` — through the
  usual evidence-and-ruling process — and a rerun of the campaigns.
- Prediction energies are NNDC (`decay_energies.csv`) only; the Jin
  simulation values are calibration targets and never feed fits.
