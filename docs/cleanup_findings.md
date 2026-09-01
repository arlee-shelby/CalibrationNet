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
calibrationnet/acquisition/epics_controls.py) stored hv WITHOUT the sign
flip — the archive readback BL13:Nab:UDETHV:voltage reports +27 kV
for a physical -27 kV — silently re-introducing the sign mistake that
was corrected across the runs table on 2026-07-30. The
instrument-table path (slow_controls.py) was always correct
(`AVG(-values[1]/1000)`). Archive biases are NOT affected:
BL13:Nab:*DETBias:SourceVoltage reports negative natively; verified
-300.0 on all archive-era runs.

**Ruling (AS, 2026-08-24):** restore the uniform physical convention
(hv negative) everywhere rather than documenting a mixed convention.

- Code: epics_controls.py hv transform `v / 1000.0` -> `-v / 1000.0`,
  plus a comment noting the readback polarity so this cannot regress.
- Data: one-time UPDATE of the 4 positive rows to -hv.
- No calibration is affected: the only sign-sensitive consumer,
  scripts/calibrate.py, already uses `abs(run.hv)` (its "readback
  reports +27 for -27 kV" note was written against exactly this).
  queries.py merely displays hv. Re-ingesting any archive-era run
  after the fix reproduces the negative value, so ingest and stored
  data agree.

**Verification (all passed 2026-08-24):** `py_compile`
epics_controls.py OK; `benchmark_fits.py --check-only` — reference
md5 matches, all 7 frozen functions identical, changeable functions
no differences; sign distribution after the UPDATE: 110 negative /
0 positive / 14 zero (was 106/4/14). Own commit (development-style
message, not `cleanup:`), separate from the run.py comment cleanup.

**Follow-up unlocked:** once applied, the "(physical signs as of
2026-07-30)" datestamp in run.py's units comment can be dropped — the
convention is uniformly true again and the correction's history lives
in the epics_controls.py comment.

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
remap means editing data/electronics_mapping.csv and re-running seed_pixels,
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

---

## calibrationnet/models/source.py — 2026-08-27 — done (README cross-ref below stays pending)

New lore added during cleanup: EzIsotope provenance; why simulation
corrections are per-source (Mylar/aluminum/carrier thicknesses = loss
corrections); elog slot-convention provenance (10/2025-present).
Knowledge-loss checks: the hv_kv magnitude convention (readback +27 =
-27 kV) was dropped from the model comment by AS ruling — it lives
durably in seed_decay_energies.py (writer) and calibrate.py (reader).
The Source Installation History slides provenance lives in
seed_source_installations.py.

### Cross-reference dependency: README slot convention

SourceInstallation's docstring points to README "Source frame slot
convention" for the R<row>C<col> / Facing UP orientation rules. The
README has NOT been cleaned yet — when it is (docs prose pass at the
latest), re-check this docstring: section still exists, same name,
conventions unchanged. (This entry prompted the general
cross-reference rule now in cleanup_plan.md, per-file loop step 5.)

---

## calibrationnet/pipeline/ → calibrationnet/acquisition/ — 2026-08-27 — done

### Directory rename (behavior-neutral, applied repo-wide)

