"""Pull metadata from experiment instruments which are recorded with EPICS
and stored on a database on the slow-control computer (which is not the same as the
slow-control database). Different than the slow-control database, this one also records
the RSIS source position starting from 2026-07-24, which is used to make run segments (periods
of a run where the source position is constant, i.e. "dwell" period).

The source position is recorded on the same server as slow controls database, but in
a database named "Test". "public.channels" names the EPICS
channels and "public.samples" holds their timestamped values. For example:

    BL13:Nab:RSIS:leftRightMPOS:MPOS      -> linear position (inches)
    BL13:Nab:RSIS:downUpstreamMPOS:MPOS   -> horizontal position (inches)

Note, run segments are determined by the period of time between when the source position changes.
The motion between dwells is deliberately left outside every segment, so waveforms selected by
a segment's time range were all taken at one position (i.e. transition periods where the
sources are moving are not included in the segment time).

Connection URL comes from POSITIONS_DATABASE_URL, or is derived from
SC_DATABASE_URL by swapping in the "Test" database (the same SSH tunnel
serves both (scripts/with_sc_tunnel.sh)).
"""

import os
import re
from datetime import timedelta
from functools import lru_cache
from typing import List

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

load_dotenv()

LINEAR_CHANNEL = "BL13:Nab:RSIS:leftRightMPOS:MPOS"
HORIZONTAL_CHANNEL = "BL13:Nab:RSIS:downUpstreamMPOS:MPOS"

# run settings that moved to the new database after 2026-07-21 (they no
# longer appear in the Nab_SlowControl database)
# Values stored in the physical sign convention, i.e. detector biases and HV are
# negative during normal run conditions (even though HV is recorded as positive,
# it is stored here as negative)
# units: voltages are in V; hv is in kV, temperatures in K, leakage currents are in uA
# settings are averaged over the run, except for leakage current which is taken to be
# the max value
# structure is "setting": (channel, expected archive unit, transform, storage type)
SETTINGS_CHANNELS = {
    "exb": ("BL13:Nab:ExBelectrode:voltage", "V", lambda v: v, "avg"),
    "ldet_bias": ("BL13:Nab:LDETBias:SourceVoltage", "V", lambda v: v, "avg"),
    "ldet_armor": ("BL13:Nab:LDETTemperatures:Armor", "K", lambda v: v, "avg"),
    "ldet_ring": ("BL13:Nab:LDETTemperatures:Ring1", "K", lambda v: v, "avg"),
    "ldet_leakage": ("BL13:Nab:LDETBias:Data", "A",lambda v: v * 1e6, "max"),
    "udet_bias": ("BL13:Nab:UDETBias:SourceVoltage", "V", lambda v: v, "avg"),
    "hv": ("BL13:Nab:UDETHV:voltage", "V", lambda v: -v / 1000.0, "avg"),
    "udet_armor": ("BL13:Nab:UDETTemperatures:Armor", "K", lambda v: v, "avg"),
    "udet_ring": ("BL13:Nab:UDETTemperatures:Ring1", "K", lambda v: v, "avg"),
    "udet_leakage": ("BL13:Nab:UDETBias:Data", "A",lambda v: v * 1e6, "max"),
}

# the source position readback wanders by a few thousandths of an inch while parked, so
# a 0.02" tolerance is used to determine dwell periods
POSITION_TOLERANCE = 0.02

# most runs have dwell periods much longer than 5 minutes, but the minimum condition can be
# overridden in the case that shorter segments need to be determined
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


