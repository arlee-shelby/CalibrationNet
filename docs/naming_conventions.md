# Naming conventions and translation glossary

**STATUS (2026-09-08): sections 1 and 2 RATIFIED by AS** (annotations
harvested + contested items ruled one-by-one; see "Rulings record" at the
end). Section 3 (per-file appendix) is working guidance, ratified in
context during each file's cleaning pass — the global table always wins
where they overlap. Entries marked **[A]** come from AS's own
already-cleaned files.

How to use this document during cleanup:
- Section 1 is the style every remaining file is rewritten toward.
- Section 2 is the cross-file vocabulary; apply it identically everywhere.
- Section 3 lists each remaining file's own opaque terms (terms already in
  the global table are not repeated there).

---

## 1. The style

1. Spell every name out in full — no single letters, no abbreviations.
   ex: `det` -> `detector`, `rp` -> `run_pixel`, `COL_START` -> `COLUMN_START`.
   Allowed exceptions: math coordinates (`x`, `y`, `x0`, `y0`), a plain
   `enumerate` index `i`, and throwaway letters in tight lambda or
   comprehension scopes (`key=lambda e: e[0]`), but be careful even with lambda expressions, if a spell out makes the code readability better, we should use that.
2. Physics and apparatus words replace CS metaphors, in names and prose.
   ex: `frontier` -> `current_ring`, `cache` -> `subrun_start_timestamps`,
   `pipeline/` -> `acquisition/`, `X_PITCH` -> `HORIZONTAL_OFFSET`,
   `motion_control` -> `epics_controls` (name the real system).
3. Bayesian and statistics jargon is out: "prior" -> "predicted position",
   "primed by" -> "starting from". Name the quantity for what it literally
   is: `excess_map` -> `count_ratios` (a count divided by a baseline).
4. "Illuminated" is the chosen word for a pixel elevated above its own
   baseline by a source. ex: `SUPPORT_EXCESS` -> `ILLUMINATED_RATIO`,
   `support_at` -> `illuminated_neighbor_number`.
5. Collection and status names say what they contain or what state they
   mark. ex: `done` -> `segments_with_filter_outputs`, `missing` ->
   `missing_segments`, `unknown` -> `unknown_pixels`.
6. Clever one-liners are unrolled into explicit for-loops with named
   accumulators, each step preceded by a plain-language comment
   (ex: the nested comprehension in `compute_baselines`).
7. Long lines are fine: wrapped signatures, calls, and comprehensions are
   JOINED onto single lines; never re-wrap to 79 characters (the line-length
   question is deferred to the post-cleanup linter decision).
8. Docstrings open with the concrete deliverable — "Returns {exact nested
   shape}" or "Returns the <physical quantity>" — before any prose; the
   closing triple-quote always sits on its own line.
9. Docstring voice: a physicist explaining a measurement to a colleague.
   Full conversational sentences, physical reasons for every number, units
   and sign conventions stated, domain definitions restated where used
   ("run segments are periods of constant source position"), clarifications
   chained with "i.e.", asides opened with "Note,".
10. Examples are introduced with "ex:", never "e.g.". Identifier and column
    names in prose take double quotes ("label"), never backticks. Isotopes
    are written element-dash-mass (Bi-207, Sn-113), never 207Bi.
11. Comments are lowercase sentence fragments without trailing periods and
    without caps-for-emphasis. No dated attributions or design-ruling
    asides ("AS ruling 2026-08-14") — history lives in git. No hostnames
    or usernames — point at scripts/with_sc_tunnel.sh instead.
12. Module constants get a leading units declaration for the block, and
    each constant a comment giving the physical reasoning and the concrete
    numbers ("1.1 = center-to-corner radius + 10%").
13. Concrete named facts over vague words: real years (2025/2026) instead
    of "older"/"new", the actual instrument or vendor (EPICS, RSIS,
    EzIsotope, nabPy single trapezoidal filter) instead of a generic role.
14. Dead code and dormant schema are deleted outright, never renamed or
    kept commented (ex: `find_clusters`, `Calibration.is_current`).
15. Definition order: helpers and callees above their callers, checked in
    every pre-edit file summary.
16. Loop variables are the singular of the collection: `for segment in
    segments`, `for pixel in pixels` — never `for k in keys` (AS ruling
    2026-09-08).
17. One quantity, one name, along the whole chain: a value that one
    function produces and another consumes carries the same name at the
    producer's return, at the call site and in every consumer's
    parameter, and the producing function is named for what it returns
    (`count_ratios` returns `ratios`). The bare name is the per-pixel
    dict for one detector (`counts`, `baselines`, `ratios`,
    `pixel_positions`); anything nested is named by its key
    (`counts_by_segment`, `baselines_by_detector`,
    `offsets_by_conditions`), so the suffix says how to index it. A name
    must never lie about what it holds (ex:
    `assign_sources_from_predictions(detector_counts, ...)` received
    ratios). (AS ruling 2026-10-01)
18. Every `return` returns a named variable carrying the quantity's
    name, never a bare expression: `ratios = {...}` then `return ratios`,
    so the bottom of a function says exactly what leaves it and a grep
    for that name finds where it lands. A literal (`return None`,
    `return "?"`) or an input handed back unchanged (`return trend`)
    needs no new name. (AS ruling 2026-10-01)

---

## 2. Global glossary