**Decision (AS, 2026-08-27):** rename the package directory so the
folder name matches its role — the data-ACQUISITION layer of the
three-code-layers design (rationale: docs/repo_layout.md, "The three
code layers"). This also frees the word "pipeline" to mean only the
run processing chain in prose, removing a long-standing ambiguity.

**Scope applied:** `git mv calibrationnet/pipeline
calibrationnet/acquisition` (history follows); 18 import lines across
13 scripts (incl. scripts/offline/); the live `python -c` probes in
scripts/apply_trap_filter.sh and README.md; path mentions in
calibrationnet/positions.py's docstring and in README.md,
docs/repo_layout.md, docs/cleanup_plan.md, docs/cluster_resources.md,
docs/source_assignment.md, docs/cleanup_findings.md,
docs/python_notes.md. The acquisition/__init__.py docstring was
rewritten by AS in the same pass (it stale-claimed fitting and
calibration would live here). Internal modules use relative imports —
unchanged. pyproject.toml discovers `calibrationnet*` — unchanged.

**Deliberately NOT changed:** docs/development_plan.md line ~533 and
docs/pipeline_roadmap.md (closed/historical documents — the old path
is accurate history). models/ and alembic/ contain no references
(verified by grep).

**Verified 2026-08-27:** repo-wide grep shows zero remaining
`calibrationnet[./]pipeline` outside the historical docs;
`py_compile` clean on every .py under calibrationnet/ and scripts/;
all 9 acquisition modules import cleanly; `benchmark_fits.py
--check-only` integrity OK (frozen md5 match, changeable functions
identical).

---

## calibrationnet/acquisition/source_assignment.py → calibrationnet/source_assignment.py — 2026-08-27 — done

### Module moved to the package root (behavior-neutral, applied repo-wide)

**Decision (AS, 2026-08-27):** source assignment is not data
acquisition — it derives run metadata from data already IN the
database plus positions.py anchors, and touches nothing external
(no slow controls, no h5, no archive). Under the three-layer rule
(docs/repo_layout.md) it belongs in the `calibrationnet/` root, so
the acquisition/ rename made the misplacement visible and the module
moved up.

**Scope applied:** `git mv` (history follows); its own relative
imports dedented one level (`from ..db` → `from .db`, likewise
geometry/models/positions); both importers
(scripts/assign_sources.py, scripts/optimal_positions.py); path
mentions in docs/source_assignment.md, docs/cleanup_plan.md ("Where
the lore lives" + the inventory tables — row relocated from the
acquisition table to the calibrationnet/ table), and
docs/repo_layout.md (layer 1 now lists source assignment with the
rationale; layer 2 dropped "plus the source-assignment
bookkeeping"). models/run_pixel.py's comment says the bare filename
"source_assignment.py" — still accurate, untouched. The module's own
docstring (a durable lore home) is location-agnostic — unchanged.

**Verified 2026-08-27:** py_compile clean repo-wide; both
`calibrationnet.source_assignment` and `calibrationnet.acquisition`
import cleanly (field_key resolves); `benchmark_fits.py
--check-only` integrity OK; grep shows zero remaining
`acquisition[./]source_assignment` references.

---

## calibrationnet/acquisition/slow_controls.py — 2026-08-27 — done

### `number_subruns = lastsubrun + 1` premise VERIFIED (known item, closed)

The run-ingestion query stores `lastsubrun + 1 AS number_subruns` on
the premise that runlog.status's lastsubrun is 0-indexed. Verified
2026-08-27 against the raw archive on GT
(/storage/ideas/is-ajezghani3-0/TempCal): run 8622 has 34
Run8622_*.h5 files with subrun indices 0..33; the DB stores
number_subruns = 34 and slow controls therefore reported
lastsubrun = 33. Count == max index + 1 with a 0 minimum confirms
0-indexing directly. The query and its line comment are correct —
no change needed.

### Cleanup review (file done 2026-08-27)

Docstrings/comments rewritten by AS; verified behavior-neutral except
one sanctioned output change: the tunnel error hint no longer spells
out the ssh command (hosts + service account) and instead points at
scripts/with_sc_tunnel.sh — per the "Infrastructure identifiers"
ruling in cleanup_plan.md. The example SC_DATABASE_URL (with the real
DB account name) left the module docstring; .env.example is the
canonical home for the URL shape.

Local renames lin->linear, horiz->horizontal, lone->lone_linear;
Style B re.search collapsed to one line (formatting only). Verified:
py_compile, imports, benchmark_fits --check-only all green; trailing
whitespace stripped.

Knowledge relocation note: the old query comment's pointer "(see
epics_controls.SETTINGS_CHANNELS), which also finally provides
ldet_ring" was dropped — that fact (the instrument tables never
provided ldet_ring, which is why the query selects udet_ring only)
now lives only in epics_controls.py's SETTINGS_CHANNELS table.
Re-check when cleaning epics_controls.py.

Cross-references recorded: this file points at
scripts/with_sc_tunnel.sh (module docstring + error hint) and
.env.example (module docstring + get_sc_engine error) — re-check
those mentions if either file is renamed/restructured during its own
cleanup.

---

## calibrationnet/acquisition/motion_control.py → epics_controls.py — 2026-08-27 — done (rename only; content cleanup separate)

### Module renamed (behavior-neutral, applied repo-wide)

**Decision (AS, 2026-08-27):** since 2026-07-21 the module reads much
more than the RSIS motion control — the run-level settings
(SETTINGS_CHANNELS: biases, HV, temperatures, leakage) also live in
the Test archive it queries. The archive holds EPICS channels, and
the folder convention names modules after the external system they
read (cf. slow_controls.py), so: epics_controls.py.

**Scope applied:** `git mv`; the one importer
(calibrationnet/acquisition/run_metadata.py — import line + a docstring
mention of MIN_DWELL); docstring/doc path mentions in
calibrationnet/positions.py, README.md:157, docs/cleanup_plan.md
(identifiers ruling + inventory row), docs/cleanup_findings.md (all
entries), docs/python_notes.md. Function names and the
POSITIONS_DATABASE_URL env var unchanged (env-var rename would be a
behavior change). Historical docs (development_plan.md,
pipeline_roadmap.md) untouched as always.

