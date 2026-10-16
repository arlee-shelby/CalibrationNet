"""Compare nabPy's per-waveform 'pixel' header with the file's own
Parameters/BoardChannelToPixelMap, channel by channel.

The filter (calibrationnet.pipeline.waveforms.segment_energies) assigns
pixels from the file's map by board channel since 2026-10-16, after
nabPy's DAQ 2.0 reader was found to mislabel most channels of run 10081.
This check says, for one file, how many channels the two assignments
disagree on — 0 for every old-DAQ file (the change is behavior-neutral
there), many for a DAQ 2.0 file read by an affected nabPy.

    python scripts/check_pixel_map.py /path/to/Run9521_0.h5
    python scripts/check_pixel_map.py /path/to/Run10081_0.h5 --show
"""

import argparse

from calibrationnet.pipeline.waveforms import import_nabpy, pixel_map


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("h5_file")
    parser.add_argument("--show", action="store_true", help="list every disagreeing channel")
    args = parser.parse_args()

    bc_to_pixel = pixel_map(args.h5_file)
    nab = import_nabpy()
    headers = nab.File(args.h5_file).singleWaves().headers()
    by_channel = headers.groupby("bc")["pixel"].agg(["first", "nunique", "count"])

    inconsistent = by_channel[by_channel["nunique"] > 1]
    disagree = {int(bc): (bc_to_pixel.get(int(bc), -1), int(row["first"]))
                for bc, row in by_channel.iterrows() if bc_to_pixel.get(int(bc), -1) != int(row["first"])}
    print(f"{len(by_channel)} board channels carry waveforms; {len(bc_to_pixel)} rows in the file's map")
    print(f"{len(disagree)} channel(s) where nabPy's pixel differs from the file's map"
          + (" — the filter's own assignment is what the pipeline stores" if disagree else " — nabPy and the map agree"))
    if len(inconsistent):
        print(f"WARNING: {len(inconsistent)} channel(s) carry more than one pixel label within the file: {sorted(int(b) for b in inconsistent.index)}")
    if args.show and disagree:
        for bc, (from_map, from_nabpy) in sorted(disagree.items()):
            print(f"  bc {bc:4d}: map -> {from_map:5d}   nabPy -> {from_nabpy:5d}   ({int(by_channel.loc[bc, 'count'])} waveforms)")


if __name__ == "__main__":
    main()
