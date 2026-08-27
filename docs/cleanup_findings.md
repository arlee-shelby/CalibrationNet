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

## calibrationnet/models/trap_filter_output.py — 2026-08-25 — done

### Stale comments: CSV-era workflow (fixed)

The label/source_file comments described the 2025/NERSC-era workflow
(persistent CSV scan files on disk, curated ingest). Current workflow
(apply_trap_filter.py) computes from raw hdf5 and the CSV is transient
staging, deleted after successful ingest. Comments rewritten to match.

### source_file column: keep (AS ruling 2026-08-25)

Considered dropping it (names files that mostly no longer exist).
Kept because: (1) it is the idempotence/dedup check for the
failed-ingest rescue path (`WHERE source_file = ...`); (2) it is the
only record of the waveform type token ("singles"); (3) cost ~zero.

### FUTURE development item: waveform_type column

Pulser waveforms (different from singles) are planned eventually.
When that campaign is designed, trap_filter_outputs needs the wave
type as a real column (currently only inside source_file), and the
pulser requirements may demand more (settings columns, energies
semantics). Deliberately DEFERRED (AS + assistant, 2026-08-25): no
current data is wrong, no pulser data exists, and the future design
should drive the schema. Do NOT build speculatively during cleanup.

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

---

## calibrationnet/models/calibration.py — 2026-08-27 — done (code + migration aff8f130ae93 applied and verified)

**Verification (2026-08-27):** py_compile x4 OK; benchmark_fits
--check-only green; zero is_current/current_only references outside
alembic history; migration aff8f130ae93 ran with lock_timeout=5s,
is_current absent from calibrationnet.calibrations; low_gain_report
smoke identical before/after (run 8622, 11 fitted pixels); ORM
roundtrip on 2784 calibrations OK. low_gain_report now takes
--cal-label (default jin2026a) instead of the is_current filter.

**Side observation while verifying:** the database holds a leftover
schema `calibration_test` with an early-design calibrations table
(gain/offset/correlation columns). Untouched by the migration and by
all code (search_path is `calibrationnet`). DB-side cruft, not repo
content — decide someday whether to drop the schema; no urgency.

### Drop dormant `is_current` column (escape hatch, AS ruling 2026-08-27)

Executing the cleanup_plan known item (full history there). Ruling:
full drop, per its RECOMMENDED option. Also fixed on the way, per the
same item: the stale docstring sentence claiming the partial index
still enforces one is_current per (run_pixel, type) (contradicted the
dormancy comment below it), and the low_gain_report.py wart (selected
a calibration by trap output + is_current with `.first()` and no
label/type filter — arbitrary row under the label scheme).

Plan (code first, migration second — the ORM tolerates an extra DB
column, not a missing one):
1. models/calibration.py — remove the is_current field, the dormancy
   comment block, the docstring claim, and is_current from __repr__.
2. queries.py — remove calibrations_for_pixel's `current_only`
   parameter and its no-op filter (no repo callers; notebooks that
   pass current_only= must drop it).
3. scripts/calibrate.py — delete the `is_current=True` line.
4. scripts/low_gain_report.py — filter explicitly by
   (label="jin2026a", calibration_type="linear").
5. Migration (drop column; downgrade restores it nullable=False,
   server_default true) run on a QUIET database (DDL hangs behind
   open fitter transactions).

Verification: py_compile all four; benchmark_fits --check-only;
low_gain_report smoke run before AND after the migration; column
absence confirmed via information_schema.
