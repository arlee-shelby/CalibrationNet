# Repo cleanup for public release — the charter

**Working mode (AS ruling 2026-08-20): AS makes the edits; the
assistant explains, answers syntax questions, and verifies safety.**
Goals: (1) readability, (2) AS understands every line, (3) remove
AS-specific development comments, (4) ready the repo for public use.
Development is COMPLETE and signed off (docs/development_plan.md,
"2026-08-20 CLOSING STATE") — every edit in this phase must be
BEHAVIOR-NEUTRAL unless explicitly decided otherwise.

**"Behavior-neutral" means:** the program does exactly the same
thing after the edit — same results, same files, same database rows,
byte-identical outputs. Comments, docstrings, blank lines, and
variable RENAMES (applied consistently everywhere) are neutral;
anything that changes a value, a condition, an argument default, an
order of operations, or an output format is NOT — that is a behavior
change and follows the escape hatch below.

## The per-file loop

1. **AS picks the file** — any order is fine; the loop is
   self-contained per file. The one caution: files that form a tight
   cluster (a rename in one ripples into its importers) are best
   done in the same sitting — the dependency column shows the
   cluster.
2. Check its "imported/used by" column — BUT treat the table as an
   index, not the truth: before certifying any rename or signature
   change, the assistant re-greps the LIVE imports/usages of the
   touched names across the repo (the table can go stale as cleanup
   progresses). Regenerate the whole table any time by asking the
   session — it is derived purely from the import statements.
3. AS edits (on the `cleanup` branch, never `main`). This phase is
   interactive, not one-shot (lesson from run.py, the first file):
   AS asks questions about constructs and conventions while editing,
   the assistant answers (and logs Python/library explanations to
   docs/python_notes.md), and findings that surface go to
   docs/cleanup_findings.md. The assistant does NOT start the diff
   review until AS explicitly says they are done editing the file —
   partial-state reviews waste effort and confuse both sides.