**Verified 2026-08-27:** repo-wide grep zero remaining motion_control
references outside historical docs; py_compile clean repo-wide;
epics_controls + ingest import cleanly; benchmark_fits --check-only
integrity OK.

---

## calibrationnet/acquisition/epics_controls.py — 2026-08-27 — done

### Content cleanup (follows the same-day rename from motion_control.py)

**Behavior changes, both sanctioned/recorded:**
- Tunnel error hint no longer spells out the ssh command; points at
  scripts/with_sc_tunnel.sh (Infrastructure identifiers ruling).
- `position_at()` DELETED as dead code: repo-wide grep (code, docs,
  notebooks) found zero callers; git log shows it appeared in exactly
  one commit (966e737, its introduction) and was never referenced
  since. Its `Optional` import removed with it. Trivially
  recreatable from _LAST_BEFORE_QUERY if ever wanted.

**Behavior-neutral renames (applied consistently, zero external
users of old names):** fetch_position_samples ->
fetch_position_entries (public; only caller is dwell_periods in the
same module), _step_series -> _merge_position_entries, _SAMPLES_QUERY
-> _ENTRIES_QUERY, plus local variable renames.

**Date disambiguation established during review (two events, three
days apart — settings vs positions):** run SETTINGS moved to the Test
archive 2026-07-21 (instrument tables stop; SETTINGS_CHANNELS
comment, slow_controls.py comment); POSITION archiving + the
inches-2026 convention start 2026-07-24 (INCHES_2026_START,
positions.py). Both files now use the right date for the right event.

**Knowledge relocated/accepted losses from the old SETTINGS_CHANNELS
comment (all durably recorded elsewhere):** the sign-mistake history
(+300/+27 corrected across the runs table 2026-07-30; hv transform
negation fixed 2026-08-24 after runs 9464-9521 briefly stored +27) —
see this file's run.py entry above; the LDET leakage magnitudes
anecdote (~36 uA before 2026-07-21, ~0.06 uA after — the reason
leakage stays in MICROAMPS, the legacy Keithley unit). Also dropped
from the constants' comments, preserved here: POSITION_TOLERANCE's
0.02" is safe because readback jitter is thousandths of an inch
(kept in code) AND scan steps are ~0.2" (dropped — the upper bound);
MIN_DWELL's 5 min is safe because stage moves take seconds to a
couple of minutes (dropped — the lower bound) while standard dwells
are ~30 min.

**Verified:** py_compile, module + ingest imports, benchmark_fits
--check-only all green; trailing whitespace stripped (12 lines);
zero old-name leftovers repo-wide.

---

## calibrationnet/acquisition/run_metadata.py — 2026-08-31 — done

### Cleanup review

**Behavior-neutral throughout** — no output or logic changes: the
ValueError text and the "keeping it" warning print are untouched;
all code edits are multi-line-call collapses (formatting only).

**Reordered per the definition-order ruling (2026-08-28):**
derive_segments -> sync_segments -> ingest_run (callees above
callers; entry point last).

**Knowledge relocated:** "positions are not Run columns — they live
on run_segments" moved from the old _RUN_COLUMNS comment into
derive_segments' docstring ("the source position is an attribute of
RunSegment, not of the Run"). The fill-if-missing guarantee ("a
value the legacy tables provided is never overwritten") restored as
a comment on the `is None` check inside ingest_run.

**Accepted losses (recorded here as the durable home):**
- The epoch-branch explanation formerly in derive_segments'
  docstring: runs from 2026-07-24 get ONE SEGMENT PER DWELL from the
  EPICS archive (a rastering run has dozens); earlier runs get a
  single whole-run segment whose position comes only from the
  free-text run description.
