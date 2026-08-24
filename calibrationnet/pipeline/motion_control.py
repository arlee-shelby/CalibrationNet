"""Source-frame position readback from the motion-control archive.

From 2026-07-24 the RSIS stage position is archived on the same server as
slow controls, in the `Test` database (`public.channels` names the EPICS
channels, `public.samples` holds their timestamped values):

    BL13:Nab:RSIS:leftRightMPOS:MPOS      -> linear position (inches)
    BL13:Nab:RSIS:downUpstreamMPOS:MPOS   -> horizontal position (inches)

Both are archived on change, so a long run's samples look like: a burst
while the stage moves, then a near-silent stretch while it dwells. That
structure is what dwell_periods() turns into run segments.

Connection URL comes from POSITIONS_DATABASE_URL, or is derived from
SC_DATABASE_URL by swapping in the `Test` database — the same SSH tunnel
serves both (scripts/with_sc_tunnel.sh).
"""

import os
import re
from datetime import timedelta
from functools import lru_cache
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

load_dotenv()

LINEAR_CHANNEL = "BL13:Nab:RSIS:leftRightMPOS:MPOS"
HORIZONTAL_CHANNEL = "BL13:Nab:RSIS:downUpstreamMPOS:MPOS"

# Run-level settings that moved to this archive after 2026-07-21 (they no
# longer appear in the Nab_SlowControl instrument tables). Values are
# stored in the PHYSICAL sign convention: detector biases and HV are
# negative (e.g. -300 V, -27 kV). The old +300/+27 recording was a sign
# mistake and was corrected across the runs table on 2026-07-30. The HV
# archive readback is positive for a physically negative HV, hence the
# negation in its transform (the bias channels report negative natively;
# fixed 2026-08-24 after runs 9464-9521 briefly stored +27). Archive
# voltages are in volts (AS); hv is stored in kV, temperatures in Kelvin.
#
# Entries: column -> (channel, expected archive unit, transform, aggregate).
# The archive self-describes its units (channels field 'display.units');
# fetch_settings VERIFIES each channel still reports the expected unit and
# warns loudly if not, so an upstream unit change cannot silently corrupt
# the runs table. Transforms convert archive units to the runs-table
# conventions: biases/exb in V, hv in kV, temperatures in K, leakage in
# MICROAMPS (the unit the legacy columns were recorded in, per AS —
# LDET ran ~36 uA before 2026-07-21 and ~0.06 uA after). Settings are
# averaged over the run window except leakage, which keeps the legacy MAX
# (worst case during the run).
SETTINGS_CHANNELS = {
    "exb": ("BL13:Nab:ExBelectrode:voltage", "V", lambda v: v, "avg"),
    "ldet_bias": ("BL13:Nab:LDETBias:SourceVoltage", "V", lambda v: v, "avg"),
    "ldet_armor": ("BL13:Nab:LDETTemperatures:Armor", "K", lambda v: v, "avg"),
    "ldet_ring": ("BL13:Nab:LDETTemperatures:Ring1", "K", lambda v: v, "avg"),
    "ldet_leakage": ("BL13:Nab:LDETBias:Data", "A",
                     lambda v: v * 1e6, "max"),
    "udet_bias": ("BL13:Nab:UDETBias:SourceVoltage", "V", lambda v: v, "avg"),
    "hv": ("BL13:Nab:UDETHV:voltage", "V", lambda v: -v / 1000.0, "avg"),
    "udet_armor": ("BL13:Nab:UDETTemperatures:Armor", "K", lambda v: v, "avg"),
    "udet_ring": ("BL13:Nab:UDETTemperatures:Ring1", "K", lambda v: v, "avg"),
    "udet_leakage": ("BL13:Nab:UDETBias:Data", "A",
                     lambda v: v * 1e6, "max"),
}

# A readback wanders by a few thousandths of an inch while parked, and the
# scan steps are ~0.2 inch, so 0.02 inch safely separates "same position"
# from "moved" without splitting a dwell on jitter.
POSITION_TOLERANCE = 0.02
# Motion between dwells takes seconds to a couple of minutes; dwells are
# ~30 min. Anything shorter than this is in-transit, not a segment.
MIN_DWELL = timedelta(minutes=5)


@lru_cache(maxsize=1)
def get_positions_engine():
    url = os.environ.get("POSITIONS_DATABASE_URL")
    if not url:
        sc_url = os.environ.get("SC_DATABASE_URL")
        if not sc_url:
            raise RuntimeError(
                "Set POSITIONS_DATABASE_URL (or SC_DATABASE_URL, whose "
                "database name is swapped for 'Test') in your environment "
                "or .env file."
            )
        url = re.sub(r"/[^/?]+(\?|$)", r"/Test\1", sc_url)
    return create_engine(url)


_SAMPLES_QUERY = text(
    """
    SELECT s.time, s.float_value
    FROM samples s
    JOIN channels c ON s.channel_id = c.id
    WHERE c.channel = :channel
      AND c.field = 'value'
      AND s.time BETWEEN :t0 AND :t1
    ORDER BY s.time
    """
)

_LAST_BEFORE_QUERY = text(
    """
    SELECT s.time, s.float_value
    FROM samples s
    JOIN channels c ON s.channel_id = c.id
    WHERE c.channel = :channel
      AND c.field = 'value'
      AND s.time <= :t
    ORDER BY s.time DESC
    LIMIT 1
    """
)


def _fetch(conn, channel: str, t0, t1) -> list:
    """Samples in [t0, t1], preceded by the last value before t0 (stamped
    at t0) so the position is known from the very start of the window."""
    rows = [
        (t, v) for t, v in conn.execute(
            _SAMPLES_QUERY, {"channel": channel, "t0": t0, "t1": t1}
        ) if v is not None
    ]
    prior = conn.execute(_LAST_BEFORE_QUERY,
                         {"channel": channel, "t": t0}).first()
    if prior is not None and prior[1] is not None:
        rows.insert(0, (t0, prior[1]))
    return rows


