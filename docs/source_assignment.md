# Source assignment

Which physical source sat over which pixel, for every run segment —
`run_pixels.source_id`, the fact the whole analysis chain keys on
(fit recipes come from the pixel's isotope; calibrations pair its peaks
with that source's keV values). Driven by `scripts/assign_sources.py`
with the machinery in `calibrationnet/acquisition/source_assignment.py`.

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

## Pools, holders, fields

Placements pool per **(installation, holder, convention, field)** —
baselines, frame trends, all of it. The rules, explicitly:

- **What combines:** segments of ONE installation at ONE field
  configuration. Nothing else, ever.
- **What never combines:** anything across installations (a re-mounted
  tray can sit differently — even the same tray, same convention, same
  field), across readback conventions, or across field epochs. The
  fall-2025 5-slot data and the 2026 5-slot reinstall share a physical
  tray and NOTHING else.
- **What carries over:** only the tray's slot GEOMETRY (inter-slot
  offsets, a property of the physical holder) — never its mounting.
  Every installation therefore needs its own eye-verified ANCHOR
  (one hit-map-checked segment, an entry in
  calibrationnet/positions.py), and assignment/planning refuse to run
  a pool whose anchor comes from a different installation.

The installation key is the latest `installed_on` among the rows in
force (touching any source means pulling the tray, so any change to
the installed set starts a new mounting period). History: the holder
entered the baseline key on 2026-08-10, when two trays first shared a
convention and field (the 5-slot reinstall, run 9464, alongside the
6-slot runs 9402+ at inches-2026/137A) — pooled together, their
rasters shifted each other's medians enough to move 524
already-reviewed placements; the installation key replaced that same
day as the general rule, verified to reproduce every pre-existing
pool byte-identically. The field key —
`source_assignment.field_key(run)`, e.g. `137/137A-exb2000` — captures
the magnet currents and ExB voltage (binned at 100 V, so residual
readings of a few volts don't split pools), because the readback→frame
mapping measurably depends on them (110 A → 137 A changed the
horizontal scale ~5% and removed the upper-detector shear). Baselines
and trends never mix field epochs; every review-CSV row carries its
`field` for visibility. The pools on record:

| epoch | installation | holder | convention | field key |
|---|---|---|---|---|
| fall 2025 (runs ≤ 8865) | 2025-10-03 | 5-slot | legacy-units | 137/137A-exb-1500 |
| 2026-07-24 .. 07-30 (9326–9378) | 2026-07-21 | 6-slot | inches-2026 | 110/110A-exb0 |
| 9402–9415 (2026-07-31 →) | 2026-07-21 | 6-slot | inches-2026 | 137/137A-exb2000 |
| 9464+ (2026-08-10 →, 5-slot reinstall; anchor run 9464 seg 30) | 2026-08-04 | 5-slot | inches-2026 | 137/137A-exb2000 |

**Why this matters (validation):** derived field-blind, the anchor run
9326's own placements sat a full pixel column off its eye-verified
pixels (upper R1C2 at 87 instead of 97, etc.); with field-aware pools
they land exactly on the verified pixels, and 9327 snaps to them at
0.07–0.09 radius. Diff `source_assignment_review.csv` (it is tracked)
after any re-derivation to see precisely what moved.

## Relationship to position planning

`scripts/optimal_positions.py` uses the same machinery (baselines,
excess, frame location, trend) plus a data-driven refinement of the
anchor slot offsets. Assignment still uses the raw anchor offsets —
its cluster-level claims don't need mm precision — but the planner's
refinement should migrate here once validated further
(docs/position_planning.md).
