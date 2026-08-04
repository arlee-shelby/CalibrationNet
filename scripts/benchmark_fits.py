"""Benchmark the live fit code against the frozen reference copy.

calibrationnet/fit_functions_reference.py is a byte-identical copy of
the original fitting module (md5 recorded below) and must NEVER change.
This script is the gate for any change to the changeable functions
(get_initial_peak_parameters, do_fit, get_fit — see
docs/pipeline_roadmap.md for the policy):

1. Integrity checks (always run):
   - the reference file still has the recorded md5;
   - the seven FROZEN functions in the live module are source-identical
     to the reference (gaussian, background, lower_exponential,
     step_function, fit_model, residual_function,
     get_histogram_data_uncertainty) — these encode the physics and are
     not to be edited at all;
   - changeable functions that differ from the reference are listed,
     informationally.
2. Fit comparison (unless --check-only): run both modules' get_fit over
   the requested run pixels (same recipes as production fitting) and
   compare centroids, widths, errors, reduced chi2, and success. The
   headline number per fit is the worst centroid PULL:
   |cen_live - cen_ref| / stderr_ref — how far the change moved a peak
   in units of its own uncertainty.

Exit status is non-zero if any integrity check fails, any fit's success
flag changes, or any pull exceeds --max-pull.

    python scripts/benchmark_fits.py --check-only
    python scripts/benchmark_fits.py --runs 8622 9327
    python scripts/benchmark_fits.py --runs 8622 --pixels 60
"""

import argparse
import hashlib
import inspect
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

import calibrationnet.fit_functions as live
import calibrationnet.fit_functions_reference as reference
from calibrationnet.db import get_session
from calibrationnet.fit_recipes import RECIPES
from calibrationnet.models import RunPixel, TrapFilterOutput

REFERENCE_MD5 = "52c85de2409e284a8cdaf303369b82a9"
FROZEN = ["gaussian", "background", "lower_exponential", "step_function",
          "fit_model", "residual_function", "get_histogram_data_uncertainty"]
CHANGEABLE = ["get_initial_peak_parameters", "do_fit", "get_fit"]

# The reference test pixels (docs/pipeline_roadmap.md): every fitting
# change is judged by re-fitting THIS fixed list and comparing against
# the recorded before-picture — successes must stay numerically
# identical, failures should become successes. Known-good pixels and
# low-gain cases supplied by AS (2026-08-04); the full low-gain
# registry — reference only, low gain is NOT stationary — is
# data/known_low_gain_pixels.csv.
REFERENCE_PIXELS = [
    # gold standard: clean fits, high statistics
    (8622, 60), (8622, 1052),
    (8631, 1067), (8631, 21),
    (8637, 77), (8637, 1087), (8637, 1091),
    # problem regimes
    (8718, 99),    # weak 566 line: find_peaks misplaces peak 3
    (8718, 95),    # low gain (0.44x): scout fires, pairs merge
    (8626, 91),    # low gain, UDET
    (8715, 1043),  # low gain, LDET
    (8682, 1028),  # low gain + Cd source (activates with Cd recipes, 4.4)
    (8622, 1051),  # LDET blend at standard trap: scrambled CE fit
    (8718, 84),    # skirt pixel: threshold shoulder in the Auger window
]


def integrity_check() -> bool:
    """Reference untouched + frozen functions unedited. Returns ok."""
    ok = True
    ref_md5 = hashlib.md5(
        Path(reference.__file__).read_bytes()).hexdigest()
    if ref_md5 != REFERENCE_MD5:
        print(f"FAIL: fit_functions_reference.py md5 {ref_md5} != recorded "
              f"{REFERENCE_MD5} — the reference must never change; restore "
              "it from git.")
        ok = False
    for name in FROZEN:
        if (inspect.getsource(getattr(live, name))
                != inspect.getsource(getattr(reference, name))):
            print(f"FAIL: frozen function {name}() differs from the "
                  "reference — these encode the physics and must not be "
                  "edited (docs/pipeline_roadmap.md).")
            ok = False
    if ok:
        print(f"integrity OK: reference md5 matches; all {len(FROZEN)} "
              "frozen functions identical")
    changed = [n for n in CHANGEABLE
               if (inspect.getsource(getattr(live, n))
                   != inspect.getsource(getattr(reference, n)))]
    if changed:
        print(f"changeable functions differing from reference: {changed}")
    else:
        print("changeable functions: no differences (fit results should "
              "be identical)")
    return ok


