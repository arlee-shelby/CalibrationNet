# Source assignment

Which physical source sat over which pixel, for every run segment —
`run_pixels.source_id`, the fact the whole analysis chain keys on
(fit recipes come from the pixel's isotope; calibrations pair its peaks
with that source's keV values). Driven by `scripts/assign_sources.py`
with the machinery in `calibrationnet/pipeline/source_assignment.py`.

## How a placement is derived

1. **Counts, not spectra.** Everything runs on waveform counts per
   pixel (`array_length` of the stored trap filter energies — no energy
   arrays are pulled).
2. **Baselines** — a pixel's source-free rate: the median of its counts
   across all segments of one (holder, convention) pool. A source only
   visits a pixel in a few segments, so the median ignores it. Baselines
   are per-pool on purpose: bias/threshold epochs differ.
3. **Excess** = counts / own baseline. Above ~2 means "a source is
   probably here", whatever the pixel's intrinsic rate — this also
   neutralizes hot or noisy pixels.
4. **The rigid frame.** The holder's slots have fixed offsets relative
   to a reference slot, fitted from the anchor run's verified pixels
   (`calibrationnet/positions.py: ANCHORS` — pixel sets identified BY
   EYE; entries marked expected-region are weaker). For every segment,
   the frame translation that best explains the excess under ALL slots
   at once is located; a trend (readback → frame position) is fitted
   across all segments of the pool and the frames relocated with that
   tighter prior (two rounds).
5. **Cluster-level claims.** A source centers over 1–3 pixels, so a
   claim is a CLUSTER: the peak pixel plus its immediate ring — every
   member gets the same source. Never judge a placement by a single
   pixel; the peak wandering within the cluster between runs (dead or
   fluctuating channels, e.g. the pixel-53 connection) is normal.

## The review workflow

```bash
python scripts/assign_sources.py            # derive; writes the review CSV
# ... optionally edit source_assignment_review.csv by hand ...
python scripts/assign_sources.py --apply    # apply the reviewed rows
```

`source_assignment_review.csv` (TRACKED in git — its diffs are the
record of what any re-assignment changed) has one row per (run,
segment, detector, slot): the predicted landing (pred_x/pred_y), the
observed peak pixel and its snap distance, evidence columns
(peak_excess, peak_counts, n_support), the claimed cluster (pixels),
and a `flag`:

| flag | meaning on --apply |
|---|---|
| *(empty)* | confident placement — applied |
| `CHECK` | ambiguous — SKIPPED (those pixels keep no source until reviewed) |
| `OK` | human-reviewed — applied |
| `REDO` + edited peak_pixel | recompute the cluster around your pixel, then apply |

`--apply` refuses to regenerate over a CSV containing manual edits
unless `--force`. `scripts/process_run.py` runs derive + apply
automatically, which applies only the non-CHECK rows.

## Pools, holders, fields — read this before trusting a re-assignment

Placements pool per **(holder, convention)** — the tray geometry is a
property of the physical holder, and anchors are keyed the same way.
The epochs on record:

| epoch | holder | convention | field (main/udet) |
|---|---|---|---|
| fall 2025 (runs ≤ 8865) | 5-slot | legacy-units | 137 A |
| 2026-07-24 .. 07-30 (9326–9378) | 6-slot | inches-2026 | 110 A |
| 9402 (2026-07-31) | 6-slot | inches-2026 | **137 A** |

**Known limitation:** the pool key does NOT include the magnet field,
but the field sets the readback→frame mapping (measured: the horizontal
scale changed ~5% and the upper-detector shear vanished going
110 A → 137 A). The inches-2026 pool therefore currently mixes two
field epochs in one baseline/trend set. Counts evidence usually
dominates single placements, but until assignment gains per-field
separation (like the position planner's `--runs`): **after processing
runs at a new field setting, diff the review CSV and check the CHECK
rows by hand.**

## Relationship to position planning

`scripts/optimal_positions.py` uses the same machinery (baselines,
excess, frame location, trend) plus a data-driven refinement of the
anchor slot offsets. Assignment still uses the raw anchor offsets —
its cluster-level claims don't need mm precision — but the planner's
refinement should migrate here once validated further
(docs/position_planning.md).
