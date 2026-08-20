"""Fit the peaks in stored trap filter outputs and save every GOOD fit
to spectrum_fits.

For each requested run pixel this pulls the trap filter output's
energies from the database, histograms and fits them with the SAME
fitting code the physics was developed with (calibrationnet/
fit_functions.py — never modified), and stores each accepted fit via
SpectrumFit.from_lmfit: parameters, errors, var_names + covariance,
chi2/ndf/reduced chi2, success, and the fit inputs (config). What each
column holds is documented in docs/fit_storage.md.

Which fits to run comes from the pixel's assigned source: each isotope
has a recipe list (calibrationnet/fit_recipes.py — ADC window, number
of peaks, peak-finder settings, starting widths). Every fit attempt
must pass the quality check (fit_is_good: converged, uncertainties
present and within thresholds, reduced chi2 within the cap); a fit
that fails it is retried with the recipe's retry starting widths and
gentler peak-finder settings (fit_attempts), then the predicted-start
rescue. If EVERY attempt fails, nothing is stored — a junk fit never
enters the database — and any previously stored fit with the same
(output, label) is removed. Re-running REPLACES a fit with the same
(output, label), so the table holds one current fit per label.

Failed-but-interesting pixels are collected for later review: pixels
that passed the statistics gate yet failed every fit attempt go to
<plot dir>/fit_failures_summary.csv (one file across all runs fitted);
--failures-detail additionally writes every non-fitted pixel of the
run, including the statistics-gate skips, to
<plot dir>/Run<run>_seg<segment>_failures.csv.

    python scripts/fit_spectra.py --run 8622 --pixels 60
    python scripts/fit_spectra.py --run 9327                # every pixel
    python scripts/fit_spectra.py --run 8622 --pixels 60 --plot fit_plots/
"""
import argparse
import csv
from pathlib import Path

import numpy as np
from sqlalchemy import select

from calibrationnet.db import get_session
from calibrationnet.fit_recipes import (NOMINAL_RELATION, RECIPES,
                                        SCOUT_ANCHORS, STATS_GATE)
# The fitting procedure itself (retry ladder, quality gate, fill-in,
# rescue, predicted window, figures) lives in calibrationnet/fitting.py
# and is shared with the offline pipeline (scripts/offline/) — this
# script is only the database side: which pixels, which recipes, store
# the accepted fits.
from calibrationnet.fitting import (LDET_ONLY_TF_LABELS, LINE_GROUP_OF,
                                    centroid_report, gain_scout,
                                    pixel_relation, plot_failed_spectrum,
                                    run_recipe, update_failure_csv)
from calibrationnet.models import (ADCPeak, CalibrationPoint, RunPixel,
                                   SpectrumFit, TrapFilterOutput)
