"""Calibrate pixels from offline fit results and a supplied set of keV
values — the offline counterpart of scripts/calibrate.py.

Reads the fits CSVs that scripts/offline/fit_spectra.py wrote, pairs
each pixel's fitted centroids with the given line energies (ascending
order, then confirmed against the two-anchor CE line within
--tolerance-kev — the same rule extraction uses), and fits linear and
quadratic ADC->keV with uncertainties (lmfit, scale_covar=False — the
project-wide convention). The keV values are an INPUT file, so
simulation-corrected (source-specific) energies plug straight in:

    label,energy_kev,energy_err_kev
    CE 482,481.6935,0.0021
    CE 554,553.8372,
    ...
    Auger 56,56.03,
    Auger 68,68.18,

Outputs: <out>/calibrations.csv (one row per pixel per fit type) and a
QA figure per pixel. Results live in FILES ONLY — the database remains
the record.

    python scripts/offline/calibrate.py offline_output/fits \\
        --kev simulated_bi207_kev.csv
"""

import argparse
import csv
from collections import defaultdict, namedtuple
from pathlib import Path

from calibrationnet.calibration import fit_calibration, plot_calibration
from calibrationnet.fit_recipes import SCOUT_ANCHORS

RunPixelLike = namedtuple("RunPixelLike",
                          "run_number segment_index pixel_number")

CAL_FIELDS = ["run", "segment", "pixel", "type", "n_points",
              "constant_kev", "constant_err", "gain_kev_per_adc",
              "gain_err", "quadratic", "quadratic_err",
              "chi2", "ndf", "reduced_chi2", "lines_used"]


def read_kev(path):
    """{'CE': [(label, kev, err)...ascending], 'Auger': [...]}"""
    groups = defaultdict(list)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            err = float(row["energy_err_kev"]) if row.get(
                "energy_err_kev") else 0.0
            groups[row["label"].split()[0]].append(
                (row["label"], float(row["energy_kev"]), err))
    return {g: sorted(v, key=lambda x: x[1]) for g, v in groups.items()}


def peaks_from_row(row, prefix_count):
    """[(cen, cen_err)] ascending from one fits-CSV row."""
    peaks = []
    for i in range(1, prefix_count + 1):
        if row[f"cen{i}"]:
            peaks.append((float(row[f"cen{i}"]),
                          float(row[f"cen_err{i}"] or 0.0)))
    return sorted(peaks)


def match_pixel(ce_row, auger_row, kev, tolerance_kev, anchor_energies):
    """(points, lines_used): fitted centroids paired with line energies
    by ascending order, each confirmed by the two-anchor CE line."""
    ce_peaks = peaks_from_row(ce_row, 6)
    ce_lines = kev.get("CE", [])
    if len(ce_peaks) != len(ce_lines):
        return [], []
    # The two CE anchors: the lowest-energy line and the line nearest
    # the isotope's strong anchor energy (Bi-207: the 976 K line) —
    # exactly extraction's rule.
    lo = 0
    hi = min(range(len(ce_lines)),
             key=lambda i: abs(ce_lines[i][1] - anchor_energies[1]))
    gain = ((ce_lines[hi][1] - ce_lines[lo][1])
            / (ce_peaks[hi][0] - ce_peaks[lo][0]))
    offset = ce_lines[lo][1] - gain * ce_peaks[lo][0]

    points, lines_used = [], []
    pairs = list(zip(ce_peaks, ce_lines))
    if auger_row is not None:
        pairs += list(zip(peaks_from_row(auger_row, 2),
                          kev.get("Auger", [])))
    for (cen, cen_err), (label, energy, energy_err) in pairs:
        implied = offset + gain * cen
        if abs(implied - energy) > tolerance_kev:
            print(f"    {label}: implied {implied:.1f} keV vs "
                  f"{energy:.1f} — outside {tolerance_kev} keV, dropped")
            continue
        points.append((cen, cen_err, energy, energy_err))
        lines_used.append(label)
    return points, lines_used


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+",
                        help="fits CSV file(s) and/or folder(s) from "
                             "scripts/offline/fit_spectra.py")
    parser.add_argument("--kev", type=Path, required=True,
                        help="CSV of line energies to calibrate against "
                             "(label,energy_kev[,energy_err_kev]) — e.g. "
                             "simulation-corrected Bi-207 values")
    parser.add_argument("--tolerance-kev", type=float, default=5.0)
    parser.add_argument("--min-points", type=int, default=3,
                        help="matched points required for the linear "
                             "fit (quadratic needs one more)")
    parser.add_argument("--out", type=Path,
                        default=Path("offline_output/calibrations"))
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    kev = read_kev(args.kev)
    anchors = SCOUT_ANCHORS["Bi-207"]["anchor_energies"]

    files = []
    for item in args.inputs:
        p = Path(item)
        files += sorted(p.glob("*_fits.csv")) if p.is_dir() else [p]
    if not files:
        raise SystemExit("no fits CSVs found.")

    cal_rows = []
    for path in files:
        by_pixel = defaultdict(dict)
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                key = (int(row["run"]), int(row["segment"]),
                       int(row["pixel"]))
                by_pixel[key][row["label"]] = row

        for (run, segment, pixel), fits in sorted(by_pixel.items()):
            ce = fits.get("ce-6peak")
            if ce is None:
                continue
            print(f"run {run} seg {segment} pixel {pixel}:")
            points, lines_used = match_pixel(
                ce, fits.get("auger-2peak"), kev,
                args.tolerance_kev, anchors)
            if len(points) < args.min_points:
                print(f"  only {len(points)} matched point(s) — "
                      f"needs >= {args.min_points}, skipped")
                continue
            results = {}
            results["linear"] = fit_calibration(points, quadratic=False)
            if len(points) >= args.min_points + 1:
                results["quadratic"] = fit_calibration(points,
                                                       quadratic=True)
            for cal_type, result in results.items():
                p = result.params
                quad = p.get("quadratic")
                cal_rows.append({
                    "run": run, "segment": segment, "pixel": pixel,
                    "type": cal_type, "n_points": len(points),
                    "constant_kev": f"{p['constant'].value:.4f}",
                    "constant_err": f"{p['constant'].stderr:.4f}"
                                    if p['constant'].stderr else "",
                    "gain_kev_per_adc": f"{p['linear'].value:.6f}",
                    "gain_err": f"{p['linear'].stderr:.6f}"
                                if p['linear'].stderr else "",
                    "quadratic": f"{quad.value:.3e}" if quad else "",
                    "quadratic_err": (f"{quad.stderr:.3e}"
                                      if quad and quad.stderr else ""),
                    "chi2": f"{result.chisqr:.4f}", "ndf": result.nfree,
                    "reduced_chi2": f"{result.redchi:.4f}",
                    "lines_used": ";".join(lines_used),
                })
                print(f"  {cal_type}: gain="
                      f"{p['linear'].value:.5f} keV/ADC, constant="
                      f"{p['constant'].value:+.2f} keV, reduced_chi2="
                      f"{result.redchi:.2f} ({len(points)} points)")
            if not args.no_plot:
                plot_calibration(points, results,
                                 RunPixelLike(run, segment, pixel),
                                 "offline", args.out)

    out_csv = args.out / "calibrations.csv"
    with open(out_csv, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAL_FIELDS)
        writer.writeheader()
        writer.writerows(cal_rows)
    print(f"\n-> {out_csv}: {len(cal_rows)} calibration(s)")


if __name__ == "__main__":
    main()
