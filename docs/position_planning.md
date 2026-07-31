# Source position planning

`scripts/optimal_positions.py` answers the question the rastered scan
runs were taken to answer: **where should the stage go so that every
pixel gets a well-centered source?** It produces a short list of stage
positions — a *position plan* — that an automation script can step
through, plus per-detector coverage maps.

## How it works

1. **Learn the mapping.** The script fits the same readback → frame
   position trend that source assignment fits from the scan data (a
   linear model per detector: hex-grid x, y as functions of the linear
   and horizontal readbacks). It improves automatically as more scanned
   segments are ingested — re-run it after every ingest.
2. **Stay inside proven range.** The allowed readback range is exactly
   the range the scan data actually used (e.g. horizontal 1.70–3.70 for
   the 5-slot legacy scans). Anything outside is not *known* to be
   reachable, so the plan never proposes it — see the what-if mode below
   for exploring beyond it.
3. **Refine the tray geometry against the data.** The anchor run gives
   slot offsets by snapping each verified source to its pixel's CENTER,
   but a verified source can really sit up to ~4.5 mm off that center —
   so raw inter-slot spacings carry up to a pixel of quantization error
   (run 9327 exposed exactly this: the predicted R1C2–R1C3 spacing was
   ~1.2 hex too large, hiding coverable pixels). The planner therefore
   compares every slot's predicted landing with the measured count
   centroid in every scanned segment and folds the median residual back
   into the offsets (`refine_slot_offsets`, two rounds, relocating the
   frames in between). Corrections found for the 6-slot tray reached
   ~1.5 hex (8 mm) on the lower detector. `--no-refine` disables this
   for comparison. (Source *assignment* still uses the raw anchor
   offsets — its cluster-level claims are insensitive to this — but
   could adopt the refinement after more validation.)
4. **Grade every candidate position.** A grid (default step 0.05) over
   the allowed range is scored: at each position, each holder **slot**
   is projected onto both detectors and its predicted offset from the
   nearest pixel center is computed. Slots, never sources: sources get
   swapped between runs; the tray geometry is what persists.
5. **Pick the fewest positions.** A greedy set cover in two passes:
   - pass 1: fewest positions that *well-center* every pixel that can
     be well-centered anywhere in the allowed range;
   - pass 2: extra positions so every remaining pixel at least gets the
     source *inside* it.
   One position typically serves ~6 pixels per detector at once, on
   both detectors simultaneously.

## The centering metric

Everything is graded by the **predicted offset**: the distance between
where the trend puts the slot center and the pixel center. Pixel
center-to-corner is 5.2 mm; the boundary to a neighboring pixel is
~4.5 mm out. Two thresholds:

| threshold | default | meaning |
|---|---|---|
| `--tolerance-mm` | 2.6 | "well centered" — comfortably inside the pixel |
| `--boundary-mm` | 4.5 | "covered" — the source center is inside the pixel at all |

A pixel whose best achievable offset is between the two (like lower
pixel 1027 under the 5-slot tray, best ≈ 3.7 mm) still gets a position —
it looked "centered-ish" in the real data and is perfectly usable. Only
pixels whose best achievable offset exceeds `--boundary-mm` everywhere
in the allowed range go uncovered, and the summary prints that best
offset for each one, so a white pixel on the map is always explained.

The metric can be validated against data: at a plan position, the
fraction of a cluster's counts landing in the target pixel should fall
monotonically with predicted offset. Disagreement is evidence about the
trend fit, not noise to ignore.

## Why a pixel can be unreachable: band geometry

The tray's slot rows sit at fixed vertical spacings (~30–36 mm between
row levels for the 6-slot tray). Moving the horizontal axis sweeps each
row through a vertical band of

    band height = |d(y)/d(horizontal)| x (horizontal range) + 2 x boundary

When the scanned horizontal range is narrow, the bands can be narrower
than the row spacing, leaving horizontal *stripes* no allowed position
reaches. Where exactly the stripes fall is extremely sensitive to the
inter-slot spacings — which is why the offset refinement above matters:
with raw anchor offsets the first 6-slot plans wrongly declared upper
31/53/78/100 and lower 1062/1086/1097 unreachable; run 9327's hit map
(source visibly on the 100/101 boundary at an in-range position)
exposed the error, and after refinement all of those are covered within
the scanned ±0.5 inch. The remaining uncovered pixels are marginal
(best 4.7–5.2 mm, a fraction of a mm past the boundary) or true
edge/corner pixels limited by the linear range. If a plan's uncovered
list ever contradicts what a hit map shows at an in-range position,
treat it as evidence about the trend/offsets and investigate — that is
precisely how the 9327 discrepancy was caught.

## What-if mode: planning the next scan

To find out what a wider scan would buy *before* taking it:

    python scripts/optimal_positions.py --assume-horizontal -0.6 0.6

pretends the given range is reachable, extrapolating the trend. Outputs
get a `_whatif` suffix and are for **scan planning only — never feed
them to the automation** (the trend is unverified out there). With the
refined offsets, the in-range plan already covers every pixel except a
few marginal ones (e.g. lower 1106 at best 4.9 mm) and the extreme
corners — a modest horizontal extension (to roughly ±0.6–0.75 inch, if
the hardware allows) would pick those up and pin the trend down over
the wider range; after ingesting such a scan, the normal plan inherits
the range automatically.

## Outputs

All named `optimal_positions_plan_<holder>_<convention>*`:

| file | contents |
|---|---|
| `.csv` | one row per position/detector/slot → pixel, with `predicted_offset_mm` and `well_centered` |
| `_positions.csv` | just the positions to visit (with per-detector pixel counts) — the automation list |
| `_summary.txt` | everything the run printed: trend, plan, coverage, unreachable pixels with best offsets |
| `_upper.png`, `_lower.png` | coverage maps: each pixel labeled with the position (P#) that best centers it, colored by predicted offset (bright = centered), white = uncovered |

## Options

| option | default | effect |
|---|---|---|
| `--holder` | current installation | which tray to plan for (the default queries `source_installations` for `removed_on IS NULL`) |
| `--label` | nabpy-standard | which trap filter outputs to count |
| `--tolerance-mm` | 2.6 | "well centered" bar; tighter → more positions, cleaner data |
| `--boundary-mm` | 4.5 | coverage bar; beyond it the source sits in a neighboring pixel |
| `--step` | 0.05 | search grid step (each axis' own units) |
| `--max-positions` | none | truncate the plan; positions are ordered by coverage gain, so this keeps the most valuable dwells |
| `--assume-linear/-horizontal` | none | what-if range for scan planning (`_whatif` outputs) |
| `--no-refine` | off | use raw anchor-derived slot offsets (skip the data-driven refinement) |
