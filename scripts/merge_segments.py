"""Merge several recorded dwells of one run into ONE derived segment, so a
raster whose dwells are each too short for the fit gate can still give
every pixel a calibration.

The merge is per pixel: the derived segment's filter output for a pixel is
the concatenation of that pixel's energies over the member dwells. That is
physically clean for calibration — same electronics, same isotope, same
run conditions; where the source sat within the pixel changes how many
counts it collected, not where its lines fall — and it is how a sweep
with steps smaller than a pixel (ex: run 10075, 5 min dwells 0.25 inch
apart) becomes a calibration of both faces instead of a positioning run.

The derived segment gets a NEGATIVE segment index (default -1): no recorded
dwell can be negative, so the sign itself says "merged". Its time span is
the members' span, its position is NULL (a merged dwell has no single
position), and its outputs carry their own trap label (default
"<label>-merged"), so nothing that asks for the recorded label ever sees
them. The pipeline (process_run.py, pending_segments.py) skips negative
segments; source assignment and the position planner are label-scoped and
never meet them. Fit, extract and calibrate the merged segment directly:

    python scripts/merge_segments.py --run 10075 --segments 0-80 --merged-label nabpy-standard-raster --dry-run
    python scripts/merge_segments.py --run 10075 --segments 0-80 --merged-label nabpy-standard-raster
    python scripts/fit_spectra.py       --run 10075 --segment -1 --tf-label nabpy-standard-raster --plot fit_plots/run_10075
    python scripts/extract_adc_peaks.py --run 10075 --segment -1 --tf-label nabpy-standard-raster
    python scripts/calibrate.py         --run 10075 --segment -1 --tf-label nabpy-standard-raster --plot fit_plots/run_10075

The concatenation happens inside the database (array_agg over unnest), so
nothing bulky crosses the tunnel. Re-running with --force replaces an
existing merged segment and everything built on it.
"""

import argparse

from sqlalchemy import select, text

from calibrationnet.db import get_session
from calibrationnet.models import RunSegment, TrapFilterOutput, RunPixel