def fetch_position_samples(start_time, end_time) -> dict:
    """{"linear": [(time, inches), ...], "horizontal": [...]} for a window.

    Raises RuntimeError with a tunnel hint if the archive is unreachable.
    """
    try:
        with get_positions_engine().connect() as conn:
            return {
                "linear": _fetch(conn, LINEAR_CHANNEL, start_time, end_time),
                "horizontal": _fetch(conn, HORIZONTAL_CHANNEL, start_time,
                                     end_time),
            }
    except OperationalError as exc:
        raise RuntimeError(
            "Could not reach the motion-control archive (Test database). "
            "Is the SSH tunnel open? (scripts/with_sc_tunnel.sh, or: ssh -N "
            "-J <you>@analysis.sns.gov -L 15432:localhost:5432 "
            "nabreplay@bl13-replay.sns.gov)"
        ) from exc


def _step_series(samples: dict) -> list:
    """Merge the two channels into [(time, linear, horizontal), ...], each
    entry being the position in force from that time onward."""
    events = [(t, "linear", v) for t, v in samples["linear"]]
    events += [(t, "horizontal", v) for t, v in samples["horizontal"]]
    events.sort(key=lambda e: e[0])

    series = []
    linear = horizontal = None
    for t, which, value in events:
        if which == "linear":
            linear = value
        else:
            horizontal = value
        if linear is None or horizontal is None:
            continue
        if series and series[-1][0] == t:
            series[-1] = (t, linear, horizontal)
        else:
            series.append((t, linear, horizontal))
    return series


def dwell_periods(start_time, end_time,
                  tolerance: float = POSITION_TOLERANCE,
                  min_dwell: timedelta = MIN_DWELL) -> List[dict]:
    """The run's periods of constant source position.

    Returns [{"start_time", "end_time", "linear_position",
    "horizontal_position"}, ...] in time order, with the position averaged
    over the dwell. Stretches shorter than min_dwell are stage motion and
    are dropped, so the gaps between returned periods are exactly the
    moves — a segment's time range therefore contains only waveforms taken
    at one position.
    """
    series = _step_series(fetch_position_samples(start_time, end_time))
    if not series:
        return []

    # Group consecutive samples that agree within tolerance on both axes.
    groups = []  # [[(t, lin, hor), ...], ...]
    for entry in series:
        if groups and (abs(entry[1] - groups[-1][0][1]) <= tolerance
                       and abs(entry[2] - groups[-1][0][2]) <= tolerance):
            groups[-1].append(entry)
        else:
            groups.append([entry])

    periods = []
    for i, group in enumerate(groups):
        group_start = group[0][0]
        # A group holds until the next group's first sample (or the window
        # end for the last one).
        group_end = groups[i + 1][0][0] if i + 1 < len(groups) else end_time
        if group_end - group_start < min_dwell:
            continue  # in transit
        periods.append({
            "start_time": group_start,
            "end_time": group_end,
            "linear_position": sum(e[1] for e in group) / len(group),
            "horizontal_position": sum(e[2] for e in group) / len(group),
        })
    return periods


_SETTING_QUERIES = {
    aggregate: text(f"""
        SELECT {aggregate.upper()}(COALESCE(s.float_value, s.integer_value))
        FROM samples s
        JOIN channels c ON s.channel_id = c.id
        WHERE c.channel = :channel
          AND c.field = 'value'
          AND s.time BETWEEN :t0 AND :t1
    """)
    for aggregate in ("avg", "max")
}

_UNITS_QUERY = text(
    """
    SELECT s.str_value
    FROM samples s
    JOIN channels c ON s.channel_id = c.id
    WHERE c.channel = :channel
      AND c.field = 'display.units'
    ORDER BY s.time DESC
    LIMIT 1
    """
)


def fetch_settings(start_time, end_time) -> dict:
    """Run-level settings over a time window, from the archive.

    Returns {runs-column: value} for every SETTINGS_CHANNELS entry that has
    samples in the window, aggregated per its entry (avg for settings, max
    for leakage) and transformed to the runs-table conventions. Channels
    with no samples are simply absent. Each channel's self-described unit
    is checked against the expected one; a mismatch skips the value and
    warns, rather than storing a silently misconverted number."""
    settings = {}
    with get_positions_engine().connect() as conn:
        for column, (channel, expected_unit, transform, aggregate) in (
                SETTINGS_CHANNELS.items()):
            unit = conn.execute(_UNITS_QUERY, {"channel": channel}).scalar()
            if unit is not None and unit != expected_unit:
                print(f"WARNING: {channel} reports display.units={unit!r} "
                      f"but {expected_unit!r} was expected — {column} left "
                      "unset; update SETTINGS_CHANNELS for the new unit.")
                continue
            value = conn.execute(_SETTING_QUERIES[aggregate], {
                "channel": channel, "t0": start_time, "t1": end_time,
            }).scalar()
            if value is not None:
                settings[column] = transform(float(value))
    return settings


def position_at(when) -> Optional[dict]:
    """The last archived position at or before a moment, or None."""
    with get_positions_engine().connect() as conn:
        linear = conn.execute(_LAST_BEFORE_QUERY,
                              {"channel": LINEAR_CHANNEL, "t": when}).first()
        horizontal = conn.execute(
            _LAST_BEFORE_QUERY, {"channel": HORIZONTAL_CHANNEL, "t": when}
        ).first()
    if linear is None or horizontal is None:
        return None
    return {"linear_position": linear[1], "horizontal_position": horizontal[1]}