Terms that recur across files. Where Input A (AS's own cleaned files)
already established a replacement, that replacement wins and is marked
**[A]**. Prose guidance is in the replacement column too.

| term | meaning | replacement |
|---|---|---|
| prior | predicted position the search starts from | predicted position / `predicted_position` **[A]** |
| excess / excesses | count divided by the pixel's own baseline (a ratio, not a subtraction) | `ratio` / `ratios`; the function that makes them is `count_ratios` (AS 2026-10-01, replacing their earlier `relative_counts` so the function names its product — rule 17); nested per-segment dict: `ratios_by_segment` |
| support / n_support | elevated pixels near a peak | `illuminated_neighbors` **[A]** (never "support") |
| evidence / `_evidence` | what the counts say about a source position | "signal strength" / `signal_strength_at` (AS ruled: purge "evidence" and "testifies") |
| k / key / keys | a (run_number, segment_index) pair | `segment_id` / `segment_ids`; in loops the singular of the collection (style rule 16); in prose "segment", never "key" |
| key_positions | per-segment stage readback (linear, horizontal) | `readback_positions` |
| readback | value the instrument reports back (slow controls / EPICS term) | KEEP "readback" (AS ruled 2026-09-08: standard slow-controls vocabulary); define once at first use |
| spec / pool / spec_of / pool_of | the (mounting, holder, convention, field) tuple, and the group of segments sharing it | `conditions` / `conditions_of`; group: "conditioned group" — in prose "segments sharing one mounting and field configuration", never "pool" or "spec" |
| det / dets | detector name, 'upper'/'lower' | `detector` **[A]** |
| rp / rps | RunPixel row(s) | `run_pixel` / `run_pixel_rows` **[A]** |
| tfo | TrapFilterOutput row | `trap_filter_output` |
| tf_label / --tf-label | trap-filter output set name (ex: "nabpy-standard") | `trap_filter_label` in code; CLI keeps `--tf-label` as an accepted alias of `--trap-filter-label` (calibrate.sh / fit_spectra.sh and human muscle memory script against it); also `LDET_ONLY_TRAP_FILTER_LABELS` |
| pred / preds | predicted slot/source position(s) | `predicted_position` / `predicted_positions` (CSV headers stay `pred_x`/`pred_y` — see rulings record item on data-format stability) |
| snap / snap_all / snap_dist | move a prediction onto the strongest nearby pixel cluster | `match_to_cluster` / `match_all_slots` / `match_distance` in code; prose "matched to the strongest nearby pixel"; the CSV column HEADER stays `snap_dist` (tracked review CSVs must stay readable by --apply) |
| seed / seeded / seeding | a fit's starting values | "starting position" / "starting value"; seeded attempts = "predicted-start attempts" |
| recipe / RECIPES / run_recipe | one spectral window's fit definition: window, peak count, starting-value heuristics, quality thresholds | **fit plan** / `FIT_PLANS` / `run_fit_plan` (AS RULED 2026-09-08); module fit_recipes.py -> fit_plans.py at its cleaning pass |
| config (fit/calibration JSON) | record of how a result was produced | KEEP `fit_config` (AS ruled: it is literally a configuration); DB column unchanged |
| anchor — BOTH senses retired | fitting: the two strong CE lines (482/976 keV) gains are measured from; tray: the eye-verified segment pinning the tray frame | AS RULED 2026-09-08: "anchor" leaves the repo entirely. Fitting sense -> **reference peak** (`reference_peak_low`/`_high`, `reference_fit_ok`); tray sense -> **verified position** (`VERIFIED_TRAY_POSITIONS`, `verified_position_for`, `check_verified_installation`) |
| redchi (repo-owned names) | reduced chi-squared | `reduced_chi2` (this one)/ `best_reduced_chi2` / `MAX_REDUCED_CHI2`; lmfit's own `result.redchi` stays, with one explaining comment |
| cen / sig / amp / n{i} / h{i} / beta / num_peaks | frozen fit-model parameter names | keep (frozen fit-function rule); add one glossary comment where first used translating each |
| label (bare) | four different identifiers in different files | always qualify: `fit_label` / `trap_filter_label` / `source_label` / `calibration_label` |
| lo / hi suffix pairs | paired range edges | spell out: `window_low` / `window_high`, `energy_low` / `energy_high`, ... |
| ox,oy / px,py / xx,yy / dx,dy / tx,ty | abbreviated coordinate pairs | spelled pairs: `offset_x`/`offset_y`, `peak_x`/`peak_y`, `pixel_x`/`pixel_y`, `correction_x`/`correction_y`, `frame_x`/`frame_y` (reference points `x0`, `y0` allowed **[A]**) |
| cx, cy | per-axis coefficient triple (a, b, c) of the trend fit frame = a*linear + b*horizontal + c — cx is the x-axis's three numbers, cy the y-axis's | `x_coefficients`, `y_coefficients` |
| gain_scout / scout_ratio / scout | quick pre-fit estimate of a pixel's gain vs nominal | `estimate_gain_ratio` / `gain_ratio`; prose "gain estimate", never "scout" |
| SCOUT_ANCHORS | per-isotope peak definitions the gain estimate uses | `GAIN_ESTIMATE_PEAKS` (AS's name — the constant literally holds peak definitions) |
| NOMINAL_RELATION / relation | the keV<->ADC straight line (nominal, or a pixel's own) | `NOMINAL_KEV_ADC_CONVERSION` / `kev_adc_conversion` (AS ruled: "relation" out) |
| n_peaks / n | how many peaks the fit model has | `number_of_peaks` |
| processed_keys | (run, segment, label, pixel) ids handled this invocation | `processed_pixels` |
| fh / f | open file objects | `csv_file` / `output_file` (name the file's role) |
| exc | a caught exception | `error` (AS ruled; also fix the one `e` in acquisition back to `error`) |
| bounds (fitting) | the ADC fit window (NOT lmfit parameter bounds) | `fit_window`, unpacked as `window_low` / `window_high` |
| base / offset (= 1000) | the +1000 added to lower-detector stored pixel numbers | `lower_detector_pixel_shift` (one name in both scripts) |
| stored | a pixel number in database form (1-127 / 1001-1127) | plain `pixel_number` wherever only one form exists; `stored_pixel_number` ONLY in functions where the detector-local (1-127) and shifted (+1000) forms coexist and must be told apart |
| frozen | a fit locked because a calibration references its peaks | KEEP "frozen" (AS ruled 2026-09-08 — already in user-facing messages and docs); define once: "frozen = referenced by a stored calibration, never refit" |
| STATS_GATE | minimum-statistics requirement before fitting | `STATISTICS_GATE` (keep the phrase "statistics gate") |
| field_configuration_key | compact label of magnet currents + ExB voltage | KEEP `field_configuration_key` **[A]** (AS ruled: it genuinely is a lookup key — "key" is fine when the thing IS one) |
| fallback | two unrelated meanings in the fitting files | name each meaning: `using_bi207_default` (fit_spectra) / `median_credible_width` (fit_recipes); never bare "fallback" |

---

## 3. Per-file appendix

Terms specific to each remaining file. Global-table terms are not
repeated; apply them there too.

### calibrationnet/source_assignment.py
(shared vocabulary imported by scripts/assign_sources.py and
scripts/optimal_positions.py — apply renames in all three)

| term | meaning | proposed |
|---|---|---|
| predict_prior | first-round predicted position from the anchor | `predict_frame_position` |
| offsets (param of predict_prior) | accepted but never used | delete the parameter and its call-site argument |
| score / best_score | quality of one candidate frame translation | `placement_goodness` / `best_goodness` (?) |
| t / best_t | candidate frame translation / best one | `candidate_position` / `best_position` |
| window (locate_frame) | half-widths of the search grid, hex units | `search_half_widths` |
| step (locate_frame) | spacing between candidate translations | `search_spacing` |
| sigma (locate_frame) | width of the pull toward the prediction | `predicted_position_sigma` (?) |
| nx, ny / i, j | grid step counts / step indices | `steps_x`, `steps_y` / `step_index_x`, `step_index_y` |
| assign_from_preds | place sources and claim nearby pixels | `assign_sources_from_predictions` |
| frames_by_key | MISLEADING: keyed by detector, not segment | `frames_by_detector` (rule 17: the values are frames; superseded `located_positions_by_detector`) |
| conv_keys | MISLEADING: the full condition group's segments | `group_segments` |
| offset (local, locate_all_frames) | MISLEADING singular: whole {slot: (dx,dy)} dict | `offsets`, taken from the parameter `offsets_by_conditions` (rule 17) |
| min_weight | minimum summed signal to trust a centroid | `minimum_total_signal` (?) |
| wsum, wx, wy / w | centroid weight totals / one pixel's weight | `total_weight`, `weighted_x_sum`, `weighted_y_sum` / `weight` |
| rs / ref | one slot's residual list / reference slot's median shift | `slot_residuals` / `reference_shift` |
| median (local function) | shadows the imported statistics.median | delete; use the module-level `median` |
| format (local in field_configuration_key) | rounds a setting for display | keep `format` **[A]** (AS preferred it over `fmt` despite shadowing the builtin) |
| A | least-squares coefficient matrix | `least_squares_matrix` |
| solution (slot_offsets) | fitted grid-coefficient vectors for x and y | `fitted_grid_vectors` |
| rows_cols / r, c | row/column numbers parsed from R#C# labels | `slot_rows_columns` / `row_number`, `column_number`; parse the label explicitly or comment the R#C# format |
| predict_trend | MISLEADING: evaluates a fitted trend, predicts a position | `predict_position_from_trend` |
| a, b, _c / lin, hor | fitted slopes + intercept / readback values | `linear_slope`, `horizontal_slope`, `intercept` / `linear_readback`, `horizontal_readback` |
| installation key (prose) | ISO date labeling one tray mounting period | "mounting date"; never "key" |
| sid / seg | source database id / anchor's run segment | `source_id` / `anchor_segment` |
| _select / anchor_for (in-function re-imports) | pointless aliases of module-level imports | delete; use the module-level `select` and `anchor_for` |
| details (compute_baselines) | one segment's {detector: pixel counts} | keep `details` **[A]** — (?) B notes `segment_counts` says more; AS to rule |
| pending (snap_all) | slots not yet matched to a cluster | `unmatched_slots` |
| report (refine_slot_offsets) | per-slot corrections applied + counts | `refine_report` (the name optimal_positions.py already uses at the call site — rule 17) |
| "quantization error" (docstring) | up-to-a-pixel error from placing sources at pixel centers | "pixel-centering error" |
| "discounted" (comments) | signal multiplied by LONE_SPIKE_FACTOR | "down-weighted" (constant name itself is fine) |
| fields (param, locate_all_frames) | per-segment field-configuration label | `field_configurations` |
| illuminated_neighbor_number | counts elevated pixels near a peak | keep the name; make `_evidence` call it instead of repeating the loop |
| leading `_` and bare `_` | Python internal-helper / ignored-value conventions | keep; add one explanation to docs/python_notes.md |

### calibrationnet/fitting.py

| term | meaning | proposed |
|---|---|---|
| prediction (4-tuple) | opaque positional tuple of line energies, relation, scale, source | `predicted_lines`, as a dict with named fields (?) |
| pred_ratio | scale factor for predicted ADC positions | `predicted_position_scale` (?) — fit_recipes inventory said `predicted_gain_ratio`; pick ONE |
| anchors_adc (dict key) | measured ADC positions of the two reference peaks | `reference_peaks_adc` (pending the anchor ruling) |
| pixel_relation | measures the pixel's own keV<->ADC line | `measure_pixel_kev_adc_relation` |
| constant_kev (dict key) | intercept of the keV<->ADC line | `offset_kev` |
| conditioned | the constrained rescue mode | `constrained` (mode label "constrained predicted-start") |
| tail_freeze / freeze_n / freeze_h | tail-shape values held fixed for weak peaks | `held_tail_shape` / `held_tail_n` / `held_tail_h` |
| beta_start | starting tail decay length | `starting_tail_length` (comment: the model parameter itself stays `beta`) |
| beta_options | starting tail lengths tried in turn | `tail_length_starts` |
| fill_in_seeds | builds starting centroids for missing peaks | `fill_in_starting_positions` |
| fit_seeded | fits from caller-supplied starting centroids | `fit_from_starting_positions` |
| init (config key) | which starting-guess method won | `starting_method` |
| init (local dict) | starting parameter values handed to lmfit | `starting_values` |
| thresholds | max allowed relative uncertainty per parameter type | `relative_error_limits` |
| tag | label of the current fit attempt | `attempt_label` |
| fx / fy / fu | windowed bin edges / counts / uncertainties | `window_bin_edges` / `window_counts` / `window_uncertainties` |
| ydata / xdata / yunc | full histogram counts / edges / uncertainties | `counts` / `bin_edges` / `count_uncertainties` |
| preds (local) | predicted ADC position of each line | `predicted_adc` |
| hist | counts array OR (counts, edges) tuple — two shapes, one name | `histogram_counts`; unpack tuples immediately as `counts, bin_edges` |
| best | closest-miss rejected fit / best peak-line matching | `closest_miss` (run_recipe) / `best_match` (fill-in) |
| remember_rejected | keeps the lowest-chi2 rejected attempt | `record_closest_miss` |
| passes | fit windows tried in order | `windows_to_try` |
| window_tag | name of the current window pass | `window_name` |
| window_scale | gain ratio in force for this window | `window_gain_ratio` |
| primary / primary_bounds | the recipe's scaled fit window | `recipe_window` (rename with the "recipe" ruling) |
| pred_bounds | window the accepted rescue fit used | `rescue_window` |
| fit_ydata | counts inside the current window | `window_counts` |
| tried_inputs | starting-input combinations already fitted | `already_attempted` |
| n_attempts | count of fit attempts launched | `attempt_count` |
| rel_tag | where the keV<->ADC relation came from | `relation_source` |
| props | scipy find_peaks properties dict | `peak_properties` |
| combo / cost / matched | candidate peak-to-line assignment / its distance / winner | `line_assignment` / `total_distance` / `matched_lines` |
| found_sigmas | widths measured for found peaks | `measured_widths` |
| mean_found | average measured width | `mean_measured_width` |
| entry / rest / factor | one retry-width option / text after "measured" / multiplier | `retry_option` / `multiplier_text` / `width_multiplier` |
| half | half the gap to the nearest predicted line | `half_gap_to_neighbour` |
| amps | starting guesses in one place, fitted values in another | `starting_amplitudes` / `fitted_amplitudes` |
| evaluated | the fitted model curve over the window | `fitted_curve` |
| healthy / health gate | internal error-size acceptance check | `passes_error_check` / "error-size check" (reserve "quality check" for fit_is_good) |
| sentinel | exclusively-created fallback lock file | `fallback_lock_file` (comment: "marker file created exclusively") |
| b_result / b_bounds / b_note / bx | closest-miss result / window / name / bin edges | `miss_result` / `miss_window` / `miss_note` / `miss_bin_edges` |
| data | per-event trap-filter energies (ADC) | `event_energies` |

### calibrationnet/fit_recipes.py + scripts/fit_spectra.py
(run_recipe, gain_scout, pixel_relation, LINE_GROUP_OF, and the failure-CSV
names are imported from fitting.py — rename in step with that file)

| term | meaning | proposed |
|---|---|---|
| peak_finder (8-value positional tuple) | scipy find_peaks settings poked by bare index | `peak_search_settings`, stored as a NAMED dict so magic indices disappear |
| peak_finder_ladder | settings to try, prominence loosening each step | `peak_search_retry_sequence`; prose "retry sequence"/"retry step", not "ladder"/"rung" |
| ratio0 / anchor0 | gate-window gain ratio + settings, computed twice | `gate_gain_ratio` / `gain_estimate_settings`; compute once and reuse |
| anchor_peaks (dict key) | 1-based positions of the two reference CE peaks | `reference_peaks` (?) — flag for ruling: "anchor" already means the tray anchor |
| anchor_energies (dict key) | keV of the two reference CE lines | `reference_line_energies` (one name here and in fitting.py) |
| second_anchor_window (dict key) | fractional band to search for the second line | `second_line_search_band` |
| search_from (dict key) | ADC bin above which to search (skips threshold region) | `search_above_adc` |
| anchor_fit_ok | whether the CE fit succeeded (Auger fit needs it) | `ce_fit_succeeded` |
| pred (peak_spacing_check) | predicted ADC position of one fitted peak | `predicted_position` |
| _tag | which relation produced the prediction (ignored) | `relation_source` (+ python_notes entry on the underscore convention) |
| stale | outdated stored fits deleted before restoring | `outdated_fits` |
| gate_numbers | measured (counts, peak height) vs the gate minimums | `measured_statistics` (?) |
| MIN_SIG_RELATIVE_ERROR | floor on a width's relative error (false-pass check) | `MIN_WIDTH_RELATIVE_ERROR` |
| error_thresholds / ERROR_THRESHOLDS | max relative error per parameter type | `max_relative_errors` / `MAX_RELATIVE_ERRORS` (reserve "threshold" for the detector threshold) |
| widths / retry_widths (dict keys) | starting width guesses / retry alternatives | `starting_widths` / `retry_starting_widths` |
| find_peaks_with_escalation | reruns find_peaks raising prominence | `find_peaks_raising_prominence` |
| props | scipy find_peaks properties dict | `peak_properties` |
| hist_all / sm_all / bg | full histogram / smoothed / background level | `full_histogram` / `smoothed_histogram` / `background_level` |
| g_lo, g_hi, a_lo, a_hi | gain-scaled CE and Auger window edges | `ce_window_low/high`, `auger_window_low/high` |
| pixel_relation / relation_pix / relation | three near-identical names, two things | `measure_pixel_kev_adc_relation` / `pixel_kev_adc_relation` / `nominal_kev_adc_relation` |
| stage (CSV key) | where in the workflow the pixel stopped | `stopped_at` |
| LINE_GROUP_OF / group | fit-label prefix -> line-group name | `LINE_GROUP_BY_FIT_LABEL` / `line_group` |
| flag | the LOW GAIN warning text | `low_gain_warning` |
| dev | development-shortlist pixel numbers | `development_pixels` (the `--dev` flag itself can stay) |
| w, s, i, srt, mid, par, cens, sigs | single-letter/clipped math locals | `measured_width`, `starting_width`, `peak_number`, `sorted_widths`, `middle_index`, `parameter`, `centroids`, `fitted_widths` |
| a, b / cen_a, cen_b / e_a, e_b | the two reference peaks, positions, energies | `first_reference`, `second_reference` / `..._position` / `..._energy` |
| entry / rest / factor (_width_options) | retry option / text after "measured" / multiplier | `width_option` / `multiplier_text` / `multiplier` |
| _width_options | private generator of starting-width sets | `_starting_width_choices` (+ python_notes note on the underscore) |
| variant | loosened copy of the peak-search settings | `loosened_settings` |
| "hardening" (comment header) | stricter false-pass rejection limits | "stricter checks against false passes" |
| rescue / fill-in (comments) | the two predicted-start passes | keep, but spell out on first use: "predicted-start rescue", "missing-peak fill-in" |
| C-1 / C-3 (comments) | bare step codes from the plan doc | spell the step out, cite "(step C-3 of docs/initial_guess_plan.md)" |
| prominence / wlen / rel_height / plateau_size | scipy find_peaks keyword names | keep (must match scipy); define each once where the settings are built |
| stderr / var_names | lmfit result attributes | keep (lmfit API) + one comment; python_notes candidates |
| IntegrityError | database refused a delete (calibration still references peaks) | keep the name; say that plainly in the except-block comment |
| pairs | (RunPixel, TrapFilterOutput) query rows | `pixel_outputs` |
| r (CSV comprehensions) | one CSV row | `row` |

### calibrationnet/queries.py

| term | meaning | proposed |
|---|---|---|
| stmt | the database question being assembled | `query` |
| s (param) | the open database session | `open_session` |
| _trap_filter | restricts a query to one trap setting (no filtering physics) | `_restrict_to_trap_setting` |
| _run_pixel_filter | restricts a query to pixel/runs/segment | `_restrict_to_pixel_and_runs` |
| trap | (rise, flattop, falltime) settings tuple | `trap_setting` |
| _with_session | runs a query with the caller's or a fresh session | `_call_with_session` |
| fn | the query function being called | `query_function` |
| fresh | the newly opened session | `new_session` |
| recipe (param/column) | SAME thing as fit_label elsewhere in this file | `fit_label` — one name everywhere in the file |
| pars / par_errors | stored fitted parameter values / uncertainties | `fit_parameter_values` / `fit_parameter_errors` (column rename waits for models.py migration) |
| matched_only | three-way peak filter — matched to WHAT unsaid | `matched_to_line` |
| run_settings | Run-column equality filters (the run's conditions) | `run_conditions` |
| tfo_counts / seg_counts / cal_counts | per-run tallies | `filter_output_counts` / `segment_counts` / `calibration_counts` |
| seg / pix | unpacked segment_index / pixel_number | `segment` / `pixel` |
| r | a Run row | `run` |
| cal / cal_id / cal_type / cal_label | Calibration row + unpacked columns | `calibration` / `calibration_id` / `calibration_type` / `calibration_label` |
| cen_lo / cen_hi | fitted centroids of the two reference CE peaks | `centroid_482_adc` / `centroid_976_adc` (?) |
| e_lo / e_hi | keV of those two lines | `reference_energy_low_kev` / `reference_energy_high_kev` (?) — align with the anchor ruling |
| nominal | expected ADC spacing per keV — nominal WHAT unsaid | `nominal_adc_per_kev` |
| const / const_err / quad / quad_err | calibration polynomial terms + errors | `constant_term` / `constant_error` / `quadratic_term` / `quadratic_error` |
| sig / sig_err | CE 976 keV fitted width (ADC) + uncertainty | `ce976_sigma_adc` / `ce976_sigma_error_adc` |
| shift_list / shift | per-point HV shifts / the shared value | `hv_shift_kev_per_point` / `shared_hv_shift_kev` |
| generic | NNDC book values not tied to a source capsule | `nndc_table_peaks` |
| prefix / groups | line-type word ("CE"/"Auger") / energies per type | `line_type` / `energies_by_line_type` |
| fit_lo / fit_hi (columns) | ADC edges of the stored fit window | `fit_range_low_adc` / `fit_range_high_adc` |
| n_points / num_peaks | counts of points / peaks | `number_of_points` / `number_of_peaks` (NEVER touch the lmfit parameter string "num_peaks" — frozen) |
| type (column) | calibration polynomial form | `calibration_type` |
| attempt / window (columns) | which retry won / which window pass | `winning_attempt` / `fit_window` (+ one-line docstring gloss) |
| ORM (prose) | SQLAlchemy object-relational mapping | "row-object" ("row-object helpers", "whole row-objects") |
| lazy-loaded / round-trip (comments) | related rows fetched on attribute access | "fetched one at a time on access, one network trip each" |
| "conditioned rescue" (docstring) | the constrained re-fit with weak-peak shapes held fixed | align with fitting.py's "constrained" rename |
| "join chains" (docstring) | sequences of table-to-table links | "the chain of table links" |
| res (docstring example) | example variable users copy into notebooks | `fit_result` |

### scripts/assign_sources.py + scripts/optimal_positions.py
(shared source-assignment vocabulary is in the global table and the
source_assignment.py section; renames must match all three files)

| term | meaning | proposed |
|---|---|---|
| members | pixels claimed for one source | `claimed_pixels` |
| MEMBER_RADIUS | radius within which pixels are claimed | `CLAIM_RADIUS` |
| VERIFY_RADIUS | how far the peak may sit from the prediction | `PEAK_SEARCH_RADIUS` (?) — AS left this name in his own constants pass; confirm |
| FLAG_DIST | match distance above which a row is flagged CHECK | `CHECK_PEAK_DISTANCE` |
| MIN_EXCESS | minimum count-to-baseline RATIO for an unflagged placement | `MIN_PEAK_RATIO` (it is a ratio, not an excess) |
| n_support (CSV column) | elevated pixels within one ring of the peak | `illuminated_neighbors` (the **[A]** word wins over "elevated") |
| n_pixels (CSV column) | how many pixels the source claims | `claimed_pixel_count` |
| cmap (apply_from_csv) | pixel->count dict, NOT a matplotlib colormap | `pixel_counts` |
| meta | (slot map, holder, installation) tuple | `installation_details` |
| slot_maps / slot_map | which source sits in which slot | `slot_contents` |
| candidate / candidates | grid position under consideration / matching condition groups | prose "trial position"; the main() local -> `matching_groups` |
| candidate_coverage | pixels each grid position reaches, per detector | `pixels_reached_per_position` (?) |
| greedy_plan | picks positions most-new-pixels-first | `choose_fewest_positions`; prose "most-new-pixels-first", not "greedy" |
| cover | per-position {(detector, slot): (pixel, offset)} | `pixels_reached` |
| tight / loose | well-centered sets vs merely-inside sets | `well_centered_sets` / `inside_pixel_sets` |
| take | inner selection-round function | `pick_positions` |
| universe | only these pixels count toward gain (set-cover jargon) | `count_only_pixels` |
| min_gain / gain | minimum / actual newly covered pixel count | `min_new_pixels` / `new_pixel_count` |
| must | pixels the plan must cover (--must-include) | `required_pixels` |
| best_key | the best position's ranking tuple (not a dict key) | `best_ranking` |
| tol_hex | --tolerance-mm converted to hex units | `tolerance_hex` (spell out "tol"; `boundary_hex` already fine) |
| lins / hors / lin_grid / hor_grid / lin_lo ... | linear/horizontal abbreviations throughout | `linear_values` / `horizontal_values` / `linear_grid` / `horizontal_grid` / `linear_low` / `linear_high` / `horizontal_low` / `horizontal_high` |
| nearest / closest / best | index / distance / running minimum — three near-synonyms | `nearest_pixel_index` / `nearest_pixel_distance` / `smallest_distance` |
| n, i (plan report loops) | printed position number / grid array index | `position_number`, `grid_index` |
| seen | pixels already credited to an earlier position | `already_counted` |
| keep | grid point inside the recorded motion envelope | `inside_envelope` |
| "mask" (comment) | removing out-of-envelope grid points | "skip the envelope filtering" |
| stem / OUT_TEMPLATE | shared start of output filenames / its pattern | `output_name` / `OUTPUT_NAME_PATTERN` |
| frac | offset as a fraction of the boundary threshold | `offset_fraction` |
| entry (draw_coverage) | pixel's (best position, offset) or None | `pixel_assignment` |
| compress | pixel list -> range text ("1-3,7-8") | `pixel_ranges_text` |
| write_keys | segments whose review rows are written | `segments_to_write` |
| offsets_for_locate | slot offsets re-keyed for locate_all_frames | `offsets_by_conditions_and_detector` |
| rounds | refine-then-relocate pass count | `refinement_passes` |
| target | how many pixels the plan could possibly cover | `coverable_pixel_count` |
| MM_PER_HEX | millimetres per hex geometry unit (5.2) | `MM_PER_HEX_UNIT` (or keep — the comment already explains it) |
| sid / lab / _sid | source id / source label unpacks | `source_id` / `source_label` |
| ring (apply_from_csv) | counts near the corrected peak (not pixels) | `ring_counts` |
| "primed" (docstring) | search starts from the readback prediction | "starting from the readback-predicted position" |
| "grid-searches" (docstring) | tries every stage position on a fine grid | "tries every position on a fine grid of readback values" |
| "provenance" (docstring) | the record of who reviewed/decided | "the human review record" |
| mappable | matplotlib object needed only for the colorbar | `colorbar_scale` (+ one-line comment: matplotlib needs it for fig.colorbar) |

### scripts/calibrate.py, scripts/extract_adc_peaks.py,
### scripts/process_run.py + calibrationnet/calibration.py,
### calibrationnet/hitmap.py, calibrationnet/db.py

| term | meaning | proposed |
|---|---|---|
| extract_fits | does NOT extract fits: matches peaks to decay lines | `match_peaks_to_lines` |
| choose_kev | picks the simulation keV target for a line | `choose_target_energy` |
| store | writes one calibration + its points | `store_calibration` |
| implied | ADC -> implied keV via the two-anchor line | `implied_energy_kev` |
| KEV_POLICY | the target-selection rule sentence (a constant) | `KEV_TARGET_RULE` |
| cal_type | 'linear' or 'quadratic' polynomial order | `calibration_type` |
| pairs (calibrate.py) | (ADCPeak, keV target row) tuples | `peak_target_pairs` |
| points / p[0]..p[3] | 4-tuples indexed positionally | `calibration_points`; unpack as `(centroid_adc, centroid_error_adc, target_kev, target_error_kev)`, state the order once where built |
| sims | candidate simulation keV rows | `simulation_rows` |
| best (choose_kev) | winner of the specificity ranking | `chosen_row` |
| shift / shifts | run-HV correction(s) in keV | `hv_shift_kev` / `hv_shifts_kev` |
| families | target-value family names used in one calibration | `target_families` |
| version (kev row column) | the target-value family name, not a number | `target_family` (DB column — schedule with a migration) |
| p = result.params | lmfit best-fit parameter set (p has 3 meanings) | `fit_parameters`; comprehension `p` -> `kev_row` / `point` |
| extra_config | extra entries merged into the stored record | `extra_record_entries` (tracks whatever "config" becomes) |
| LINE_GROUPS | fit-label prefix -> line-label prefix table | `FIT_PREFIX_TO_LINE_PREFIX` |
| groups (extract_adc_peaks) | decay lines per prefix, sorted by energy | `lines_by_group` |
| anchor_lo / anchor_hi | 'hi' is highest INTENSITY, not energy — misleading | `lowest_energy_anchor` / `brightest_anchor` |
| adc_lo, kev_lo, adc_hi, kev_hi | the two anchor peaks' positions and energies | `first_anchor_adc` / `first_anchor_kev` / `second_anchor_adc` / `second_anchor_kev` (align with the anchor ruling) |
| slope | the two-anchor gain, keV per ADC | `anchor_gain_kev_per_adc` |
| resid | match residual in keV | `residual_kev` |
| pars / par_errors (SpectrumFit) | stored parameter values / uncertainties | `parameter_values` / `parameter_errors` (DB columns — migration territory) |
| "stage" (verb, docstrings) | rows added to the session, not yet committed | "queue for saving" / "add to the session (saved at commit)" |
| preflight | checks required seed data exists | `check_seed_data` |
| counts (preflight) | MISLEADING: holds tables to check, not counts | `required_tables` |
| stage(title) | prints a step banner (collides with noun "stage") | `announce_stage` |
| run_script / argv | runs a child script / its command words | `run_pipeline_script` / `command_words` |
| code | a child process's exit code | `exit_code` (+ comment "0 = success") |
| "idempotent" (docstring) | safe to run twice | "safe to re-run: each stage checks what already exists and continues" |
| "Knobs" (docstring) | the adjustable settings | "Settings you can adjust" |
| "drains" (help text) | SLURM array finished | "when the array finishes" |
| --rt / --ft / --fall | trap rise / flat top / fall time flags | `--rise-time` / `--flat-top` / `--fall-time` (?) — keep short aliases only if the upstream submit script's flags must match |
| run_list | a temporary FILE of run numbers, not a list | `run_list_file` |
| fit_extra / extra | pass-through command-line flags | `fit_flags` / `extra_flags` |
| n_bc | pixels with a board channel recorded | `board_channel_count` |
| gain0 | first fit's gain, used only for figure error bars | `gain_for_error_bars` |
| yerr | total point error in keV (matplotlib name leaked) | `energy_error_kev` (still passed as `yerr=`) |
| grid (calibration.py) | dense ADC values for drawing smooth curves | `curve_adc_values` (avoid "grid" = detector geometry) |
| draw (hitmap.py) | draws one detector's hit map | `draw_hitmap` |
| vmax | color scale maximum (93rd-percentile default) | `color_scale_max` |
| cmap / sm (hitmap.py) | colormap object / colorbar helper | `colormap` / `colorbar_scale` |
| ordered | counts sorted ascending for the percentile cap | `sorted_counts` |
| factory (db.py) | the sessionmaker callable | `make_session` (or inline: `return Session(bind=get_engine())`) |
| lru_cache(maxsize=1) | build the engine once, reuse it | keep; add comment "create the engine once and reuse it" + python_notes entry |
| engine | SQLAlchemy's connection manager | keep (library term) + comment "the shared database connection manager" |
| redchi / chisqr / nfree / stderr | lmfit result attribute names | keep (library-fixed) + one comment where first read: "lmfit's names: redchi = reduced chi2, nfree = degrees of freedom" |
| n_stored / n_flagged | one pixel's stored / unmatched peak counts | `pixel_stored_count` / `pixel_flagged_count` |
| nndc_energy | newest generic NNDC value for a line | keep (or `newest_nndc_energy` to surface the selection rule) (?) |

---

## Rulings record (AS, 2026-09-08 — the former open list, resolved)

1. "evidence"/"testifies" — PURGED: "signal strength" /
   `signal_strength_at`.
2. "recipe" — RENAME repo-wide: **fit plan** (`FIT_PLANS`,
   `run_fit_plan`; fit_recipes.py -> fit_plans.py at its pass).
3. "anchor" — RETIRED IN BOTH SENSES (AS: "confusing for both").
   Fitting -> **reference peak**; tray -> **verified position**.
4. `config` — KEEP `fit_config` (AS: it is literally a configuration).
5. `field_configuration_key` — KEEP (AS: it genuinely is a lookup key).
6. `exc` — `error`; the one stray `e` in acquisition gets fixed back.
7. "readback" — KEEP (standard slow-controls term); define at first use.
8. "frozen" — KEEP; define once at first use.
9. SCOUT_ANCHORS -> `GAIN_ESTIMATE_PEAKS`; NOMINAL_RELATION ->
   `NOMINAL_KEV_ADC_CONVERSION` (both AS's wording).
10. Data-format stability (dev-session recommendation, standing unless
    AS objects): CSV COLUMN HEADERS and CLI FLAGS are interfaces, not
    prose — headers stay as written (`snap_dist`, `pred_x/y`: tracked
    review CSVs must remain readable by `--apply`), and renamed flags
    keep their old spelling as an accepted alias (`--tf-label`,
    `--rt/--ft/--fall` — shell scripts and muscle memory script against
    them). Code identifiers still follow the glossary.

Deferred to the owning file's cleaning pass (context needed):
`details` vs `segment_counts` (compute_baselines); `pred_ratio` (one of
`predicted_position_scale` / `predicted_gain_ratio`); VERIFY_RADIUS
(AS left it during their constants pass — keep or
`PEAK_SEARCH_RADIUS`); the remaining "(?)" marks in the appendix.

---

## Rulings record (AS, 2026-10-01 — data flow in source_assignment.py)

Found during AS's pass on the applied pilot draft: the glossary had fixed
word choice but not flow — the ratios dict was under four names
(`relative_counts` made it unnamed, the scripts held it as `excesses`,
six functions took it as `ratios`, the three matching functions took it
as `counts`/`detector_counts` while actually receiving ratios). Hence
style rules 17 and 18, and these names, one per quantity:

1. `counts_by_segment` — {segment_id: {detector: counts}} (was `counts`).
2. `baselines_by_detector` / `baselines` for one detector's slice (was
   `baselines` / `detector_baselines`); the scripts' three-level dict is
   `baselines_by_conditions`.
3. `ratios`, made by `count_ratios(counts, baselines)` (was
   `relative_counts`, AS's own earlier name — reversed so the function
   names its product); `counts`/`detector_counts` in the matching
   functions -> `ratios`; scripts' `excesses`/`excess` ->
   `ratios_by_segment`/`ratios`.
4. `pixel_positions` — {pixel: (x, y)} (was `positions`).
5. `slot_position` — one slot's (x, y) under a trial frame placement
   (was `position` in `_signal_strength_at`).
6. `frame` and its collections: `best_frame`/`candidate_frame` inside
   `locate_frame`, `first_round_frames`, `frames_by_detector`, `frames`
   (was `best_position`, `located`, `located_by_detector`).
7. `offsets_by_conditions` for the dict keyed by conditions + detector;
   `offsets` for one detector's {slot: (dx, dy)} (was `offsets`/`offset`
   in `locate_all_frames`, `offsets_for_locate`/`offsets_by_det` in
   optimal_positions.py).
8. `readback_positions` in the scripts too (was `key_positions`).
9. `sources_by_slot` — {slot: (source_id, source_label)}, the first
   return of `installation_for`; `source_labels_by_slot` for the reduced
   {slot: source_label}; per segment in the scripts: `sources_by_segment`
   (was `slot_maps`, `slot_sources`, `labels`).
10. Named returns everywhere (rule 18); `predicted_position` keeps both
    of its uses — where the frame's reference slot is expected before
    locating, and where every slot is expected after locating — because
    both are an expectation for a slot's position, stated in the
    module's data-flow section.