def parse_segments(spec: str) -> list:
    """'0-80' or '0-40,42,45-80' -> sorted list of indices."""
    indices = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part[1:]:
            low, high = part.split("-", 1)
            indices.update(range(int(low), int(high) + 1))
        elif part:
            indices.add(int(part))
    return sorted(indices)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--segments", required=True, help="member dwells, ex: 0-80 or 0-40,42,45-80 (recorded indices only)")
    parser.add_argument("--label", default="nabpy-standard", help="trap label of the members' outputs")
    parser.add_argument("--merged-label", default=None, help="trap label for the merged outputs (default <label>-merged)")
    parser.add_argument("--index", type=int, default=-1, help="segment index of the merged segment (must be negative)")
    parser.add_argument("--note", default=None, help="note stored on the merged segment (default describes the members)")
    parser.add_argument("--force", action="store_true", help="replace an existing merged segment with this index, and everything built on it")
    parser.add_argument("--dry-run", action="store_true", help="report what would be merged, write nothing")
    args = parser.parse_args()
    merged_label = args.merged_label or f"{args.label}-merged"
    members = parse_segments(args.segments)
    if args.index >= 0:
        raise SystemExit("--index must be negative: the sign is what marks a merged segment")
    if any(index < 0 for index in members):
        raise SystemExit("member segments must be recorded dwells (non-negative indices)")

    with get_session() as session:
        rows = session.scalars(select(RunSegment).where(RunSegment.run_number == args.run,
                                                        RunSegment.segment_index.in_(members))
                               .order_by(RunSegment.segment_index)).all()
        found = {segment.segment_index for segment in rows}
        missing = [index for index in members if index not in found]
        if missing:
            raise SystemExit(f"run {args.run} has no segment(s) {missing[:10]}{'...' if len(missing) > 10 else ''}")

        # the members' outputs: one trap setting, counted per segment
        settings = session.execute(text("""
            select t.trap_rise, t.trap_flattop, t.trap_falltime, count(distinct rp.segment_index) as segments,
                   count(distinct rp.pixel_number) as pixels, sum(array_length(t.energies, 1)) as waveforms
            from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
            where rp.run_number = :run and rp.segment_index = any(:members) and t.label = :label
            group by 1, 2, 3"""), {"run": args.run, "members": members, "label": args.label}).all()
        if not settings:
            raise SystemExit(f"no '{args.label}' outputs for run {args.run} segments {args.segments}")
        if len(settings) > 1:
            raise SystemExit(f"the members hold more than one trap setting under '{args.label}': {settings}")
        rise, flattop, falltime, with_outputs, pixels, waveforms = settings[0]
        if with_outputs < len(members):
            print(f"note: {len(members) - with_outputs} member segment(s) have no '{args.label}' outputs and contribute nothing")

        start = min(segment.start_time for segment in rows)
        end = max(segment.end_time for segment in rows)
        conventions = {segment.position_convention for segment in rows}
        note = args.note or (f"merged from recorded dwells {args.segments} ({len(members)} segments, {with_outputs} with "
                             f"'{args.label}' outputs); per-pixel energies concatenated by scripts/merge_segments.py")
        print(f"run {args.run}: merge segments {args.segments} -> segment {args.index}, label {merged_label!r}")
        print(f"  span {start} -> {end}; trap rt={rise:g} ft={flattop:g} fall={falltime:g}; {pixels} pixels, {int(waveforms):,} waveforms")
        if args.dry_run:
            print("dry run — nothing written")
            return

        existing = session.get(RunSegment, (args.run, args.index))
        if existing is not None:
            dependents = session.execute(text("""
                select (select count(*) from spectrum_fits f join trap_filter_outputs t on t.id = f.trap_filter_output_id
                        join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and rp.segment_index = :index),
                       (select count(*) from calibrations c join run_pixels rp on rp.id = c.run_pixel_id
                        where rp.run_number = :run and rp.segment_index = :index)"""), {"run": args.run, "index": args.index}).one()
            if not args.force:
                raise SystemExit(f"segment {args.index} already exists for run {args.run} ({dependents[0]} fits, {dependents[1]} calibrations "
                                 "built on it) — re-run with --force to replace it")
            scope = {"run": args.run, "index": args.index}
            for statement in (
                "delete from calibration_points where calibration_id in (select c.id from calibrations c join run_pixels rp on rp.id = c.run_pixel_id where rp.run_number = :run and rp.segment_index = :index)",
                "delete from calibrations where run_pixel_id in (select id from run_pixels where run_number = :run and segment_index = :index)",
                "delete from adc_peaks where spectrum_fit_id in (select f.id from spectrum_fits f join trap_filter_outputs t on t.id = f.trap_filter_output_id join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and rp.segment_index = :index)",
                "delete from spectrum_fits where trap_filter_output_id in (select t.id from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id where rp.run_number = :run and rp.segment_index = :index)",
                "delete from trap_filter_outputs where run_pixel_id in (select id from run_pixels where run_number = :run and segment_index = :index)",
                "delete from run_pixels where run_number = :run and segment_index = :index",
            ):
                session.execute(text(statement), scope)
            session.delete(existing)
            session.flush()
            print(f"  replaced the existing segment {args.index} ({dependents[0]} fits, {dependents[1]} calibrations removed)")

        merged = RunSegment(run_number=args.run, segment_index=args.index, start_time=start, end_time=end,
                            linear_position=None, horizontal_position=None,
                            position_convention=conventions.pop() if len(conventions) == 1 else None, notes=note)
        session.add(merged)
        session.flush()

        pixel_numbers = [row[0] for row in session.execute(text("""
            select distinct rp.pixel_number from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
            where rp.run_number = :run and rp.segment_index = any(:members) and t.label = :label order by 1"""),
            {"run": args.run, "members": members, "label": args.label})]
        source_file = f"merged: run {args.run} segments {args.segments} ({with_outputs} dwells), label {args.label}"[:255]
        for pixel_number in pixel_numbers:
            run_pixel = RunPixel(run_number=args.run, segment_index=args.index, pixel_number=pixel_number)
            session.add(run_pixel)
            session.flush()
            # concatenate inside the database: nothing bulky crosses the tunnel
            session.execute(text("""
                insert into trap_filter_outputs (run_pixel_id, trap_rise, trap_flattop, trap_falltime, label, source_file, energies)
                select :run_pixel_id, :rise, :flattop, :falltime, :merged_label, :source_file,
                       (select array_agg(e) from (
                            select unnest(t.energies) as e from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
                            where rp.run_number = :run and rp.segment_index = any(:members) and rp.pixel_number = :pixel and t.label = :label) x)"""),
                {"run_pixel_id": run_pixel.id, "rise": rise, "flattop": flattop, "falltime": falltime, "merged_label": merged_label,
                 "source_file": source_file, "run": args.run, "members": members, "pixel": pixel_number, "label": args.label})
        session.commit()
        stored = session.execute(text("""select count(*), sum(array_length(t.energies, 1)) from trap_filter_outputs t join run_pixels rp on rp.id = t.run_pixel_id
            where rp.run_number = :run and rp.segment_index = :index"""), {"run": args.run, "index": args.index}).one()
        print(f"  stored segment {args.index}: {stored[0]} pixel outputs, {int(stored[1]):,} waveforms, label {merged_label!r}")
        print(f"next: python scripts/fit_spectra.py --run {args.run} --segment {args.index} --tf-label {merged_label} --plot fit_plots/run_{args.run}")


if __name__ == "__main__":
    main()
