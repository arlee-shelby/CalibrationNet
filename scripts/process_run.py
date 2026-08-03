"""Run the whole pipeline for one run: ingest -> trap filter -> source
assignment -> spectrum fits -> ADC peak extraction -> calibrations.

Each stage is idempotent and checked before it runs, so this script can
be re-run safely at any point and it continues where things stand:

1. **Ingest** run metadata + segments (slow controls + motion archive).
   Needs the slow-controls tunnel — wrap with scripts/with_sc_tunnel.sh,
   which also works inside sbatch on the cluster. If the run is already
   in the database and the tunnel is down, this stage degrades to a
   warning.
2. **Trap filter**: if any segment lacks outputs, on the cluster
   (sbatch available) the SLURM array is submitted via
   scripts/submit_trap_filter.sh and THIS SCRIPT EXITS — re-run it when
   the array completes (scripts/pending_segments.py --summary tells
   you). Off the cluster it exits with instructions.
3. **Source assignment**: skipped when the run's pixels already carry
   sources; otherwise runs scripts/assign_sources.py and applies the
   non-CHECK rows (CHECK pixels stay unassigned — their fits are simply
   skipped downstream; review them by hand later).
4. **fit_spectra / extract_adc_peaks / calibrate** per segment.

    ./scripts/with_sc_tunnel.sh python scripts/process_run.py 9402 \\
        --h5-dir /storage/ideas/is-ajezghani3-0/TempCal/
    python scripts/process_run.py 8718 --skip-ingest   # already ingested
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select

from calibrationnet.db import get_session
from calibrationnet.models import (Isotope, IsotopeDecayEnergy, Pixel, Run,
                                   RunPixel, RunSegment, Source,
                                   SourceInstallation)


def stage(title):
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def preflight(session, run_number) -> list:
    """Everything the pipeline assumes is already in the database.
    Returns a list of blocking problems (empty = good to go)."""
    problems = []
    counts = {
        "pixels (scripts/seed_pixels.py)": Pixel,
        "sources (scripts/seed_sources.py)": Source,
        "isotope decay lines (scripts/seed_decay_energies.py)":
            IsotopeDecayEnergy,
        "isotopes (scripts/seed_sources.py)": Isotope,
    }
    for what, model in counts.items():
        n = session.execute(
            select(func.count()).select_from(model)).scalar()
        if n == 0:
            problems.append(f"no {what} seeded")
    run = session.get(Run, run_number)
    if run is not None and run.start_time is not None:
        day = run.start_time.date()
        covered = session.execute(
            select(func.count()).select_from(SourceInstallation)
            .where(SourceInstallation.installed_on <= day,
                   (SourceInstallation.removed_on.is_(None))
                   | (SourceInstallation.removed_on > day))).scalar()
        if covered == 0:
            problems.append(
                f"no source installation covers {day} — update "
                "data/source_installations.csv and re-seed "
                "(scripts/seed_source_installations.py)")
    return problems


def run_script(argv, **kwargs) -> int:
    print("$ " + " ".join(str(a) for a in argv))
    return subprocess.call([sys.executable, *argv], **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_number", type=int)
    parser.add_argument("--h5-dir", default=None,
                        help="directory with the run's .h5 files (needed "
                             "only when trap filtering must be submitted)")
    parser.add_argument("--tf-label", default="nabpy-standard")
    parser.add_argument("--rt", type=int, default=1250,
                        help="trap rise time (4 ns bins)")
    parser.add_argument("--ft", type=int, default=50,
                        help="trap flat top (4 ns bins)")
    parser.add_argument("--fall", type=int, default=1250,
                        help="trap fall time (4 ns bins)")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="skip the slow-controls ingest stage")
    parser.add_argument("--min-dwell", type=float, default=None,
                        metavar="MINUTES",
                        help="passed to ingest_run.py for short-dwell runs")
    parser.add_argument("--plot", type=Path, default=None, metavar="DIR",
                        help="save fit + calibration QA figures here")
    args = parser.parse_args()
    run_number = args.run_number

    # 1. Ingest ---------------------------------------------------------
    stage(f"ingest run {run_number}")
    if args.skip_ingest:
        print("skipped (--skip-ingest)")
    else:
        argv = ["scripts/ingest_run.py", str(run_number)]
        if args.min_dwell is not None:
            argv += ["--min-dwell", str(args.min_dwell)]
        if run_script(argv) != 0:
            with get_session() as session:
                exists = session.get(Run, run_number) is not None
            if not exists:
                raise SystemExit(
                    "ingest failed and the run is not in the database — "
                    "is the slow-controls tunnel up? "
                    "(./scripts/with_sc_tunnel.sh ...)")
            print("WARNING: ingest failed (tunnel down?) but the run is "
                  "already in the database — continuing with stored data.")

    with get_session() as session:
        segments = session.scalars(
            select(RunSegment.segment_index)
            .where(RunSegment.run_number == run_number)
            .order_by(RunSegment.segment_index)).all()
        problems = preflight(session, run_number)
    if not segments:
        raise SystemExit(f"run {run_number} has no segments — ingest it "
                         "first (slow-controls tunnel required).")
    print(f"run {run_number}: {len(segments)} segment(s)")
    if problems:
        raise SystemExit("preflight failed:\n  - " + "\n  - ".join(problems))

    # 2. Trap filter ----------------------------------------------------
    stage("trap filter outputs")
    pending = subprocess.run(
        [sys.executable, "scripts/pending_segments.py",
         "--runs", str(run_number), "--label", args.tf_label,
         "-rt", str(args.rt), "-ft", str(args.ft), "-fall", str(args.fall)],
        capture_output=True, text=True)
    missing = [line for line in pending.stdout.splitlines() if line.strip()]
    if missing:
        print(f"{len(missing)} segment(s) lack '{args.tf_label}' outputs "
              f"at rt={args.rt} ft={args.ft} fall={args.fall}.")
        if shutil.which("sbatch") and args.h5_dir:
            run_list = Path(f"run_{run_number}.txt")
            run_list.write_text(f"{run_number}\n")
            code = subprocess.call(["./scripts/submit_trap_filter.sh",
                                    str(run_list), args.h5_dir,
                                    str(args.rt), str(args.ft),
                                    str(args.fall), "singles",
                                    args.tf_label])
            raise SystemExit(
                code or f"trap filter array submitted — re-run this "
                        f"script when it completes "
                        f"(python scripts/pending_segments.py --runs "
                        f"{run_number} --summary).")
        raise SystemExit(
            "not on the cluster (or --h5-dir not given): submit the trap "
            f"filter there first:\n  echo {run_number} > run.txt && "
            f"./scripts/submit_trap_filter.sh run.txt <h5_dir>")
    print("all segments have outputs")

    # 2.5 Board channels: the cluster trap task now fills them from the
    # h5 map as it ingests; runs filtered before that change may lack
    # them. Informational only — nothing downstream of here needs them.
    with get_session() as session:
        n_bc = session.execute(
            select(func.count()).select_from(RunPixel)
            .where(RunPixel.run_number == run_number,
                   RunPixel.board_channel.is_not(None))).scalar()
    if n_bc == 0:
        print(f"NOTE: run {run_number} has no board channels recorded — "
              "on the cluster: python scripts/ingest_board_channels.py "
              f"{run_number} <any subrun .h5>")
    else:
        print(f"board channels recorded for {n_bc} run pixels")

    # 3. Source assignment ----------------------------------------------
    stage("source assignment")
    with get_session() as session:
        unassigned = session.scalars(
            select(RunPixel).where(RunPixel.run_number == run_number,
                                   RunPixel.source_id.is_(None))).all()
        assigned = session.scalars(
            select(RunPixel).where(RunPixel.run_number == run_number,
                                   RunPixel.source_id.is_not(None))).all()
    if assigned and not unassigned:
        print(f"already assigned ({len(assigned)} run pixels)")
    else:
        # Writes the review CSV, then applies its non-CHECK rows; CHECK
        # pixels stay unassigned (their fits are skipped downstream) and
        # can be reviewed by hand later.
        if run_script(["scripts/assign_sources.py",
                       "--label", args.tf_label]) != 0:
            raise SystemExit("source assignment failed")
        if run_script(["scripts/assign_sources.py", "--apply",
                       "--label", args.tf_label]) != 0:
            raise SystemExit("source assignment apply failed")

    # 4-6. Fit, extract, calibrate — per segment ------------------------
    for script, extra in (("scripts/fit_spectra.py",
                           ["--plot", str(args.plot)] if args.plot else []),
                          ("scripts/extract_adc_peaks.py", []),
                          ("scripts/calibrate.py",
                           ["--plot", str(args.plot)] if args.plot else [])):
        stage(Path(script).stem)
        for segment in segments:
            code = run_script([script, "--run", str(run_number),
                               "--segment", str(segment),
                               "--tf-label", args.tf_label, *extra])
            if code != 0:
                raise SystemExit(f"{script} failed on segment {segment}")

    stage("done")
    print(f"run {run_number} processed end to end "
          f"({len(segments)} segment(s)). Calibrations are queryable via "
          "calibrationnet.queries.calibrations_for_pixel.")


if __name__ == "__main__":
    main()
