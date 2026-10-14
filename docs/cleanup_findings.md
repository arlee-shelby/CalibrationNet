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

---

## calibrationnet/acquisition/trap_filter.py — 2026-09-01 — ESCAPE-HATCH change, PRE-cleaning (development session)

### Behavior change: fall time from the filename (AS decision 2026-09-01)

Found while AS started cleaning: BOTH current writers
(scripts/apply_trap_filter.py and scripts/offline/trap_filter.py)
already encode `_fall{N}` in filter-output filenames, but
parse_filter_filename ignored it and the module docstring claimed
"fall time is not [encoded]" — stale. Consequences before the fix: the
rescue path (scripts/ingest_filter_output.py) required the human to
re-supply the fall time (--falltime defaulted to 1250 SILENTLY — a
recovered non-1250 file would have been mislabeled), and offline
consumers threw the encoded value away. The filename has two live
roles: rescue record in the DB pipeline (files deleted on successful
ingest), PRIMARY metadata in the offline pipeline (files persist).

Changes (development session, before AS's cleaning of the file):

1. parse_filter_filename: additionally captures an optional
   `fall(\d+(?:\.\d+)?)` -> trap_falltime. Absent -> key absent
   (legacy names stay distinguishable; never a silent default).
2. ingest_filter_output(): trap_falltime now Optional[float]=None.
   Resolution: filename value wins when present; caller value allowed
   only to agree (mismatch -> ValueError) or to supply the fall for a
   legacy name; neither present -> ValueError. Validation moved ABOVE
   the segment lookup: all filename-derived checks run before any
   database access.
3. scripts/ingest_filter_output.py: --falltime default None (was
   1250); now needed only for legacy names; docstring + examples
   updated (legacy folder example shows --falltime 1250).
4. Module docstring + parse docstring corrected (three settings
   encoded; legacy caveat). Addendum same day: the stale
   "optimization scan stays on disk" paragraph (module docstring) and
   the matching phrase in scripts/ingest_filter_output.py replaced
   with the live facts — curation-by-label policy + transient-CSV
   lifecycle (deleted on successful ingest; persists only awaiting
   rescue, or in the offline pipeline where files are the record).
   Style/wording cleanup still AS's.

Compatibility: parse result gains a key — all five call sites use
.get()/specific keys, verified unaffected (offline
fit_spectra/show_spectra/show_hitmap, ingest_filter_output.py,
trap_filter.py itself). apply_trap_filter.py passes a falltime that
always matches its own filename -> cross-check passes.

**Verified 2026-09-01:** py_compile (trap_filter.py,
ingest_filter_output.py, apply_trap_filter.py); parser round-trip
(modern, offline, legacy, fractional-value names); LIVE error-path
smoke against the DB via the rescue script — mismatch (fall1250 vs
--falltime 999) and legacy-without---falltime both FAIL loudly with
per-file rollback, nothing stored; benchmark_fits --check-only green.
AS's behavior-neutral cleaning of the file follows this entry.

---

## calibrationnet/acquisition/trap_filter.py — 2026-09-01 — done (cleaning pass; follows the same-day escape-hatch entry above)

### Cleanup review

**Behavior-neutral except one recorded output change:** the
parse-error hint now reads "(expected ex: filter_output_rt100_ft10_
fall1250.csv)" — "e.g." -> "ex:" (AS's house style) and the example
modernized to include _fall. All other prints and error strings
byte-identical. Variable renames applied consistently (parsed ->
parsed_filter_settings, done -> segments_with_filter_outputs,
settings -> filter_settings, per_pixel -> energy_per_pixel, rp ->
run_pixel, and friends); every function's PARAMETER names unchanged,
so keyword callers unaffected. Signatures/calls collapsed to single
lines (formatting only). Definition order was already compliant.