4. When AS declares the file done, verify, scaled to the risk class:
   - all classes: `git diff` reviewed together — the assistant reads
     the diff and confirms it is behavior-neutral (comments, names,
     formatting) or flags exactly what changed semantically;
     `python -m py_compile <file>`. The diff review ALSO checks for
     KNOWLEDGE LOSS: any deleted comment carrying non-obvious
     understanding (a why, a constraint, a lesson) must be relocated
     to its durable home (see "Where the lore lives"), never just
     dropped.
   - **engine** (calibrationnet/*.py): additionally
     `python scripts/benchmark_fits.py --check-only`, and for
     fitting.py/fit_recipes.py also `--reference-pixels`
     (needs the GT tunnel; pulls must stay 0.000).
   - **driver** (scripts/): additionally one smoke run on a reference
     pixel (e.g. `fit_spectra.py --run 8622 --pixels 60 --no-plot`,
     or the script's own cheapest invocation).
   - **schema** (models/): comments/docstrings ONLY — any column or
     constraint change is a migration, i.e. development, not cleanup.
5. CROSS-REFERENCE check (added 2026-08-27): before ticking, note any
   reference from the edited file to a file NOT yet cleaned (README,
   docs/*, other modules — e.g. "see README section X"). Record the
   dependency in the file's cleanup_findings.md entry, so when the
   referenced file is later edited, every recorded referrer gets
   re-checked (does the section still exist? same name? same
   content?). Same in reverse: when cleaning a file, grep for who
   references IT and re-check those mentions.
6. Tick the file off below (edit this document), commit with a
   `cleanup:` prefixed message, move on.

## Hard rules

- `calibrationnet/fit_functions.py` and `fit_functions_reference.py`
  are FROZEN — never edited, byte-identical, md5-checked by
  `benchmark_fits.py --check-only`. Not even comments.
- `alembic/versions/*` are history — never edited.
- Comment policy for public release: comments explain what the code
  cannot say itself (physics constraints, units, conventions).
  Date-stamped "AS ruling" development notes move to
  docs/development_plan.md if worth keeping, else delete. Docstrings
  stay complete — they are the public documentation.
- If an edit turns out to need behavior change: STOP, record it as a
  decision in **docs/cleanup_findings.md** (per-file log of everything
  the cleanup turns up — AS ruling 2026-08-24: development_plan.md is
  closed and stays closed), then do it with the full engine
  verification — that is development, not cleanup.

## Where the lore lives (the development nuance is NOT only in
## old chat sessions — each arc has a durable home)

- The complete development timeline, every ruling with its evidence,
  every found-and-fixed issue, ops lessons, closing state:
  **docs/development_plan.md** (closed as of 2026-08-20; anything
  worth keeping from deleted comments moves to the matching topic doc
  below, or to cleanup_findings.md if it belongs nowhere else).
- Everything FOUND during cleanup — latent bugs, stale docs,
  escape-hatch decisions with their evidence and verification —
  per file: **docs/cleanup_findings.md**.
- The retry ladder — passes, rungs, gates, quality check, why:
  **docs/fit_retry_ladder.md**.
- Storage semantics, label registry, freeze protections, the
  redevelopment ritual: **docs/fit_storage.md**.
- Position planning method (+ the shared-trend saga: plan item 11):
  **docs/position_planning.md** and development_plan.md.
- Source assignment design (joint method, pools, anchors, claims):
  the module docstrings in calibrationnet/source_assignment.py +
  scripts/assign_sources.py, and the assistant's persistent memory.
- Notebook analysis how-to: **docs/notebook_fitting.md**.
- Original architecture: **docs/pipeline_roadmap.md**.

If during cleanup something important seems to live NOWHERE durable,
that is a gap: stop and write it into the right document above
before deleting anything. And the long development session itself
remains consultable — it can be resumed to ask "why did we do X",
even after cleanup sessions have started.

## Session handoff

Start each cleanup session with: "Read docs/cleanup_plan.md; we
are cleaning <file>." (AS chooses the file.) The assistant's
persistent memory carries the project rulings; this file carries the
cleanup state. Nothing else needs re-explaining.

Session scope (AS + assistant, 2026-08-24): one session per
DIRECTORY/CLUSTER, not per file — the files in a directory share
conventions (models/ especially), and the shared context makes later
files in the batch go faster and stay consistent. Start a fresh
session when switching directories, or sooner if the session gets
sluggish or unfocused.

## Known items to address during cleanup

**`recipe_isotope` backfill (decide when cleaning
scripts/extract_adc_peaks.py).** The config key `recipe_isotope` was
added with gate-only fitting (2026-08-18); the bulk Fall 2025
campaign fits (2026-08-13/14) predate it — 1377 of 1505 stored fits
lack it. Extraction falls back to the pixel's assigned source; a
pixel with neither (10 pixels DB-wide, e.g. 8622 p80) prints
"skipped (fits record no isotope and no source is assigned)".
Verified benign 2026-08-24: every missing-isotope fit is FROZEN
(peaks referenced by a jin2026a calibration) and none has zero
peaks — the skip changes nothing. Options:

1. RECOMMENDED — backfill `recipe_isotope: "Bi-207"` on the 1377 old
   configs (a fact, not an invention: ce-6peak/auger-2peak ARE the
   Bi-207 recipes; every pre-gate-only fit used them). One-time
   UPDATE script; afterwards every fit is self-describing, the
   source-assignment fallback in extract_adc_peaks.py becomes dead
   code to delete, and the message can never recur.
2. Cosmetic only — reorder extraction so the frozen check precedes
   isotope resolution ("kept (frozen)" instead of the skip); the
   fallback stays.

Either way the redevelopment ritual is unaffected (it re-FITS, which
writes modern configs).

**Drop the dormant `is_current` column — ADDRESSED 2026-08-27:
dropped in full (migration aff8f130ae93 + all four code sites; the
stale README/repo_layout docs in item 3 below remain for the docs
prose pass). See cleanup_findings.md, calibration.py entry.
Original item kept for history:** History: `is_current` + a partial
unique index originally enforced one calibration-of-record per
(run_pixel, type), with demotion on every store. The 2026-08-20
label ruling replaced that model entirely (identity = trap output +
type + label; labels never interact; NO calibration-of-record). The
acutely-broken part was the INDEX (UniqueViolation from
insert-before-delete flush ordering), so the minimal mid-campaign
fix dropped only the index (migration 6ed9910381f5) and declared
the column dormant — calibrate.py writes True unconditionally, so
every row is True and the column carries no meaning. Removal was
DEFERRED (live-DB ALTER + touching readers during the freeze), not
rejected. Verified live usage as of 2026-08-26 (re-grep before
acting — this list goes stale):

1. `calibrationnet/queries.py` — one function still exposes a
   `current_only` parameter filtering `.where(Calibration.
   is_current)` (a no-op today; dead semantics as API). The earlier
   "current_only removed" sweep missed it.
2. `scripts/low_gain_report.py` (~line 88-91) — REAL WART, fix
   regardless of the column: selects a Calibration by
   trap_filter_output_id + is_current and takes `.first()` with NO
   label or type filter. Under the label scheme a trap output holds
   jin2026a linear + quadratic + jin2026a-ce-only rows, all True, so
   `cal_gain` comes from an arbitrary row. Filter explicitly by
   (label='jin2026a', calibration_type='linear').
3. Stale docs describing the OLD semantics as live: README (two
   spots: "unique index guarantees at most one is_current", the
   `--no-current` mention) and docs/repo_layout.md ("is_current per
   (run_pixel, type) lands on..."). docs/pipeline_roadmap.md also
   mentions it but is historical — leave it. Fix in the docs prose
   pass at the latest.

RECOMMENDED: drop the column properly — one drop-column migration on
a QUIET database (lesson: DDL hangs behind open fitter
transactions), remove the model field + the `current_only` parameter
(re-grep callers first), fix low_gain_report per (2), update the
docs per (3), and delete the `is_current=True` line in calibrate.py.
Keeping it dormant instead is acceptable but still requires (1)-(3),
so dropping costs only the migration on top. A public repo with an
always-true "meaningless" column invites confusion (this item exists
because AS had to ask what it was for).

**Rename `calibrationnet/calibration.py` -> `calibration_fit.py`
(do when cleaning the calibrationnet/ ROOT cluster — AS decision
2026-08-27).** Two files share the name: the root module (calibration
fit math — `fit_calibration`, `plot_calibration`; its docstring
already says "Calibration fit math") and `models/calibration.py`
(the Calibration/CalibrationPoint ORM entities). Python never
confuses them (full dotted paths differ), but humans do — identical
editor tabs, ambiguous references in notes. KEEP `models/
calibration.py`: the models directory is one-file-per-entity
(adc_peak.py, run_pixel.py, ...) and five sibling files import it as
`.calibration`; renaming it would break that directory's own
convention. RENAME the root module. This is a plain
behavior-neutral rename under the charter (applied consistently),
NOT an escape-hatch item. Touch list as of 2026-08-27 (re-grep
`from calibrationnet.calibration` before acting — it can go stale):

1. `git mv calibrationnet/calibration.py
   calibrationnet/calibration_fit.py` (git mv so history follows).
2. Its only two importers: `scripts/calibrate.py` (~line 52) and
   `scripts/offline/calibrate.py` (~line 32) — both import
   `fit_calibration, plot_calibration`.
3. Doc mentions of the ROOT module: docs/repo_layout.md — the
   `calibrationnet/` row of the directory table AND the "three code
   layers" section (two mentions there).
4. While at it: docs/python_notes.md line ~146 says "calibration.py"
   meaning the MODELS one — clarify to "models/calibration.py"
   (that ambiguity is the reason this item exists).

Verify: py_compile both touched scripts + the renamed module, then
the calibrate.py smoke run per the driver class (engine checks too —
root cluster: `benchmark_fits.py --check-only`).

**Line length during cleanup (AS ruling 2026-08-27): NOT enforced.**
Long lines are fine and are not to be flagged in diff reviews; the
79/88-character question is deferred entirely to the linter decision
below, after cleanup.

**Infrastructure identifiers (AS ruling 2026-08-27): minimize where
they appear.** No secrets live in the repo (.env is gitignored,
.env.example is all placeholders), and hostnames are not credentials
— but for public release the concrete identifiers (bl13-replay.
sns.gov, the analysis.sns.gov jump host, and especially the
`nabreplay` service-account name) should appear in as FEW files as
possible. Canonical in-repo home: the header comment of
scripts/with_sc_tunnel.sh (whose mechanics are already host-agnostic
— SSH alias + env overrides), duplicated on the Nab wiki. Everywhere
else — module docstrings, error hints — point at
scripts/with_sc_tunnel.sh instead of spelling out the ssh command.
Why: (a) one place to maintain; (b) AS may scrub the git history
before release, and files already stripped during cleanup stay
untouched by that scrub. Apply while cleaning each file from
2026-08-27 on; already-known sites: calibrationnet/acquisition/
slow_controls.py (docstring + error hint) and epics_controls.py
(error hint); README/docs get theirs in the prose pass. Personal
usernames and user-specific paths (ashelby*, /pscratch/sd/a/
ashelby/...) are genericized wherever encountered. NOTE: changing an
error-message string is an OUTPUT change, i.e. not strictly
behavior-neutral — each such edit gets a line in the file's
cleanup_findings.md entry (trivially safe, but recorded). Before
release: ask the Nab/ORNL contact whether publishing the tunnel
recipe (nabreplay in particular) even in the one canonical file is
acceptable, and decide the git-history question (scrub vs squash vs
leave) explicitly.

**Definition order (AS ruling 2026-08-28): callees above callers.**
Within a module, a function is DEFINED ABOVE the functions that call
it (helpers first, entry points last) — AS reads bottom-up, not
"newspaper order". Python allows either (names resolve at call time;
see docs/python_notes.md "Function definition order"), so reordering
is behavior-neutral — with the one real constraint that module-level
EXECUTED code (constants, queries) must stay below everything it
references at import time. Procedure: the assistant checks definition
order in the pre-edit "things to note about this file" summary for
every file, and re-checks it in the diff review. models/ files
audited 2026-08-28: no violations (class-only files).

**Trailing whitespace (AS ruling 2026-08-27): ALWAYS removed.**
Unlike line length, trailing whitespace on any line of a cleaned
file is fixed as part of cleaning it (it is invisible in editors,
churns future diffs, and any later formatter would strip it anyway).
The assistant checks for it in every diff review
(`grep -n '[[:space:]]$' <file>`) — a stray trailing space never
blocks a tick, it just gets removed. FROZEN files excepted, as
always.

**Linter/formatter decision (after cleanup, before public release).**
The repo has no linter configured. Decide whether to add one (e.g.
Ruff for lint, optionally Black for formatting) with a config checked
into the repo, so line-length/whitespace/import hygiene is enforced
automatically for outside contributors instead of by hand. If yes:
pick the line-length limit first (the cleanup has been wrapping at
~79), run it once over the whole repo as its own commit, and note
that the two FROZEN fit_functions files must be EXCLUDED from any
auto-formatting (md5-checked byte-identical). See docs/python_notes.md
"Linters" for what these tools do.

**`number_subruns` = lastsubrun + 1 — ADDRESSED 2026-08-27: premise
verified against the raw archive (run 8622: 34 files, indices 0..33,
stored number_subruns 34 — lastsubrun IS 0-indexed; see
cleanup_findings.md, slow_controls.py entry). No change needed.
Original item kept for history: (check when cleaning
calibrationnet/acquisition/slow_controls.py).** The run-ingestion query
stores `lastsubrun + 1 AS number_subruns` on the premise that
lastsubrun is 0-indexed. Verify the premise when cleaning that file —
e.g. count the actual Run<r>_<subrun>.h5 files for a couple of runs
and confirm the count equals the stored number_subruns.

## File inventory — tick when cleaned ([ ] -> [x])

### calibrationnet/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/__init__.py` | (nothing imports it — leaf) | engine |   |
| `calibrationnet/calibration.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/run_pixel.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py, scripts/calibrate.py, scripts/offline/calibrate.py | engine |   |
| `calibrationnet/db.py` | calibrationnet/source_assignment.py, calibrationnet/queries.py, scripts/apply_trap_filter.py, scripts/assign_sources.py, scripts/benchmark_fits.py, scripts/calibrate.py, scripts/extract_adc_peaks.py, scripts/fit_spectra.py, scripts/ingest_board_channels.py, scripts/ingest_filter_output.py, scripts/ingest_run.py, scripts/low_gain_report.py, scripts/optimal_positions.py, scripts/pending_segments.py, scripts/plot_stored_fits.py, scripts/process_run.py, scripts/seed_decay_energies.py, scripts/seed_pixels.py, scripts/seed_source_installations.py, scripts/seed_sources.py, scripts/show_hitmap.py | engine | x |
| `calibrationnet/fit_functions.py` | calibrationnet/fitting.py, calibrationnet/queries.py, scripts/benchmark_fits.py | FROZEN |   |
| `calibrationnet/fit_functions_reference.py` | scripts/benchmark_fits.py | FROZEN |   |
| `calibrationnet/fit_recipes.py` | calibrationnet/fitting.py, calibrationnet/queries.py, scripts/benchmark_fits.py, scripts/fit_spectra.py, scripts/low_gain_report.py, scripts/offline/calibrate.py, scripts/offline/fit_spectra.py | engine |   |
| `calibrationnet/fitting.py` | scripts/fit_spectra.py, scripts/offline/fit_spectra.py | engine |   |
| `calibrationnet/geometry.py` | calibrationnet/hitmap.py, calibrationnet/source_assignment.py, calibrationnet/positions.py, scripts/assign_sources.py, scripts/optimal_positions.py | engine |   |
| `calibrationnet/hitmap.py` | scripts/offline/show_hitmap.py, scripts/show_hitmap.py | engine |   |
| `calibrationnet/positions.py` | calibrationnet/acquisition/run_metadata.py, calibrationnet/source_assignment.py, scripts/optimal_positions.py | engine |   |
| `calibrationnet/queries.py` | scripts/fit_spectra.py | engine |   |
| `calibrationnet/source_assignment.py` | scripts/assign_sources.py, scripts/optimal_positions.py | engine |   |

### calibrationnet/models/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/models/__init__.py` | (nothing imports it — leaf) | schema | x |
| `calibrationnet/models/adc_peak.py` | calibrationnet/models/calibration.py, calibrationnet/models/source.py, calibrationnet/models/spectrum_fit.py | schema | x |
| `calibrationnet/models/base.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/calibration.py, calibrationnet/models/pixel.py, calibrationnet/models/run.py, calibrationnet/models/run_pixel.py, calibrationnet/models/run_segment.py, calibrationnet/models/source.py, calibrationnet/models/spectrum_fit.py, calibrationnet/models/trap_filter_output.py | schema | x |
| `calibrationnet/models/calibration.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/run_pixel.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py, scripts/calibrate.py, scripts/offline/calibrate.py | schema | x |
| `calibrationnet/models/covariance.py` | calibrationnet/models/calibration.py, calibrationnet/models/spectrum_fit.py | schema | x |
| `calibrationnet/models/pixel.py` | calibrationnet/models/run_pixel.py, scripts/optimal_positions.py | schema | x |
| `calibrationnet/models/run.py` | calibrationnet/models/run_segment.py | schema | x |
| `calibrationnet/models/run_pixel.py` | calibrationnet/models/calibration.py, calibrationnet/models/pixel.py, calibrationnet/models/run.py, calibrationnet/models/run_segment.py, calibrationnet/models/source.py, calibrationnet/models/trap_filter_output.py | schema | x |
| `calibrationnet/models/run_segment.py` | calibrationnet/models/run.py, calibrationnet/models/run_pixel.py | schema | x |
| `calibrationnet/models/source.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/calibration.py, calibrationnet/models/run_pixel.py | schema | x |
| `calibrationnet/models/spectrum_fit.py` | calibrationnet/models/adc_peak.py, calibrationnet/models/trap_filter_output.py | schema | x |
| `calibrationnet/models/trap_filter_output.py` | calibrationnet/models/calibration.py, calibrationnet/models/run_pixel.py, calibrationnet/models/spectrum_fit.py | schema | x |

### calibrationnet/acquisition/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `calibrationnet/acquisition/__init__.py` | (nothing imports it — leaf) | engine | x |
| `calibrationnet/acquisition/board_channels.py` | scripts/apply_trap_filter.py, scripts/ingest_board_channels.py | engine | x |
| `calibrationnet/acquisition/run_metadata.py` | scripts/ingest_run.py, scripts/offline/export_segments.py | engine | x |
| `calibrationnet/acquisition/epics_controls.py` | calibrationnet/acquisition/run_metadata.py | engine | x |
| `calibrationnet/acquisition/slow_controls.py` | calibrationnet/acquisition/run_metadata.py, scripts/offline/export_segments.py | engine | x |
| `calibrationnet/acquisition/trap_filter.py` | scripts/apply_trap_filter.py, scripts/ingest_filter_output.py, scripts/offline/fit_spectra.py, scripts/offline/show_hitmap.py, scripts/offline/show_spectra.py, scripts/pending_segments.py | engine | x |
| `calibrationnet/acquisition/waveforms.py` | scripts/apply_trap_filter.py, scripts/offline/trap_filter.py | engine | x |
| `calibrationnet/acquisition/electronics_mapping.py` | scripts/seed_pixels.py | engine | x |

### scripts/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `scripts/apply_trap_filter.py` | (nothing imports it — leaf) | driver |   |
| `scripts/assign_sources.py` | (nothing imports it — leaf) | driver |   |
| `scripts/benchmark_fits.py` | (nothing imports it — leaf) | driver |   |
| `scripts/calibrate.py` | (nothing imports it — leaf) | driver |   |
| `scripts/extract_adc_peaks.py` | (nothing imports it — leaf) | driver |   |
| `scripts/extract_bc_maps.py` | scripts/ingest_board_channels.py | driver |   |
| `scripts/fit_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/ingest_board_channels.py` | scripts/apply_trap_filter.py | driver |   |
| `scripts/ingest_filter_output.py` | (nothing imports it — leaf) | driver |   |
| `scripts/ingest_run.py` | (nothing imports it — leaf) | driver |   |
| `scripts/low_gain_report.py` | (nothing imports it — leaf) | driver |   |
| `scripts/optimal_positions.py` | (nothing imports it — leaf) | driver |   |
| `scripts/pending_segments.py` | (nothing imports it — leaf) | driver |   |
| `scripts/plot_stored_fits.py` | (nothing imports it — leaf) | driver |   |
| `scripts/process_run.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_decay_energies.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_pixels.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_source_installations.py` | (nothing imports it — leaf) | driver |   |
| `scripts/seed_sources.py` | (nothing imports it — leaf) | driver |   |
| `scripts/show_hitmap.py` | (nothing imports it — leaf) | driver |   |
| `scripts/apply_trap_filter.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/calibrate.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/fit_spectra.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/ingest_filter_outputs.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/process_run.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/setup_env.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/submit_fit_spectra.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/submit_trap_filter.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/with_sc_tunnel.sh` | (shell — called by AS on clusters) | driver |   |

### scripts/offline/

| file | imported/used by | risk class | done |
|---|---|---|---|
| `scripts/offline/README.md` | (docs — prose) | driver |   |
| `scripts/offline/calibrate.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/export_segments.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/fit_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/show_hitmap.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/show_spectra.py` | (nothing imports it — leaf) | driver |   |
| `scripts/offline/trap_filter.py` | scripts/apply_trap_filter.py, scripts/ingest_filter_output.py, scripts/offline/fit_spectra.py, scripts/offline/show_hitmap.py, scripts/offline/show_spectra.py, scripts/pending_segments.py | driver |   |
| `scripts/offline/submit_fit_spectra_nersc.sh` | (shell — called by AS on clusters) | driver |   |
| `scripts/offline/submit_trap_filter_nersc.sh` | (shell — called by AS on clusters) | driver |   |

### docs/ (prose pass at the end: consistency, remove AS-specific
### references, add a public-facing README)
