"""Pull run metadata from the slow-controls database (read only access). Note,
for runs before 2026-07-24, the source position is parsed out of the run description notes.
After which, it is pulled directly from the RSIS motion control.

The slow-controls database is only reachable through an SSH tunnel. This module
does not manage the tunnel — open it first (scripts/with_sc_tunnel.sh
does this for you), then everything here talks to the forwarded local
port. Connection URL comes from SC_DATABASE_URL in the environment or .env file (see .env.example).
"""

import os
import re
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

load_dotenv()


@lru_cache(maxsize=1)
def get_sc_engine():
    url = os.environ.get("SC_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SC_DATABASE_URL is not set. Put it in your environment or a "
            ".env file (see .env.example)."
        )
    return create_engine(url)


# one query for everything stored in this database for a run (adapted from the grafana dashboard query)
# each setting is averaged over the run window, except leakage current (which
# is taken to be the max value) (this is the same convention as grafana) and start/end times
#
# these instrument tables stopped being populated around 2026-07-21, newer
# runs get these settings from the Test database instead
_RUN_QUERY = text(
    """
    WITH rundata AS (
        SELECT runnumber,
               lastsubrun,
               runstarttime,
               rundescription,
               runstarttime + runelapsedtime::interval AS runendtime,
               errorcode
        FROM runlog.status
        WHERE runnumber = :run_number
    ),
    udettemps AS (
        SELECT time,
               unnest(temperatures) AS temperatures,
               unnest((SELECT inputnames FROM ls224_2.config
                       WHERE time < data.time
                       ORDER BY time DESC LIMIT 1)) AS metric
        FROM ls224_2.data, rundata
        WHERE time BETWEEN runstarttime AND runendtime
    ),
    exbvoltage AS (
        SELECT time,
               aivoltage[6] * (SELECT aivoltagegains[6] FROM nidaqmx.config
                               WHERE time < data.time
                               ORDER BY time DESC LIMIT 1) AS voltage
        FROM nidaqmx.data, rundata
        WHERE time BETWEEN runstarttime AND runendtime
        UNION
        SELECT time,
               voltage * (SELECT CASE WHEN polarity = 'N' THEN -1 ELSE 1 END
                          FROM hjpss_2.config
                          WHERE time < data.time
                          ORDER BY time DESC LIMIT 1)
        FROM hjpss_2.data, rundata
        WHERE time BETWEEN runstarttime AND runendtime
    )
    SELECT
        runnumber,
        lastsubrun + 1 AS number_subruns,  -- lastsubrun is 0-indexed
        runstarttime AS start_time,
        rundescription,
        runendtime AS end_time,
        errorcode,
        -- Physical sign convention: biases and HV are negative. The raw
        -- instrument numbers have flipped signs, corrected here.
        (SELECT AVG(values[2]/1e6) FROM keithley6487_2.data
         WHERE time BETWEEN runstarttime AND runendtime) AS udet_bias,
        (SELECT AVG(values[2]/1e6) FROM keithley6487_1.data
         WHERE time BETWEEN runstarttime AND runendtime) AS ldet_bias,
        (SELECT AVG(-values[1]/1000)::integer FROM highvoltage.data
         WHERE time BETWEEN runstarttime AND runendtime) AS hv,
        (SELECT AVG(values[35])::integer FROM magnet.data
         WHERE time BETWEEN runstarttime AND runendtime) AS main,
        (SELECT AVG(values[34])::integer FROM magnet.data
         WHERE time BETWEEN runstarttime AND runendtime) AS udet,
        (SELECT AVG(voltage) FROM exbvoltage
         WHERE time BETWEEN runstarttime AND runendtime) AS exb,
        (SELECT AVG(temperatures) FROM udettemps
         WHERE time BETWEEN runstarttime AND runendtime
           AND metric LIKE '%armor%') AS udet_armor,
        (SELECT AVG(temperatures[1]) FROM ls224_1.data
         WHERE time BETWEEN runstarttime AND runendtime) AS ldet_armor,
        (SELECT AVG(temperatures) FROM udettemps
         WHERE time BETWEEN runstarttime AND runendtime
           AND metric LIKE '%ring 1%') AS udet_ring,
        (SELECT MAX(values[1]) FROM keithley6487_2.data
         WHERE time BETWEEN runstarttime AND runendtime) AS udet_leakage,
        (SELECT MAX(values[1]) FROM keithley6487_1.data
         WHERE time BETWEEN runstarttime AND runendtime) AS ldet_leakage
    FROM rundata
    """
)


def fetch_run(run_number: int) -> Optional[dict]:
    """Pull all the metadata for a run to be stored from the slow-control database
    using one query. Note, most settings are averaged over the run period, except start/end times
    and leakage current (which is taken to be the max value).

    Keys that match Run column names (start_time, end_time, number_subruns,
    udet_bias, ...) are stored by ingest. A setting whose instrument has
    no samples in the window comes back as None (stored as NULL).

    Returns None if the run is not in the slow-controls database. Raises
    RuntimeError with a hint about the tunnel if the database is
    unreachable.
    """
    try:
        with get_sc_engine().connect() as conn:
            row = (
                conn.execute(_RUN_QUERY, {"run_number": run_number})
                .mappings()
                .first()
            )
    except OperationalError as exc:
        raise RuntimeError(
            "Could not reach the slow-controls database. Is the SSH tunnel "
            "open? (scripts/with_sc_tunnel.sh opens it.)"
        ) from exc

    if row is None:
        return None

    data = dict(row)
    data.update(_parse_positions(data.get("rundescription")))
    return data


def _parse_positions(description: Optional[str]) -> dict:
    """Pull source positions out of the run description (ex:
    "calibration run, source lin pos: 34.0, 2D: 2.7 units, ..."). This is only necessary for
    older runs where the RSIS motion control was not recorded. Returns
    {} for keys it can't find so ingest just leaves those columns NULL. Three general styles
    were used in 2025 data, so each is used to attempt to find a match and assign a position
    for that run.
    """
    if not description:
        return {}
    found = {}
    # style A: "source lin pos: 34.0, 2D: 2.7 units"
    linear = re.search(r"lin\s*pos:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
    if linear:
        found["linear_position"] = float(linear.group(1))
    horizontal = re.search(r"2D:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
    if horizontal:
        found["horizontal_position"] = float(horizontal.group(1))
    # style B: "position: 34.0,2.7" / "position 34.4/1.7" / "position 33.2 2.7"
    if not found:
        pair = re.search(r"position:?\s*(-?\d+(?:\.\d+)?)\s*[,/ ]\s*(-?\d+(?:\.\d+)?)",description,re.I)
        if pair:
            found["linear_position"] = float(pair.group(1))
            found["horizontal_position"] = float(pair.group(2))
    # mixed style: "position: 35.0 2D 2.2" — 2D matched above but linear
    # didn't; the number right after "position" is the linear position
    if "linear_position" not in found:
        lone_linear = re.search(r"position:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
        if lone_linear:
            found["linear_position"] = float(lone_linear.group(1))
    return found
