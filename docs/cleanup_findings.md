# Cleanup findings — issues discovered during the public-release cleanup

One entry per file (in the order files were cleaned). This is the
durable record for anything the cleanup pass turns up that is more
than a comment edit: latent bugs, stale documentation, behavior-change
decisions taken through the charter's escape hatch (see
docs/cleanup_plan.md, "Hard rules"). Development is CLOSED
(docs/development_plan.md, "2026-08-20 CLOSING STATE"); escape-hatch
decisions made during cleanup are recorded HERE, each with its
evidence, ruling, and verification. Pure lore relocated from deleted
comments still goes to its topic home ("Where the lore lives" in the
charter), not here.

Entry format: **file — date — status** (status is one of
`decided, pending application` / `applied, pending verification` /
`done`).

---

## calibrationnet/models/run.py — 2026-08-24 — done (applied + verified 2026-08-24)

### Archive-path HV sign flip (behavior change, escape hatch)

**Found:** while reviewing run.py's units comment ("hv in kilovolts
(negative, e.g. -27)"), AS observed that recent runs store hv
POSITIVE. Verified against the DB: of 124 runs with hv, all
instrument-table-era runs (through 8865) store it negative; exactly
the 4 archive-era runs — 9464, 9469, 9470, 9521 — store hv ≈ +27.04.

**Cause:** the motion-control archive path (SETTINGS_CHANNELS in
calibrationnet/pipeline/motion_control.py) stored hv WITHOUT the sign
flip — the archive readback BL13:Nab:UDETHV:voltage reports +27 kV
for a physical -27 kV — silently re-introducing the sign mistake that
was corrected across the runs table on 2026-07-30. The
instrument-table path (slow_controls.py) was always correct
(`AVG(-values[1]/1000)`). Archive biases are NOT affected:
BL13:Nab:*DETBias:SourceVoltage reports negative natively; verified
-300.0 on all archive-era runs.

**Ruling (AS, 2026-08-24):** restore the uniform physical convention
(hv negative) everywhere rather than documenting a mixed convention.

- Code: motion_control.py hv transform `v / 1000.0` -> `-v / 1000.0`,
  plus a comment noting the readback polarity so this cannot regress.
- Data: one-time UPDATE of the 4 positive rows to -hv.
- No calibration is affected: the only sign-sensitive consumer,
  scripts/calibrate.py, already uses `abs(run.hv)` (its "readback
  reports +27 for -27 kV" note was written against exactly this).
  queries.py merely displays hv. Re-ingesting any archive-era run
  after the fix reproduces the negative value, so ingest and stored
  data agree.

**Verification (all passed 2026-08-24):** `py_compile`
motion_control.py OK; `benchmark_fits.py --check-only` — reference
md5 matches, all 7 frozen functions identical, changeable functions
no differences; sign distribution after the UPDATE: 110 negative /
0 positive / 14 zero (was 106/4/14). Own commit (development-style
message, not `cleanup:`), separate from the run.py comment cleanup.

**Follow-up unlocked:** once applied, the "(physical signs as of
2026-07-30)" datestamp in run.py's units comment can be dropped — the
convention is uniformly true again and the correction's history lives
in the motion_control.py comment.

---

## calibrationnet/models/run_segment.py — 2026-08-24 — done

### Dead `notes` column: kept, documented

Found while cleaning: nothing in the codebase writes RunSegment.notes
(the seed scripts' `notes` handling targets the source tables), and
0 of 511 segments have one. Ruling (AS, 2026-08-24): keep the column
as an optional manual-entry field rather than dropping it (a drop
would be a migration = development); documented with a comment in the
file. Also fixed during cleanup: docstring rewritten with position
provenance (2025 manual entry vs 2026 archive readback) — new lore
that previously lived nowhere durable.

---

## calibrationnet/models/pixel.py — 2026-08-24 — done

### Stale docstring: wiring location (fixed)

The class docstring said wiring ("board channel, preamp, FET") is
stored per run on RunPixel, but the preamp/fet columns live on Pixel
itself; only board_channel is per-run. Docstring rewritten to match
reality (and now also records the pixel-0 catch-all exclusion).

### Wiring-history concern: assessed, no action needed

AS raised during cleanup: if the preamp/FET maps ever changed, an
in-place update would lose which wiring old runs had. Assessment
(2026-08-24): safe as designed — (1) nothing computes from preamp/fet
(they are human-facing metadata; processing depends on board_channel,
which IS per-run on RunPixel); (2) history is not actually lost — any
remap means editing data/pixel_wiring.csv and re-running seed_pixels,
and git history preserves every old map; (3) if per-run wiring history
ever became analytically important, the fix is a migration moving
preamp/fet to RunPixel (development, deferred until needed). The
docstring now states the in-place-update + git-history design.
