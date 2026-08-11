"""Fit spectra from filter-output CSVs — the offline counterpart of
scripts/fit_spectra.py, for working entirely WITHOUT the database.

Reads the pixel,energy CSVs that scripts/offline/trap_filter.py (or the
cluster staging) wrote, fits every pixel with the IDENTICAL procedure
the database pipeline uses (calibrationnet/fitting.py — retry ladder,
quality check, fill-in, rescue, predicted window; the frozen model in
calibrationnet/fit_functions.py is never touched), and writes:

    <out>/Run{run}_seg{seg}_fits.csv    one row per accepted fit
    <plot>/Run..._<label>.png           the usual per-fit figures
    <plot>/fit_failures_summary.csv     pixels with statistics that
                                        still failed (review list)

Line energies come from data/decay_energies.csv (the repo file the
database seeds from) — no connection needed. Scope: Bi-207 (the only
isotope with recipes). These results live in FILES ONLY; the database
remains the record — when it returns, ingest the filter CSVs and refit
through scripts/fit_spectra.py.

    python scripts/offline/fit_spectra.py offline_output/filter/Run9416_seg0_*.csv
    python scripts/offline/fit_spectra.py offline_output/filter --pixels 51 1077
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

from calibrationnet.fit_recipes import (NOMINAL_RELATION, RECIPES,
                                        SCOUT_ANCHORS, STATS_GATE)
from calibrationnet.fitting import (LINE_GROUP_OF, centroid_report,
                                    gain_scout, pixel_relation,
                                    plot_failed_spectrum, run_recipe,
                                    update_failure_csv)
from calibrationnet.pipeline.trap_filter import parse_filter_filename

REPO = Path(__file__).resolve().parents[2]
MAX_PEAKS = 6

FIT_FIELDS = (["run", "segment", "pixel", "isotope", "label",
               "fit_lo", "fit_hi", "n_peaks", "chi2", "ndf",
               "reduced_chi2", "attempt", "window", "scout_ratio"]
              + [f"{q}{i}" for i in range(1, MAX_PEAKS + 1)
                 for q in ("cen", "cen_err", "sig", "sig_err",
                           "amp", "amp_err")])


def line_energies_from_csv(isotope):
    """{'CE': [keV ascending], 'Auger': [...]} straight from
    data/decay_energies.csv — the same file the database is seeded
    from, so offline fits use the same line list."""
    groups = defaultdict(list)
    with open(REPO / "data" / "decay_energies.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["isotope"] != isotope:
                continue
            groups[row["label"].split()[0]].append(
                float(row["energy_kev"]))
    return {prefix: sorted(values) for prefix, values in groups.items()}


def read_filter_csv(path):
    """{pixel: np.array(energies)} from a pixel,energy staging CSV.
    Rows with an empty energy (present in some 2025-era CSVs) are
    skipped, matching what fitting a histogram would do anyway."""
    per_pixel = defaultdict(list)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["energy"]:
                continue
            per_pixel[int(row["pixel"])].append(float(row["energy"]))
    return {pixel: np.asarray(energies)
            for pixel, energies in per_pixel.items()}


def fit_row(run, segment, pixel, isotope, recipe, bounds, result, config):
    row = {"run": run, "segment": segment, "pixel": pixel,
           "isotope": isotope, "label": recipe["label"],
           "fit_lo": bounds[0], "fit_hi": bounds[1],
           "n_peaks": recipe["n_peaks"],
           "chi2": f"{result.chisqr:.4f}", "ndf": result.nfree,
           "reduced_chi2": f"{result.redchi:.4f}",
           "attempt": config.get("attempt", ""),
           "window": config.get("window", ""),
           "scout_ratio": config.get("scout_ratio", "")}
    for i in range(1, MAX_PEAKS + 1):
        for q, name in (("cen", f"cen{i}"), ("sig", f"sig{i}"),
                        ("amp", f"amp{i}")):
            par = result.params.get(name)
            row[f"{q}{i}"] = "" if par is None else f"{par.value:.4f}"
            row[f"{q}_err{i}"] = ("" if par is None or par.stderr is None
                                  else f"{par.stderr:.4f}")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+",
                        help="filter-output CSV file(s) and/or folder(s)")
    parser.add_argument("--isotope", default="Bi-207",
                        help="which isotope's recipes to fit "
                             "(offline scope: Bi-207)")
    parser.add_argument("--pixels", type=int, nargs="+", default=None)
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/fits"))
    parser.add_argument("--plot", type=Path,
                        default=Path("offline_output/fit_plots"))
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--failures-detail", action="store_true")
    args = parser.parse_args()

    recipes = RECIPES.get(args.isotope)
    if recipes is None:
        raise SystemExit(f"no recipes for {args.isotope!r} "
                         f"(have: {sorted(RECIPES)})")
    lines = line_energies_from_csv(args.isotope)
    relation = NOMINAL_RELATION.get(args.isotope)
    anchor = SCOUT_ANCHORS.get(args.isotope)
    gate = STATS_GATE.get(args.isotope)

    with open(REPO / "data" / "excluded_pixels.csv", newline="") as fh:
        excluded_pixels = {int(r["pixel"]): r["reason"]
                           for r in csv.DictReader(fh)}

    files = []
    for item in args.inputs:
        p = Path(item)
        files += sorted(p.glob("*filter_output*.csv")) if p.is_dir() else [p]
    if not files:
        raise SystemExit("no filter-output CSVs found.")

    args.out.mkdir(parents=True, exist_ok=True)
    if args.no_plot:
        args.plot = None
    else:
        args.plot.mkdir(parents=True, exist_ok=True)

    for path in files:
        meta = parse_filter_filename(path)
        run = meta.get("run_number", 0)
        segment = meta.get("segment_index", 0)
        per_pixel = read_filter_csv(path)
        print(f"{path.name}: run {run} segment {segment}, "
              f"{len(per_pixel)} pixels")

        fit_rows, failure_rows, processed = [], [], set()
        fitted = failed = skipped = 0

        def record_failure(pixel, recipe_label, stage, gate_numbers=None,
                           attempts="", best_redchi="", figure=""):
            failure_rows.append({
                "run": str(run), "segment": str(segment),
                "pixel": str(pixel), "tf_label": "offline",
                "recipe": recipe_label, "stage": stage,
                "ce_window_counts": ("" if gate_numbers is None
                                     else str(gate_numbers[0])),
                "ce_peak_height": ("" if gate_numbers is None
                                   else f"{gate_numbers[1]:.0f}"),
                "attempts": str(attempts),
                "best_redchi": ("" if best_redchi in ("", None)
                                else f"{best_redchi:.2f}"),
                "figure": figure,
            })

        for pixel in sorted(per_pixel):
            if args.pixels and pixel not in args.pixels:
                continue
            processed.add((str(run), str(segment), "offline", str(pixel)))
            if pixel in excluded_pixels:
                print(f"pixel {pixel}: EXCLUDED — "
                      f"{excluded_pixels[pixel]}")
                record_failure(pixel, "", "excluded")
                continue
            data = per_pixel[pixel]
            print(f"pixel {pixel} ({args.isotope}, "
                  f"{len(data)} waveforms):")

            # C-3 statistics gate — identical to the database pipeline.
            gate_numbers = None
            if gate is not None and recipes:
                hist_all, _ = np.histogram(data, bins=np.arange(0, 4500))
                sm_all = np.convolve(hist_all, np.ones(5) / 5,
                                     mode="same")
                ratio0 = 1.0
                if anchor is not None:
                    ratio0 = gain_scout(data, anchor)
                    if abs(ratio0 - 1.0) <= 0.05:
                        ratio0 = 1.0
                g_lo = int(recipes[0]["bounds"][0] * ratio0)
                g_hi = int(recipes[0]["bounds"][1] * ratio0)
                window = hist_all[g_lo:g_hi]
                peak_height = float(sm_all[g_lo:g_hi].max()
                                    - np.median(window))
                gate_numbers = (int(window.sum()), peak_height)
                if (window.sum() < gate["min_window_counts"]
                        or peak_height < gate["min_peak_height"]):
                    print(f"  skipped: insufficient statistics "
                          f"(CE window counts={int(window.sum())}, "
                          f"peak height={peak_height:.0f})")
                    figure = ""
                    if args.plot is not None:
                        fig_path = args.plot / (
                            f"Run{run}_seg{segment}_pix{pixel}"
                            f"_{recipes[0]['label']}.png")
                        plot_failed_spectrum(
                            data, recipes[0], ratio0, None, fig_path,
                            note="SKIPPED: insufficient statistics "
                                 "(data only)")
                        figure = fig_path.name
                    record_failure(pixel, recipes[0]["label"],
                                   "statistics gate", gate_numbers,
                                   figure=figure)
                    skipped += 1
                    continue

            scout_ratio = 1.0
            if anchor is not None:
                scout_ratio = gain_scout(data, anchor)
                if abs(scout_ratio - 1.0) <= 0.05:
                    scout_ratio = 1.0
                else:
                    print(f"  gain scout: {scout_ratio:.3f}x nominal "
                          "— windows scaled")
            relation_pix = (pixel_relation(data, anchor)
                            if anchor is not None else None)

            anchor_fit_ok = False
            for recipe_index, recipe in enumerate(recipes):
                if recipe_index > 0 and not anchor_fit_ok:
                    print(f"  {recipe['label']}: skipped (the CE fit "
                          "did not succeed)")
                    record_failure(pixel, recipe["label"],
                                   "skipped (CE fit failed)",
                                   gate_numbers)
                    continue
                group = LINE_GROUP_OF.get(recipe["label"].split("-")[0])
                energies = lines.get(group, [])
                prediction = None
                if (relation_pix is not None
                        and len(energies) == recipe["n_peaks"]):
                    prediction = (energies, relation_pix, 1.0,
                                  "two-anchor")
                elif (relation is not None
                        and len(energies) == recipe["n_peaks"]):
                    prediction = (energies, relation, scout_ratio,
                                  "nominal-scaled")
                plot_path = None
                if args.plot is not None:
                    plot_path = args.plot / (
                        f"Run{run}_seg{segment}_pix{pixel}"
                        f"_{recipe['label']}.png")
                result, bounds, config = run_recipe(
                    data, recipe, scout_ratio, plot_path, prediction)
                if result is None:
                    print(f"  {recipe['label']}: FAILED — no attempt "
                          "passed the quality check")
                    record_failure(
                        pixel, recipe["label"],
                        config.get("stage", "all attempts failed"),
                        gate_numbers,
                        attempts=config.get("attempts", ""),
                        best_redchi=config.get("best_redchi"),
                        figure=plot_path.name if plot_path else "")
                    failed += 1
                    continue
                fit_rows.append(fit_row(run, segment, pixel,
                                        args.isotope, recipe, bounds,
                                        result, config))
                fitted += 1
                if recipe_index == 0:
                    anchor_fit_ok = True
                accepted = config["attempt"]
                if config.get("window", "recipe window") != "recipe window":
                    accepted += f" ({config['window']})"
                print(f"  {recipe['label']}: reduced_chi2="
                      f"{result.redchi:.2f} accepted={accepted}")
                for line in centroid_report(result):
                    print(f"    {line}")

        out_csv = args.out / f"Run{run}_seg{segment}_fits.csv"
        with open(out_csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIT_FIELDS)
            writer.writeheader()
            writer.writerows(fit_rows)
        print(f"-> {out_csv}: {fitted} fit(s), {failed} failed, "
              f"{skipped} skipped")
        if args.plot is not None:
            update_failure_csv(args.plot / "fit_failures_summary.csv",
                               failure_rows, processed,
                               stages={"all attempts failed"})
            if args.failures_detail:
                update_failure_csv(
                    args.plot / f"Run{run}_seg{segment}_failures.csv",
                    failure_rows, processed)


if __name__ == "__main__":
    main()