- The concrete min_dwell boundary example: RUN 9402's grid dwells
  were themselves ~5 min, sitting exactly on the MIN_DWELL default —
  ingesting it needed a lowered --min-dwell or segments were
  silently dropped. (Run 9464 needed --min-dwell 2; that one is
  still named in scripts/offline/export_segments.py's help text.)
- The old module docstring's enumeration of non-column keys
  (rundescription, errorcode) — the storing contract is documented
  from the other side in slow_controls.fetch_run's docstring.

**Cross-references:** module docstring mentions grafana (lowercase)
and the "scripts" folder; ingest_run's docstring points at
epics_controls.py. Nothing points at not-yet-cleaned files.

**Verified 2026-08-31:** py_compile, imports, benchmark_fits
--check-only green; trailing whitespace stripped; definition order
compliant.

---

## calibrationnet/acquisition/wiring.py → electronics_mapping.py — 2026-08-31 — done

### Module + data file renamed, "wiring" vocabulary retired (behavior-neutral)

**Decision (AS, 2026-08-31):** "wiring" was confusing as a name for
what the data actually is — the pixel -> preamp/FET electronics
mapping. Renamed: the module (wiring.py -> electronics_mapping.py,
git mv), the data file (data/pixel_wiring.csv ->
data/electronics_mapping.csv, git mv), and the loader (load_wiring ->
load_mapping). The word "wiring" was removed from live prose
repo-wide, replaced with mapping/electronics-mapping vocabulary.

**Scope applied:** _DEFAULT_CSV path in the module; the sole importer
scripts/seed_pixels.py (import, call, locals, docstring); README,
docs/repo_layout.md (directory + seed tables, layer prose),
docs/cleanup_plan.md inventory row; docs/cleanup_findings.md live
filename pointers. AS edited the two already-cleaned models files
(comment-only, re-verified): models/pixel.py (4 sites, including two
stale csv filenames) and models/run_pixel.py (1 site). Deliberately
untouched: alembic/versions/134d9680eb84 ("move preamp/fet wiring to
pixels" — migration history, never edited) and the historical
narrative wording inside older cleanup_findings entries (dated
records; only their filename pointers were updated).

### Content pass (same sitting)

Docstring rewritten by AS: provenance now says the map figures came
from the Nab wiki; loader-mirroring explanation kept; pixel-58 and
label-uniqueness facts kept. A comment was added documenting that
parents[2] in _DEFAULT_CSV is the repo root (depth-dependent anchor).
Definition order compliant (constant above its consumer; single
function).

**Verified 2026-08-31:** py_compile; live smoke test load_mapping()
returns 254 pixels with 58/1058 unmapped; benchmark_fits --check-only
green; zero "wiring" left in code outside migration history; trailing
whitespace stripped; models/pixel.py and run_pixel.py re-verified
(comment-only diffs, py_compile OK).

---

## calibrationnet/acquisition/ingest.py → run_metadata.py — 2026-08-31 — done

### Module renamed (behavior-neutral, applied repo-wide)

**Decision (AS, 2026-08-31):** the acquisition package's naming
convention is nouns for modules (the domain/source: slow_controls,
epics_controls, board_channels, waveforms, trap_filter,
electronics_mapping), verbs for scripts (ingest_run,
apply_trap_filter, ...). ingest.py was the one verb-named module,
which made it read as "the generic ingest file" and made
board_channels.py's ingestion look anomalous. Renamed to the noun
run_metadata.py (runs.py rejected: confusably close to
models/run.py; run_settings.py rejected: settings are the smallest
slice of what it handles). Function names unchanged (ingest_run,
derive_segments, sync_segments).

**Scope applied:** `git mv`; both importers (scripts/ingest_run.py,
scripts/offline/export_segments.py); path mentions in
docs/cleanup_plan.md (inventory + dependency columns),
docs/cleanup_findings.md (live pointers), docs/python_notes.md
(entry headings). Historical docs untouched.

**Follow-up in the same decision:** acquisition/__init__.py UNTICKED
— AS is adding a module inventory to the package docstring (each
module with its data source) so the per-source organization is
visible at the package door.

**Verified 2026-08-31:** zero leftover acquisition.ingest references;
repo-wide py_compile; run_metadata imports clean; benchmark_fits
--check-only green.

---

## calibrationnet/acquisition/board_channels.py — 2026-09-01 — done

### Cleanup review

**Behavior-neutral throughout:** variable renames applied
consistently (bc_map -> board_channel_map incl. apply_bc_map's
parameter — both callers pass it positionally, verified; channels ->
board_channels; rp -> run_pixel; unknown -> unknown_pixels); the
ambiguity print and all three ValueError messages byte-identical;
returns unchanged; remaining code edits are formatting collapses.

**Reordered per the definition-order ruling:** clean_bc_pairs ->
read_bc_map -> apply_bc_map -> ingest_board_channels (was:
ingest above apply, a violation).

**Docstrings rewritten by AS; knowledge check:** all facts kept
(pixel-0 catch-all, padding rows, pixel-58 multi-BC ambiguity,
per-run-not-per-position so written to every segment, h5py-only,
any-subrun-works). New content added: why BC maps are per-run while
preamp/FET maps are per-pixel (change frequency), and the
logic-vs-scripts commit split promoted to the module docstring.

**Verified 2026-09-01:** py_compile; import + clean_bc_pairs logic
smoke test (ambiguous and pixel-0 rows dropped as documented, with
the expected report line); benchmark_fits --check-only green;
trailing whitespace stripped; definition order compliant.
