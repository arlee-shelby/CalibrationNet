"""Remove one run's stored trap filter outputs (and the run_pixels that
then carry nothing), so the run can be refiltered from scratch.

Needed when stored outputs are WRONG rather than missing — first case:
run 10081, whose pixels were mislabeled by nabPy's DAQ 2.0 reader
(2026-10-16). Re-running the trap stage alone would not help there,
because it only fills segments that lack outputs.

Refuses to delete outputs that have spectrum fits or calibrations
hanging off them unless --force is given (those rows go too, since they
were computed from the wrong pixels). Prints what it would remove and
does nothing without --yes.

    python scripts/remove_filter_outputs.py --run 10081 --label nabpy-standard          # report only
    python scripts/remove_filter_outputs.py --run 10081 --label nabpy-standard --yes    # delete
"""

import argparse

from sqlalchemy import text

from calibrationnet.db import get_session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--label", default="nabpy-standard", help="trap filter label whose outputs to remove")
    parser.add_argument("--force", action="store_true", help="also remove fits and calibrations built on these outputs")
    parser.add_argument("--yes", action="store_true", help="actually delete (default: report only)")
    args = parser.parse_args()
    scope = {"run": args.run, "label": args.label}

    with get_session() as session:
        outputs = session.execute(text("""
            select count(*) from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
            where rp.run_number = :run and t.label = :label"""), scope).scalar()
        fits = session.execute(text("""
            select count(*) from spectrum_fits f join trap_filter_outputs t on t.id = f.trap_filter_output_id
            join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and t.label = :label"""), scope).scalar()
        calibrations = session.execute(text("""
            select count(*) from calibrations c join trap_filter_outputs t on t.id = c.trap_filter_output_id
            join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and t.label = :label"""), scope).scalar()
        print(f"run {args.run}, label {args.label!r}: {outputs} filter output(s), {fits} fit(s), {calibrations} calibration(s) built on them")
        if outputs == 0:
            print("nothing to remove")
            return
        if (fits or calibrations) and not args.force:
            raise SystemExit("fits/calibrations hang off these outputs — re-run with --force to remove them too, "
                             "or leave the run as it is")
        if not args.yes:
            print("report only — add --yes to delete")
            return

        # dependents first (adc_peaks and calibration_points cascade from their parents in the ORM,
        # here they are removed explicitly so the SQL path needs no ORM loads)
        removed = {}
        removed["calibration_points"] = session.execute(text("""
            delete from calibration_points where calibration_id in (
                select c.id from calibrations c join trap_filter_outputs t on t.id = c.trap_filter_output_id
                join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and t.label = :label)"""), scope).rowcount
        removed["calibrations"] = session.execute(text("""
            delete from calibrations where trap_filter_output_id in (
                select t.id from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
                where rp.run_number = :run and t.label = :label)"""), scope).rowcount
        removed["adc_peaks"] = session.execute(text("""
            delete from adc_peaks where spectrum_fit_id in (
                select f.id from spectrum_fits f join trap_filter_outputs t on t.id = f.trap_filter_output_id
                join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and t.label = :label)"""), scope).rowcount
        removed["spectrum_fits"] = session.execute(text("""
            delete from spectrum_fits where trap_filter_output_id in (
                select t.id from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
                where rp.run_number = :run and t.label = :label)"""), scope).rowcount
        removed["trap_filter_outputs"] = session.execute(text("""
            delete from trap_filter_outputs where id in (
                select t.id from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
                where rp.run_number = :run and t.label = :label)"""), scope).rowcount
        # run_pixels that now carry nothing at all are removed too; the next trap ingest recreates them
        removed["run_pixels"] = session.execute(text("""
            delete from run_pixels rp where rp.run_number = :run
              and rp.source_id is null and rp.board_channel is null
              and not exists (select 1 from trap_filter_outputs t where t.run_pixel_id = rp.id)
              and not exists (select 1 from calibrations c where c.run_pixel_id = rp.id)"""), scope).rowcount
        session.commit()
        print("removed:", ", ".join(f"{n} {table}" for table, n in removed.items() if n))
        print(f"run {args.run} is ready to be refiltered: ./scripts/process_run.sh {args.run} <h5 path>")


if __name__ == "__main__":
    main()