# query to obtain the channel values from the "Test" database
_ENTRIES_QUERY = text(
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

# query to determine channel value at or before time t
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
    """Get all records in a time range [t0, t1]. And, the last value before t0 (so the
    position is known from the very start of the time range).
    """
    rows = [
        (t, v) for t, v in conn.execute(
            _ENTRIES_QUERY, {"channel": channel, "t0": t0, "t1": t1}
        ) if v is not None
    ]
    prior = conn.execute(_LAST_BEFORE_QUERY,
                         {"channel": channel, "t": t0}).first()
    if prior is not None and prior[1] is not None:
        rows.insert(0, (t0, prior[1]))
    return rows


def fetch_position_entries(start_time, end_time) -> dict:
    """Get the source positions for a time period
    {"linear": [(time, inches), ...], "horizontal": [...]}.

    Raises RuntimeError with a tunnel hint if the database is unreachable.
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
            "Could not reach the Test database. "
            "Is the SSH tunnel open? (scripts/with_sc_tunnel.sh opens it)"
        ) from exc


def _merge_position_entries(entries: dict) -> list:
    """Merge the linear and horizontal position channels into one object.
    Returns a list [(time, linear, horizontal), ...] sorted in time
    """
    events = [(t, "linear", v) for t, v in entries["linear"]]
    events += [(t, "horizontal", v) for t, v in entries["horizontal"]]
    events.sort(key=lambda e: e[0])

    positions = []
    linear = horizontal = None
    for t, position, value in events:
        if position == "linear":
            linear = value
        else:
            horizontal = value
        if linear is None or horizontal is None:
            continue
        if positions and positions[-1][0] == t:
            positions[-1] = (t, linear, horizontal)
        else:
            positions.append((t, linear, horizontal))
    return positions


def dwell_periods(start_time, end_time,tolerance: float = POSITION_TOLERANCE,min_dwell: timedelta = MIN_DWELL) -> List[dict]:
    """Determines a run's periods of constant source position, i.e. dwell periods.
    The position stored in the database for the run segment is the average over the dwell
    period. Stretches shorter than min_dwell (default 5 minutes) are considered motion periods and
    are dropped, so a segment's time range therefore contains only waveforms taken
    at one position.

    Returns a list [{"start_time", "end_time", "linear_position", "horizontal_position"}, ...]
    in time order.
    """
    positions = _merge_position_entries(fetch_position_entries(start_time, end_time))
    if not positions:
        return []

    # group consecutive positions that agree within tolerance on both axes (linear and horizontal)
    # by separate lists (i.e. groups = [[(t, lin, hor), ...], ...])
    groups = []
    for entry in positions:
        if groups and (abs(entry[1] - groups[-1][0][1]) <= tolerance and abs(entry[2] - groups[-1][0][2]) <= tolerance):
            groups[-1].append(entry)
        else:
            groups.append([entry])

    dwells = []
    for i, group in enumerate(groups):
        group_start = group[0][0]
        group_end = groups[i + 1][0][0] if i + 1 < len(groups) else end_time
        if group_end - group_start < min_dwell:
            continue
        dwells.append({
            "start_time": group_start,
            "end_time": group_end,
            "linear_position": sum(e[1] for e in group) / len(group),
            "horizontal_position": sum(e[2] for e in group) / len(group),
        })
    return dwells


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


# each channel stores its own units (channels field 'display.units'), which can be
# cross referenced to the expected units so a unit change cannot corrupt the runs table
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
    """Run-level settings over a time window.

    Returns {runs-column: value} for every SETTINGS_CHANNELS entry that has
    entries in the window, obtained from a transformation to the runs-table conventions.
    The average of the entries is returned as the runs-column value for
    all setting channels, except leakage current which is set to the max value. Channels
    with no samples are simply absent. Each channel's self-described unit
    is checked against the expected one and a mismatch skips the value and
    warns, rather than storing a silently misconverted number.
    """
    settings = {}
    with get_positions_engine().connect() as conn:
        for column, (channel, expected_unit, transform, aggregate) in (SETTINGS_CHANNELS.items()):
            unit = conn.execute(_UNITS_QUERY, {"channel": channel}).scalar()
            if unit is not None and unit != expected_unit:
                print(f"WARNING: {channel} reports display.units={unit!r} "
                      f"but {expected_unit!r} was expected — {column} left "
                      "unset; update SETTINGS_CHANNELS for the new unit.")
                continue
            value = conn.execute(_SETTING_QUERIES[aggregate], {"channel": channel, "t0": start_time, "t1": end_time,}).scalar()
            if value is not None:
                settings[column] = transform(float(value))
    return settings
