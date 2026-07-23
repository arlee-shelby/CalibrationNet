"""Read-only access to the Nab slow-controls database.

The slow-controls Postgres runs on bl13-replay.sns.gov and is only
reachable through an SSH tunnel (jump host analysis.sns.gov). This module
does NOT manage the tunnel — open it first (scripts/with_sc_tunnel.sh
does this for you), then everything here talks to the forwarded local
port.

Connection URL comes from SC_DATABASE_URL in the environment or .env:

    SC_DATABASE_URL=postgresql+psycopg://readonly:<password>@127.0.0.1:15432/Nab_SlowControl
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


# One query for everything we store about a run: timing from runlog.status
# plus each setting averaged (leakage: max'd) over the run's time window
# from the instrument table that records it. Adapted from the Grafana
# run-metadata dashboard's query.
#
# Not yet available here (TODO once we know where they live):
#   ldet_ring, linear_position, horizontal_position
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
        (SELECT AVG(-values[2]/1e6) FROM keithley6487_2.data
         WHERE time BETWEEN runstarttime AND runendtime) AS udet_bias,
        (SELECT AVG(-values[2]/1e6) FROM keithley6487_1.data
         WHERE time BETWEEN runstarttime AND runendtime) AS ldet_bias,
        (SELECT AVG(values[1]/1000)::integer FROM highvoltage.data
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
    """Pull everything we store about one run from slow controls, in a
    single query: start/end times plus the detector/beamline settings
    averaged over the run's time window.

    Keys that match Run column names (start_time, end_time, number_subruns,
    udet_bias, …) are stored by ingest; the rest (rundescription, errorcode)
    ride along for callers that want them. A setting whose instrument has
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
            "open? (scripts/with_sc_tunnel.sh, or: ssh -N -J "
            "<you>@analysis.sns.gov -L 15432:localhost:5432 "
            "nabreplay@bl13-replay.sns.gov)"
        ) from exc

    if row is None:
        return None

    data = dict(row)
    data.update(_parse_positions(data.get("rundescription")))
    return data


def _parse_positions(description: Optional[str]) -> dict:
    """Pull source positions out of the free-text run description, e.g.
    "calibration run, source lin pos: 34.0, 2D: 2.7 units, ...". Returns
    {} for keys it can't find so ingest just leaves those columns NULL."""
    if not description:
        return {}
    found = {}
    # Style A: "source lin pos: 34.0, 2D: 2.7 units"
    lin = re.search(r"lin\s*pos:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
    if lin:
        found["linear_position"] = float(lin.group(1))
    horiz = re.search(r"2D:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
    if horiz:
        found["horizontal_position"] = float(horiz.group(1))
    # Style B: "position: 34.0,2.7" / "position 34.4/1.7" / "position 33.2 2.7"
    if not found:
        pair = re.search(
            r"position:?\s*(-?\d+(?:\.\d+)?)\s*[,/ ]\s*(-?\d+(?:\.\d+)?)",
            description,
            re.I,
        )
        if pair:
            found["linear_position"] = float(pair.group(1))
            found["horizontal_position"] = float(pair.group(2))
    # Mixed style: "position: 35.0 2D 2.2" — 2D matched above but linear
    # didn't; the number right after "position" is the linear position.
    if "linear_position" not in found:
        lone = re.search(r"position:?\s*(-?\d+(?:\.\d+)?)", description, re.I)
        if lone:
            found["linear_position"] = float(lone.group(1))
    return found