**Docstrings substantially expanded by AS:** module docstring now
carries the transient-CSV lifecycle, the offline-pipeline rationale
(usable during database maintenance, ingest later), and the
curation-by-label policy with its storage rationale (~500-setting
optimization scans; trap_filter_outputs dominates database storage).

**Accepted losses (recorded here as the durable home):**
- The flush comment kept the mechanism (per-pixel flush so each
  INSERT carries one energies array; a single batched INSERT can
  reach hundreds of MB and the server closes the connection
  mid-send; the module does not commit, so per-file all-or-nothing
  is unchanged) but dropped the CONCRETE INCIDENT: run 9371
  segment 5, 409 subruns — the batched statement grew to hundreds
  of MB and the server dropped the connection mid-send
  (2026-08-13/14 campaign). That evidence lives here now.
- The junk-pixel comment kept "pixel 58 (and 1058) has no
  electronics" but dropped the why-the-data-is-junk detail: what the
  replay attributes to pixel 58 is an AGGREGATE OF JUNK BOARD
  CHANNELS — none of it real detector data (the ambiguous multi-BC
  mapping half of the story is in board_channels.py's docstring).

**Verified 2026-09-01:** py_compile; parser round-trip smoke test
(modern name -> all five keys; legacy name -> no falltime/run keys);
benchmark_fits --check-only green; trailing whitespace stripped.

---

## calibrationnet/acquisition/waveforms.py — 2026-09-01 — done

### Cleanup review

**Behavior-neutral throughout.** Public renames, swept to all
callers: subrun_span -> subrun_timespan (no external callers),
find_subruns -> find_segment_subruns (scripts/apply_trap_filter.py
and scripts/offline/trap_filter.py, 2 sites each — the assistant
swept them plus the python_notes.md mentions). _headers ->
_waves_and_headers (module-private). save_filter_output's parameter
per_pixel -> energies_per_pixel (both callers positional). Internal
renames (found -> subruns, stamps -> timestamps, cache ->
subrun_start_timestamps, low/high -> low/high_subrun_index, etc.)
consistent. Prints/errors byte-identical except the find_segment_
subruns ValueError ("this segment's window ends" -> "the target end
time" — recorded output change) and docstrings expanded (notably:
segment_energies now documents the window=None hazard — without a
window all waveforms in the passed subruns are included, i.e. the
caller asserts single-position data).

**Knowledge RELOCATED (AS decision): the lazy-dask rationale moved
from the code comment to docs/cluster_resources.md** ("Why the trap
filter needs so much memory"): filter the whole subrun and mask the
ENERGIES, never the waveforms — boolean-indexing the lazy ~7.6 GB
dask array forces an expensive rechunk; masking energies is free;
only a segment's two edge subruns have anything masked. The doc
previously POINTED at the comment; it now CONTAINS the rationale
(pointer removed). The code comment keeps only the what.
CROSS-REFERENCE: re-check that section when the docs prose pass
reaches cluster_resources.md.

**Accepted losses (durable here):** the binary-search scale
illustration — "a dozen header reads instead of opening all 600-odd
files" (the docstring now says "significantly"; for the record: 2026
rastering runs reach ~900 subruns, e.g. run 9521 = 877, so the
saving is ~10 probes vs hundreds of opens). The one-per-end
over-include rationale was reworded but kept.

**Also fixed during review:** epoch wording (ticks count since the
unix epoch, not "the first unix timestamp"); segment/subrun mix-ups
in the binary-search locals and comments; the stale
"pipeline.trap_filter" reference (folder rename leftover) in
save_filter_output's docstring.

### trap_filter.py addendum (same sitting)

energy_per_pixel -> energies_per_pixel applied consistently to match
waveforms.py's vocabulary; AS's pass missed one site (the pixels
dict comprehension) which would have raised NameError at runtime —
caught in review, fixed, verified by import + py_compile.

**Verified 2026-09-01:** py_compile (both modules + both driver
scripts); all eight waveforms exports import; to_ticks epoch smoke
check; benchmark_fits --check-only green; trailing whitespace
stripped; definition order compliant.

---

## calibrationnet/acquisition/__init__.py — 2026-09-01 — done (re-opened 2026-08-31 for the module inventory, closed today)

Docstring-only file. Re-ticked after AS added the per-module
inventory (one line per module with its data source) — the
package-door map decided alongside the run_metadata rename. The
waveforms line states that module both reads waveforms AND applies
the trap filter (the only nabPy module); the trap_filter line scoped
to CSV ingestion. Verified: py_compile, live import (docstring
present), benchmark green, no trailing whitespace.

---

## calibrationnet/positions.py — 2026-09-03 — ANALYSIS (development session), resolves the 9367 caveat

### Where the INCHES_2026 constants come from (they are measured, not chosen)

`hex_per_linear = 3.35`: slope of frame-x vs linear readback fitted
over run 9370 (linear raster: 19 segments, 31.88->34.61 in — a
2.73-inch lever, 110A field). The 2026-09-03 full-pool refit
reproduces it (a=+3.341): the linear constant was always sound.
`hex_per_horizontal = -3.90`: Delta(frame y)/Delta(horizontal)
between runs 9326 and 9327 (same linear 33.502, horizontals -0.249
vs -0.499) — a two-point, 0.25-inch-lever derivation. Units: the
detector hex-grid coordinates of geometry.py (column spacing 1.5,
row spacing sqrt(3)) per inch of stage readback.

### Did run 9367 settle "field demagnifies vs biased fit"? YES — both, in parts

The deleted development comment asked for a long-horizontal-lever
run; 9367 IS one (horizontal raster: 11 segments sweeping +-0.485 in
at two linear positions, 110A-exb0) — taken, ingested, never
analyzed until now. Method: the assignment pipeline's own
locate_all_frames over ALL pooled data (shared lower-detector
slopes, per the rigid-tray ruling), plus a direct regression of
9367's 11 fitted frames. Results (geometric HEX_PER_INCH = 3.750):

| pool (field) | fitted linear a | fitted horizontal b |
|---|---|---|
| 2026 6-slot 110A-exb0 (9367's) | +3.341 | -4.433 |
| 2026 6-slot 137A-exb2000 | +3.608 | -4.432 |
| 2026 5-slot 137A-exb2000 | +3.642 | -4.653 |
| 2025 legacy 137A-exb-1500 | +4.680 | -1.378 hex/unit (constant: 1.732) |

Direct 9367 regression, lower detector: b = -4.412 hex/in (rms 0.37
hex, 11 frames) — agrees with its pool. The upper-only fit gives
-3.22: the same weak-upper bias the trend saga diagnosed
(development_plan item 11), further confirming share-lower-slopes.

Conclusions: (1) the -3.90 two-point fit WAS biased (~13% low in
magnitude); (2) the field ALSO genuinely rescales, anisotropically
and field-dependently (horizontal ~18% above geometric at
exb0/exb2000 but ~20% below at 2025's exb-1500; linear the opposite
direction) — which is WHY assignment pools never mix field epochs.

Operational impact: NONE on stored data. The convention constants
are only the round-1 prior; locate_all_frames round 2 refits the
true trend from each pool's own data and relocates every frame, so
all placements used the correct ~-4.4 slopes. Prior error over a
+-0.5 in lever is ~0.27 hex, well inside the locate window. DECISION
(AS + dev session 2026-09-03): leave 3.35/-3.90 unchanged
(behavior-neutral cleanup); update the comment from an open question
to this answer, pointing here.

---

## calibrationnet/source_assignment.py — 2026-09-23 — applied, pending AS pass (PILOT of the prepared-draft method)

### Method

First file cleaned by the prepared-draft method (docs/cleanup_plan.md
method v2; glossary = docs/naming_conventions.md, ratified 2026-09-08).
Dev session produced the draft on top of AS's own partial clean (kept
verbatim except the items listed below), verified it in isolation,
AS ruled on a 9-item checklist (all accepted 2026-09-23), dev session
applied it with a guard (each real file unchanged since verification).
AS's own editing pass on the real file follows; the cleanup tick comes
after THAT pass, not after this step.

### What changed (real-tree line numbers as applied)

Ratified renames (naming_conventions.md): `_evidence` ->
`_signal_strength_at` (l.139), `snap_to_cluster`/`snap_all` ->
`match_to_cluster` (l.548)/`match_all_slots` (l.580), `assign_from_preds`
-> `assign_sources_from_predictions` (l.610), `predict_prior` ->
`predict_frame_position` (l.533), `check_anchor_installation` ->
`check_verified_installation` (l.362); k/keys -> segment_id(s);
spec/pool -> conditions/conditions_of (l.409); excess(es) ->
ratios/ratios_by_segment; prior -> predicted_position; det/rp spelled
out; cx,cy -> x_coefficients/y_coefficients (l.524). Companion:
positions.py `ANCHORS` -> `VERIFIED_TRAY_POSITIONS` (l.71), `anchor_for`
-> `verified_position_for` (l.117), `anchor_pixel_center` ->
`verified_slot_center` (l.160) — "anchor" no longer appears in either
file. Scripts: exactly two identifiers at their call sites
(assign_sources.py l.42-43, 119, 188; optimal_positions.py l.64, 452);
the scripts' own vocabulary is untouched (their own passes).

AS rulings beyond pure renames (all accepted):
1. nested private `median` in refine_slot_offsets deleted; module
   `statistics.median` used (l.265-266) — identical, odd+even tested.
2. `predict_frame_position` drops the never-used `offsets` parameter
   (def l.533, sole call l.417).
3. three function-local imports hoisted to module top (l.36-38;
   `RunSegment` added to the models import).
4. positions.py companion applied together (mutually dependent imports
   — MUST be one commit with source_assignment.py).
5. `details` -> `detectors` in compute_baselines (l.105) — closes the
   deferred "details vs segment_counts" ruling with a third name.
6. inner `format` -> `format_value` (l.90): shadowed the built-in.
7. ILLUMINATED_* constants comment reworded to current names (l.51-53).
8. module docstring wording "many real sources" -> "real sources" (l.25).
9. "pool across field configurations" -> "grouped across" (l.88).

### Verification (all on 2026-09-23, real tree after applying)

py_compile x4; equivalence probe (cleanup_review/equivalence_probe.py:
5 segments x 2 detectors x 127 pixels synthetic data through every
function) vs the COMMITTED ORIGINAL at HEAD — 7,355 result lines
identical; full pipeline (scripts/assign_sources.py, whole database)
before vs after — review CSV byte-identical, 5,536 placements over 507
segments; benchmark_fits --check-only green; no retired vocabulary left
(grep anchor|snap|spec|pool|excess|prior|evidence|testif).

### Side findings

- The tracked/live `source_assignment_review.csv` was STALE (built from
  469 segments; the database now has 507) — regenerate when convenient.
- Test-harness lesson: an editable install makes `import calibrationnet`
  resolve to the real repo unless PYTHONPATH forces the test tree, and
  `.env` is read from the cwd — both bit the pipeline test twice before
  the setup was right. Recorded so the next file's verification starts
  from the working recipe (PYTHONPATH=<tree>, .env exported, cwd=<tree>).

## calibrationnet/source_assignment.py — 2026-10-01 — v2: data-flow names (style rules 17/18), applied, pending AS pass

### Why a second draft

AS's editing pass on the applied pilot stalled on "where does this come
from": the glossary had fixed word choice but not flow. The ratios dict
was under four names — `relative_counts` made it without naming its
return, the scripts held it as `excesses`/`excess`, six functions took
it as `ratios`/`ratios_by_segment`, and the three matching functions
took it as `counts`/`detector_counts` while the script actually passed
ratios into them (`match_to_cluster`'s docstring even said
"count-weighted"). Hence two new style rules in naming_conventions.md
(17: one quantity, one name along the whole chain, bare name = per-pixel
dict for one detector, nested = `_by_<key>`; 18: every `return` returns a
named variable) and step 0 of method v2 in cleanup_plan.md (data-flow
table first, ratified before drafting, then written into the module
docstring). AS ruled the nine quantity names and the named-return rule
in chat (rulings record 2026-10-01 in naming_conventions.md), then
accepted the 14 consequential items of the review checklist
(cleanup_review/README.md section B) with "proceed with everything".

### What changed (real-tree line numbers as applied; applied == draft)

Module docstring point 4, the data-flow table (l.27-72): every quantity,
its shape, who makes it, who consumes it. Renames: `fetch_all_counts` ->
returns `counts_by_segment` (l.108-128); `compute_baselines
(counts_by_segment)` -> `baselines_by_detector` (l.149-168);
`relative_counts` -> `count_ratios(counts, baselines)` -> `ratios`
(l.171-176; reverses AS's own earlier [A] name so the function names its
product); `illuminated_neighbor_number` returns `illuminated_neighbors`
(l.179-193); `_signal_strength_at(slot_position, ratios,
pixel_positions)` -> `signal_strength` (l.196-220); `slot_offsets` uses
`verified_pixels_by_slot` (l.236); `refine_slot_offsets` returns
`corrected_offsets_by_detector, refine_report` (l.276-337);
`locate_frame` -> `best_frame`, `candidate_frame`, `slot_position` as a
named local (l.340-369); `installation_for` returns `sources_by_slot,
holder, installation` (l.372-402); `check_verified_installation` uses
`verified_position`, `verified_segment` (l.405-434); `locate_all_frames
(..., offsets_by_conditions, ...)` with `first_round_frames`,
`frames_by_detector`, inner `offsets` (l.437-500); `share_trend_slopes
(trend, frames_by_detector, ...)` -> `corrected_trend` (l.503-534);
`fit_position_trend(frames_by_detector, ...)` (l.537-565);
`predict_trend`/`predict_frame_position` return `predicted_position`
(l.568-587); `match_to_cluster(predicted_position, ratios, ...)` ->
`peak_pixel, centroid, match_distance`, `ratio_sum`, "ratio-weighted"
(l.593-619); `match_all_slots(predicted_positions, ratios, ...)` ->
`matches_by_slot` with `unclaimed_ratios`, `unmatched_slots`,
`candidate_match` (l.622-649); `assign_sources_from_predictions(ratios,
detector, source_labels_by_slot, predicted_positions, ...)` ->
`placements` (l.652-690). AS's own compute_baselines loops: `detectors`
-> `counts_by_detector`, `pixels` -> `counts` (l.158-160); AS's `counts`
loop variable in the ratio comprehension back to singular `count`
(l.175, the parameter is now `counts`). `predicted_position` keeps both
of its uses (frame's reference slot before locating; every slot after),
stated in the table.

Scripts, boundary names only, produced by the line-pinned edit list
cleanup_review/make_scripts_draft_v2.py (same line numbers before and
after): assign_sources.py 58 lines — `counts` -> `counts_by_segment`,
`key_positions` -> `readback_positions`, `slot_maps` ->
`sources_by_segment`, `baselines` -> `baselines_by_conditions`,
`excesses`/`excess` -> `ratios_by_segment`/`ratios`, `offsets` ->
`offsets_by_conditions` (+ `slot_offset` -> `offsets`), `labels` ->
`source_labels_by_slot`, `tx, ty` -> `frame_x, frame_y`, `preds`/`pred`
-> `predicted_positions`/`predicted_position`, `label_` ->
`source_label`, `peak` -> `peak_pixel`, `dist` -> `match_distance`,
`peak_excess` -> `peak_ratio`, `n_support` -> `illuminated_neighbors`,
the +1000 `offset` -> `lower_detector_pixel_shift` (l.190, 203, 211,
262, 289), `cmap` -> `counts` (l.261-280); optimal_positions.py 54 lines
— the same boundary names plus `offsets_by_det` -> `offsets_by_detector`,
`offsets_for_locate` -> `offsets_by_conditions`, `raw` ->
`unrefined_offsets_by_detector`, `slot_map` -> `sources_by_slot`,
`cx, cy` -> `x_coefficients, y_coefficients` (l.102-104, 539-542),
`ox, oy` -> `offset_x, offset_y` (l.109-112), `candidate_coverage`'s
parameter `offsets` -> `offsets_by_detector` (l.92, 109). CSV headers
(`peak_excess`, `n_support`, `snap_dist`, `pred_x/y`) unchanged
(interfaces). The scripts' own vocabulary (k, det, spec, pool_of, meta,
base, dx/dy, their docstrings' "excess counts"/"anchor-derived") waits
for their own passes.

### Verification (2026-10-01)

Draft in isolation: py_compile x3; equivalence probe working tree vs
draft — 7,355 lines identical; full pipeline against the live database
(511 segments, 4 without a recorded position skipped as before), working
tree and draft run side by side — review CSV byte-identical, 5,537 lines
(5,536 placements over 507 segments, 1,871 flagged CHECK both sides);
benchmark_fits --check-only green; inventory 20 -> 20 functions; every
`return` audited (named, literal, or unchanged input); retired-name
grep clean apart from CSV headers and the scripts' own prose.
Apply, guarded: the two script drafts regenerated byte-identically from
the live sources immediately before the copy (content guard), pre-apply
copies kept; after the copy, probe on the REAL tree vs the committed
HEAD c71f64a (old names, via the probe's fallbacks) and vs the pre-apply
copy — both identical; benchmark green; imports of both scripts
resolve. The confirmatory pipeline re-run on the real tree could NOT be
done: the database connection was refused (the DB compute-node job had
ended); since the applied bytes equal the draft bytes (cmp), the
side-by-side run above covers the same code — repeat it when the tunnel
is back (`python scripts/assign_sources.py --path <scratch>.csv`, cmp
against the pre-apply CSV).

### Side findings

- **Two weightings for one centroid (behavior, AS to decide).**
  `apply_from_csv` (assign_sources.py l.261-279) recomputes every
  applied row's membership around the CSV's peak with a COUNT-weighted
  centroid, while `generate_review`'s placements come from
  `match_to_cluster` with a RATIO-weighted centroid (module l.615-617).
  So the CSV's `pixels` column can differ from what `--apply` claims
  (its docstring does say the column is ignored). Left as is; a
  cleanup must not change it — if AS wants one weighting, it is an
  escape-hatch change with its own entry.
- Harness lesson 2: a draft overlay directory without `__init__.py`
  works only because the editable install is a meta-path finder
  (`__editable___calibrationnet_0_1_0_finder.py`), so the overlay's
  namespace portion supplies `source_assignment`/`positions` and the
  finder supplies the rest — but ONLY when the script directory and
  PYTHONPATH precede the real package. `python -c` from the repo root
  puts the cwd first and silently imports the real package; check
  `module.__file__` in the same path context as the real run.
- Guard oddity, unresolved: the two real scripts showed an mtime of
  11:49:58 (after the drafts were built at 11:43) although their content
  was unchanged (byte-identical regeneration, and a diff against HEAD
  showing only the pilot's renamed lines); probably an unchanged save
  from VSCode. The content guards, not the mtime, were what admitted
  the apply.
- docs/source_assignment.md l.18 still says "Excess = counts / own
  baseline" (AS's doc; glossary word is "ratio") — for the docs pass.