def compare(res_live, res_ref):
    """Worst centroid/width pulls and metric shifts between two fits."""
    def pulls(prefix):
        worst = 0.0
        for name in res_ref.params:
            if not name.startswith(prefix):
                continue
            ref_err = res_ref.params[name].stderr
            if not ref_err:
                continue
            delta = abs(res_live.params[name].value
                        - res_ref.params[name].value)
            worst = max(worst, delta / ref_err)
        return worst

    return {
        "cen_pull": pulls("cen"),
        "sig_pull": pulls("sig"),
        "d_redchi": res_live.redchi - res_ref.redchi,
        "success": (bool(res_live.success), bool(res_ref.success)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-only", action="store_true",
                        help="run only the integrity checks (no fits)")
    parser.add_argument("--reference-pixels", action="store_true",
                        help="re-fit the fixed reference test pixel list "
                             "(known-good pixels + known problem cases)")
    parser.add_argument("--runs", type=int, nargs="+", default=None)
    parser.add_argument("--segment", type=int, default=0)
    parser.add_argument("--pixels", type=int, nargs="+", default=None)
    parser.add_argument("--tf-label", default="nabpy-standard")
    parser.add_argument("--limit", type=int, default=None,
                        help="benchmark at most this many run pixels")
    parser.add_argument("--max-pull", type=float, default=0.5,
                        help="fail if any centroid moved by more than this "
                             "many reference standard errors (default 0.5)")
    args = parser.parse_args()

    if not integrity_check():
        sys.exit(1)
    if args.check_only:
        return
    if not args.runs and not args.reference_pixels:
        parser.error("--runs or --reference-pixels is required unless --check-only")

    with get_session() as session:
        query = (
            select(RunPixel, TrapFilterOutput)
            .join(TrapFilterOutput,
                  TrapFilterOutput.run_pixel_id == RunPixel.id)
            .where(RunPixel.segment_index == args.segment,
                   TrapFilterOutput.label == args.tf_label)
            .order_by(RunPixel.run_number, RunPixel.pixel_number)
        )
        if args.reference_pixels:
            from sqlalchemy import or_, and_
            query = query.where(or_(*[
                and_(RunPixel.run_number == run,
                     RunPixel.pixel_number == pixel)
                for run, pixel in REFERENCE_PIXELS]))
        else:
            query = query.where(RunPixel.run_number.in_(args.runs))
            if args.pixels:
                query = query.where(RunPixel.pixel_number.in_(args.pixels))
        pairs = session.execute(query).all()

        rows, failed = [], False
        done = 0
        for rp, tfo in pairs:
            if args.limit is not None and done >= args.limit:
                break
            isotope = rp.source and rp.source.isotope.name
            recipes = RECIPES.get(isotope)
            if recipes is None:
                continue
            done += 1
            data = np.asarray(tfo.energies)
            for recipe in recipes:
                fit_args = (data, recipe["bounds"][0], recipe["bounds"][1],
                            recipe["peak_finder"], recipe["n_peaks"],
                            recipe["widths"])
                try:
                    res_ref = reference.get_fit(*fit_args)
                except Exception as exc:
                    print(f"run {rp.run_number} pixel {rp.pixel_number} "
                          f"{recipe['label']}: reference fit FAILED ({exc})")
                    continue
                try:
                    res_live = live.get_fit(*fit_args)
                except Exception as exc:
                    print(f"run {rp.run_number} pixel {rp.pixel_number} "
                          f"{recipe['label']}: live fit FAILED where "
                          f"reference succeeded ({exc})")
                    failed = True
                    continue
                row = compare(res_live, res_ref)
                bad = (row["cen_pull"] > args.max_pull
                       or row["success"][0] != row["success"][1])
                failed = failed or bad
                rows.append(row)
                print(f"run {rp.run_number} pixel {rp.pixel_number:>4} "
                      f"{recipe['label']:>12}: "
                      f"cen pull {row['cen_pull']:.3f}  "
                      f"sig pull {row['sig_pull']:.3f}  "
                      f"d(redchi) {row['d_redchi']:+.4f}  "
                      f"success {row['success'][1]}->{row['success'][0]}"
                      f"{'  <-- FAIL' if bad else ''}")

        if not rows:
            raise SystemExit("no benchmarkable fits found — check --runs/"
                             "--pixels and that sources are assigned.")
        cen = [r["cen_pull"] for r in rows]
        print(f"\n{len(rows)} fits over {done} run pixel(s): "
              f"worst cen pull {max(cen):.3f}, mean {np.mean(cen):.3f} "
              f"(threshold {args.max_pull})")
        if failed:
            print("BENCHMARK FAILED — do not adopt this change as-is.")
            sys.exit(1)
        print("BENCHMARK PASSED")


if __name__ == "__main__":
    main()