from calibrationnet.queries import line_energies
from sqlalchemy.exc import IntegrityError


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--pixels", type=int, nargs="+", default=None,
                        help="stored pixel numbers (1-127 upper, "
                             "1001-1127 lower); default: every pixel of "
                             "the segment with a trap filter output")
    parser.add_argument("--tf-label", default="nabpy-standard",
                        help="which trap filter outputs to fit")
    parser.add_argument("--detector", choices=("udet", "ldet"),
                        default=None,
                        help="fit only this detector's pixels (udet: "
                             "1-127, ldet: 1001-1127); default both. "
                             "E.g. the Fall 2025 campaign fits UDET "
                             "only at nabpy-standard — 2025 LDET is "
                             "the known oddball (AS ruling).")
    parser.add_argument("--isotope", default=None,
                        help="force this isotope's recipes instead of "
                             "using each pixel's assigned source")
    parser.add_argument("--plot", type=Path, default=Path("fit_plots"),
                        metavar="DIR",
                        help="save a figure per fit here (default "
                             "fit_plots/ — development policy: every fit "
                             "gets a plot for visual verification)")
    parser.add_argument("--no-plot", action="store_true",
                        help="skip the figures (also skips the failure "
                             "review CSVs, which live in the plot dir)")
    parser.add_argument("--failures-detail", action="store_true",
                        help="also write every non-fitted pixel of this "
                             "run (excluded / statistics gate / all "
                             "attempts failed) to Run<run>_seg<segment>"
                             "_failures.csv in the plot dir")
    parser.add_argument("--dev", action="store_true",
                        help="fit only the development shortlist in "
                             "data/dev_pixels.csv (one representative "
                             "pixel per known class, per trap label) — "
                             "a fast cycle for developing the fit "
                             "routine instead of whole runs")
    args = parser.parse_args()

    if args.dev:
        with open("data/dev_pixels.csv", newline="") as fh:
            dev = [int(r["pixel"]) for r in csv.DictReader(fh)
                   if r["tf_label"] == args.tf_label]
        if not dev:
            raise SystemExit(f"data/dev_pixels.csv has no pixels for "
                             f"tf_label {args.tf_label}")
        args.pixels = sorted(set(dev) & set(args.pixels)
                             if args.pixels else dev)
        print(f"--dev: fitting pixels {args.pixels} ({args.tf_label})")

    if args.no_plot:
        args.plot = None
    if args.plot is not None:
        args.plot.mkdir(parents=True, exist_ok=True)

    fitted = failed = skipped = 0
    failure_rows = []      # rows for the failure review CSVs
    processed_keys = set() # every (run, seg, tf, pixel) this invocation saw

    def record_failure(pixel, recipe_label, stage, gate_numbers=None,
                       attempts="", best_redchi="", best_reason="",
                       figure=""):
        failure_rows.append({
            "run": str(args.run), "segment": str(args.segment),
            "pixel": str(pixel), "tf_label": args.tf_label,
            "recipe": recipe_label, "stage": stage,
            "ce_window_counts": ("" if gate_numbers is None
                                 else str(gate_numbers[0])),
            "ce_peak_height": ("" if gate_numbers is None
                               else f"{gate_numbers[1]:.0f}"),
            "attempts": str(attempts), "best_redchi":
                ("" if best_redchi in ("", None) else f"{best_redchi:.2f}"),
            "best_reason": best_reason,
            "figure": figure,
        })

    with get_session() as session:
        query = (
            select(RunPixel, TrapFilterOutput)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.run_number == args.run,
                   RunPixel.segment_index == args.segment,
                   TrapFilterOutput.label == args.tf_label)
            .order_by(RunPixel.pixel_number)
        )
        if args.pixels:
            query = query.where(RunPixel.pixel_number.in_(args.pixels))
        if args.detector == "udet":
            query = query.where(RunPixel.pixel_number < 1000)
        elif args.detector == "ldet":
            query = query.where(RunPixel.pixel_number >= 1000)
        pairs = session.execute(query).all()
        lines_by_isotope = {}
        if not pairs:
            raise SystemExit(
                f"no '{args.tf_label}' trap filter outputs for run "
                f"{args.run} segment {args.segment}"
                f"{f' pixels {args.pixels}' if args.pixels else ''}."
            )

        with open("data/excluded_pixels.csv", newline="") as fh:
            excluded_pixels = {int(r["pixel"]): r["reason"]
                               for r in csv.DictReader(fh)}

        for rp, tfo in pairs:
            processed_keys.add((str(args.run), str(args.segment),
                                args.tf_label, str(rp.pixel_number)))
            if (args.tf_label in LDET_ONLY_TF_LABELS
                    and rp.pixel_number < 1000):
                print(f"pixel {rp.pixel_number}: skipped (UDET is not "
                      f"fitted at {args.tf_label} — LDET only, AS ruling)")
                skipped += 1
                continue
            if rp.pixel_number in excluded_pixels:
                print(f"pixel {rp.pixel_number}: EXCLUDED — "
                      f"{excluded_pixels[rp.pixel_number]}")
                record_failure(rp.pixel_number, "", "excluded")
                continue
            # GATE-ONLY SELECTION (AS ruling 2026-08-15): the source
            # assignment influences NOTHING here — enough signal means
            # the pixel is attempted. Recipe choice: the assigned
            # isotope's recipes when a recipe exists for it, otherwise
            # Bi-207 (unassigned pixels AND known non-Bi assignments —
            # the quality gate + spacing check reject wrong-source
            # spectra honestly). Validated on 9469: 25 gate-passing
            # unassigned dwells were silently skipped by the old rule,
            # and 10/10 gate-passing Cd-ASSIGNED dwells fit cleanly as
            # mis-claimed Bi light. Pure-foreign spectra never pass
            # the gate (347/357 Cd dwells stopped there). The assigned
            # isotope is recorded in the log and every fit's config so
            # fallback fits stay identifiable.
            assigned = rp.source and rp.source.isotope.name
            isotope = args.isotope or assigned
            recipes = RECIPES.get(isotope)
            fallback = recipes is None
            if fallback:
                isotope = "Bi-207"
                recipes = RECIPES[isotope]

            data = np.asarray(tfo.energies)
            print(f"pixel {rp.pixel_number} ({isotope}"
                  + (f", assigned={assigned or 'none'}" if fallback else "")
                  + f", {len(data)} waveforms):")

            # C-3 statistics gate (AS rule, 2026-08-05): the CE window
            # is the gatekeeper — its counts and strongest peak height
            # decide whether the pixel is worth fitting at all. The
            # Auger window's own statistics are never used: a huge
            # Auger signal can mean a WORSE pixel (the unexplained
            # ~62 ADC line dominates dead pixels).
            gate = STATS_GATE.get(isotope)
            gate_numbers = None
            if gate is not None and recipes:
                hist_all, _ = np.histogram(data, bins=np.arange(0, 4500))
                sm_all = np.convolve(hist_all, np.ones(5) / 5, mode="same")
                anchor0 = SCOUT_ANCHORS.get(isotope)
                ratio0 = 1.0
                if anchor0 is not None:
                    ratio0 = gain_scout(data, anchor0)
                    if abs(ratio0 - 1.0) <= 0.05:
                        ratio0 = 1.0
                g_lo = int(recipes[0]["bounds"][0] * ratio0)
                g_hi = int(recipes[0]["bounds"][1] * ratio0)
                window = hist_all[g_lo:g_hi]
                bg = float(np.median(window))
                peak_height = float(sm_all[g_lo:g_hi].max() - bg)
                gate_numbers = (int(window.sum()), peak_height)
                if (window.sum() < gate["min_window_counts"]
                        or peak_height < gate["min_peak_height"]):
                    print(f"  skipped: insufficient statistics "
                          f"(CE window counts={int(window.sum())}, "
                          f"peak height={peak_height:.0f}; gate needs "
                          f">={gate['min_window_counts']} and "
                          f">={gate['min_peak_height']})")
                    figure = ""
                    if args.plot is not None:
                        fig_path = args.plot / (
                            f"Run{args.run}_seg{args.segment}"
                            f"_pix{rp.pixel_number}"
                            f"_{recipes[0]['label']}.png")
                        plot_failed_spectrum(
                            data, recipes[0], ratio0, None, fig_path,
                            note="SKIPPED: insufficient statistics "
                                 "(data only)")
                        figure = fig_path.name
                    record_failure(rp.pixel_number, recipes[0]["label"],
                                   "statistics gate", gate_numbers,
                                   figure=figure)
                    skipped += 1
                    continue
                a_lo = int(recipes[-1]["bounds"][0] * ratio0) or 5
                a_hi = int(recipes[-1]["bounds"][1] * ratio0)
                auger_peak = float(sm_all[a_lo:a_hi].max()
                                   - np.median(hist_all[a_lo:a_hi]))
                if len(recipes) > 1 and auger_peak > peak_height:
                    print(f"  SUSPECT: low-energy window peak "
                          f"({auger_peak:.0f}) exceeds the strongest CE "
                          f"peak ({peak_height:.0f}) — not Auger physics")

            # Estimate this pixel's gain relative to nominal and scale
            # the recipe windows to match (within 5% = use the recipe
            # exactly, so healthy pixels fit identically to before).
            scout_ratio = 1.0
            anchor = SCOUT_ANCHORS.get(isotope)
            if anchor is not None:
                scout_ratio = gain_scout(data, anchor)
                if abs(scout_ratio - 1.0) <= 0.05:
                    scout_ratio = 1.0
                else:
                    print(f"  gain scout: strongest peak at "
                          f"{scout_ratio:.3f}x nominal — windows scaled")

            pixel_fitted = 0
            # Line energies for this isotope (once): they let the
            # second-chance fit compute starting peak positions.
            if isotope not in lines_by_isotope:
                lines_by_isotope[isotope] = line_energies(session, isotope)
            relation = NOMINAL_RELATION.get(isotope)
            # C-1: prefer this pixel's own two-anchor relation.
            relation_pix = (pixel_relation(data, anchor)
                            if anchor is not None else None)
            if relation_pix is not None:
                print(f"  pixel relation (two-anchor): "
                      f"{relation_pix['gain_kev_per_adc']:.4f} keV/ADC, "
                      f"offset {relation_pix['constant_kev']:+.1f} keV")

            anchor_fit_ok = False   # the CE recipe anchors the rest
            try:
                for recipe_index, recipe in enumerate(recipes):
                    # SKIP-FROZEN (AS ruling 2026-08-20): a stored fit
                    # whose peaks a calibration references is KEPT, not
                    # re-fitted — the freeze is per RECIPE, not per
                    # pixel. Before this, the per-pixel transaction
                    # rolled back a frozen CE replacement TOGETHER with
                    # a fresh, innocent Auger fit from the same pass
                    # (the re-sweep silently lost >=14 clean Augers),
                    # and re-sweeps burned the full ladder on fits that
                    # could never be stored. A kept CE fit still
                    # provides the Auger's anchors.
                    frozen = session.execute(
                        select(CalibrationPoint.id)
                        .join(ADCPeak,
                              CalibrationPoint.adc_peak_id == ADCPeak.id)
                        .join(SpectrumFit,
                              ADCPeak.spectrum_fit_id == SpectrumFit.id)
                        .where(SpectrumFit.trap_filter_output_id == tfo.id,
                               SpectrumFit.label == recipe["label"])
                        .limit(1)).first() is not None
                    if frozen:
                        print(f"  {recipe['label']}: kept (frozen — its "
                              "peaks are referenced by a calibration)")
                        if recipe_index == 0:
                            anchor_fit_ok = True
                        continue
                    if recipe_index > 0 and not anchor_fit_ok:
                        print(f"  {recipe['label']}: skipped (the CE fit "
                              "did not succeed — it provides the anchors)")
                        record_failure(rp.pixel_number, recipe["label"],
                                       "skipped (CE fit failed)",
                                       gate_numbers)
                        continue
                    group = LINE_GROUP_OF.get(recipe["label"].split("-")[0])
                    energies = lines_by_isotope[isotope].get(group, [])
                    prediction = None
                    if relation_pix is not None and len(energies) == recipe["n_peaks"]:
                        prediction = (energies, relation_pix, 1.0,
                                      "two-anchor")
                    elif relation is not None and len(energies) == recipe["n_peaks"]:
                        prediction = (energies, relation, scout_ratio,
                                      "nominal-scaled")
                    plot_path = None
                    if args.plot is not None:
                        plot_path = args.plot / (
                            f"Run{args.run}_seg{args.segment}"
                            f"_pix{rp.pixel_number}_{recipe['label']}.png")
                    try:
                        result, bounds, config = run_recipe(
                            data, recipe, scout_ratio, plot_path,
                            prediction)
                    except Exception as exc:
                        print(f"  {recipe['label']}: FAILED with an "
                              f"unexpected error ({exc})")
                        result, bounds = None, None
                        config = {"attempts": "", "best_redchi": None,
                                  "stage": f"error: {exc}"}

                    # One current fit per (output, label): whether this
                    # attempt succeeded or failed, any previously stored
                    # fit with the same label is stale — remove it.
                    stale = list(session.scalars(
                        select(SpectrumFit)
                        .where(SpectrumFit.trap_filter_output_id == tfo.id,
                               SpectrumFit.label == recipe["label"])
                    ))
                    for old in stale:
                        session.delete(old)

                    if result is None:
                        if stale:
                            print(f"  {recipe['label']}: previously "
                                  "stored fit removed (junk is never "
                                  "kept)")
                        print(f"  {recipe['label']}: FAILED — no attempt "
                              "passed the quality check")
                        record_failure(
                            rp.pixel_number, recipe["label"],
                            config.get("stage", "all attempts failed"),
                            gate_numbers,
                            attempts=config.get("attempts", ""),
                            best_redchi=config.get("best_redchi"),
                            figure=plot_path.name if plot_path else "")
                        failed += 1
                        continue

                    config["recipe_isotope"] = isotope
                    config["assigned_isotope"] = assigned
                    fit = SpectrumFit.from_lmfit(
                        result,
                        trap_filter_output=tfo,
                        label=recipe["label"],
                        fit_range=bounds,
                        config=config,
                    )
                    session.add(fit)
                    pixel_fitted += 1
                    if recipe_index == 0:
                        anchor_fit_ok = True

                    flag = ""
                    if scout_ratio != 1.0:
                        # Low gain is not stationary (pixels drift in and
                        # out of it), so every scout-scaled fit is flagged
                        # for human verification regardless of quality.
                        flag = (f"  <-- LOW GAIN ({scout_ratio:.2f}x) "
                                "— verify")
                    accepted = config["attempt"]
                    if config.get("window", "recipe window") != "recipe window":
                        accepted += f" ({config['window']})"
                    print(f"  {recipe['label']}: reduced_chi2="
                          f"{result.redchi:.2f} "
                          f"accepted={accepted}{flag}")
                    for line in centroid_report(result):
                        print(f"    {line}")
                session.commit()  # per pixel: an interruption loses nothing
                fitted += pixel_fitted
            except IntegrityError:
                session.rollback()
                print(f"  pixel {rp.pixel_number}: REFUSED — its fits' "
                      "peaks are referenced by a calibration (frozen). "
                      "Delete or rebuild that calibration to refit.")
                # Its own honest stage (AS, 2026-08-20) — this was
                # recorded as "excluded", polluting failure review
                # whenever a re-sweep hits already-calibrated pixels.
                record_failure(rp.pixel_number, "",
                               "frozen (calibration references its peaks)")
                continue

    if args.plot is not None:
        update_failure_csv(args.plot / "fit_failures_summary.csv",
                           failure_rows, processed_keys,
                           stages={"all attempts failed"})
        if args.failures_detail:
            update_failure_csv(
                args.plot / f"Run{args.run}_seg{args.segment}_failures.csv",
                failure_rows, processed_keys)

    print(f"\n{fitted} fit(s) stored, {failed} failed, "
          f"{skipped} pixel(s) skipped")


if __name__ == "__main__":
    main()
