"""
Historical data loading and preparation.

Two entry points:

- :func:`load_hurdat2` parses the raw, fixed-format text file NOAA/NHC
  distributes ("HURDAT2") into a tidy :class:`pandas.DataFrame`. Use this
  if you're handed the ``.txt`` file directly.
- :func:`prepare_historical_data` takes *any* DataFrame that already has
  the minimum required columns (storm_id, timestamp, latitude, longitude,
  max_wind_kt) -- whether it came from :func:`load_hurdat2`, a database
  query, or a CSV someone else produced -- validates it, and adds the
  derived transition columns (delta_lat, delta_lon, delta_wind) the
  Monte Carlo engine needs.

``simulate_hurricanes`` calls :func:`prepare_historical_data` itself, so
most callers only ever need one of these two functions.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

from .schema import HISTORICAL_REQUIRED_COLUMNS

__all__ = ["load_hurdat2", "prepare_historical_data"]

_HEADER_RE = re.compile(r"^(?P<id>AL\d{6}|[A-Z]{2}\d{6}),\s*(?P<name>[^,]*),\s*(?P<n>\d+),?\s*$")

#: Two whitespace-joined lat/lon tokens with no comma between them, e.g.
#: ``'63.3N    7.5E'``. Seen exactly once in NOAA's official 1851-2025
#: file (a 1969 extratropical-transition record) -- a source-file typo,
#: not a documented format variant.
_MERGED_LATLON_RE = re.compile(
    r"^(-?\d+(?:\.\d+)?[NS])\s+(-?\d+(?:\.\d+)?[EW])$", re.IGNORECASE
)


def _parse_latlon(token: str, *, default_hemisphere: str | None = None) -> float:
    """Convert a HURDAT2 lat/lon token like ``'28.0N'`` or ``'94.8W'``
    into signed decimal degrees (N/E positive, S/W negative).

    A few real records in NOAA's official file drop the trailing
    hemisphere letter outright (e.g. ``'38.83'`` instead of
    ``'38.83N'``). If ``default_hemisphere`` is given, a bare numeric
    token falls back to it; otherwise a missing hemisphere letter
    raises rather than guessing.
    """
    token = token.strip()
    if token[-1].isalpha():
        sign = -1.0 if token[-1] in ("S", "W") else 1.0
        return sign * float(token[:-1])
    if default_hemisphere is not None:
        sign = -1.0 if default_hemisphere in ("S", "W") else 1.0
        return sign * float(token)
    raise ValueError(f"Could not parse HURDAT2 lat/lon token {token!r}: missing hemisphere letter.")


def load_hurdat2(source: Union[str, Path, io.TextIOBase]) -> pd.DataFrame:
    """Parse a raw NOAA HURDAT2-format best-track file.

    Parameters
    ----------
    source:
        A path to a HURDAT2 ``.txt`` file, or an already-open text stream
        (anything with ``.readlines()`` / iterable lines) with the same
        format. The official file lives at
        https://www.nhc.noaa.gov/data/#hurdat -- download it once and
        point this function at the local copy; this library does not
        fetch it for you.

    Returns
    -------
    pandas.DataFrame
        One row per best-track observation, with (at minimum) the
        columns in ``schema.HISTORICAL_REQUIRED_COLUMNS`` plus
        ``storm_name``, ``record_identifier``, ``status``, and
        ``min_pressure_mb`` where available. Missing-data sentinels
        (``-999``, and older-record ``-99`` wind entries -- in
        general, any negative value) in the source file are converted to
        ``NaN``.

    Notes
    -----
    HURDAT2 alternates two line shapes:

    - A **header** line per storm: ``AL011851,  UNNAMED,  14,``
      (basin+ATCF number+year, name, number of best-track entries).
    - One **data** line per synoptic observation: date, time, record
      identifier, status, lat, lon, max wind (kt), min pressure (mb),
      then wind-radii fields we don't need for this V1.

    A handful of genuine source-file typos are also tolerated rather
    than dropped: a missing comma that joins the lat and lon tokens
    into one field (one 1969 record in NOAA's official file), and a
    latitude missing its trailing hemisphere letter, which is assumed
    to be ``N`` (one 1975 record; every other latitude in the Atlantic
    file is Northern Hemisphere, so this isn't a guess).
    """
    if isinstance(source, (str, Path)):
        with open(source, "r") as f:
            lines = f.readlines()
    else:
        lines = source.readlines()

    rows = []
    storm_id = None
    storm_name = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        header_match = _HEADER_RE.match(line)
        if header_match:
            storm_id = header_match.group("id")
            storm_name = header_match.group("name").strip()
            continue

        # Data line -- comma-separated, trailing comma tolerated.
        fields = [f.strip() for f in line.split(",")]
        if len(fields) >= 5:
            merged = _MERGED_LATLON_RE.match(fields[4])
            if merged:
                # A missing comma joined the lat and lon tokens into one
                # field (see _MERGED_LATLON_RE) -- split them back apart
                # and re-insert, restoring the normal field count/order
                # rather than losing this row.
                fields = fields[:4] + [merged.group(1), merged.group(2)] + fields[5:]
        if len(fields) < 8 or storm_id is None:
            continue  # malformed / stray line; skip rather than raise

        date_str, time_str = fields[0], fields[1]
        record_identifier = fields[2]
        status = fields[3]
        lat = _parse_latlon(fields[4], default_hemisphere="N")
        lon = _parse_latlon(fields[5])
        wind = float(fields[6])
        pressure = float(fields[7]) if len(fields) > 7 else np.nan

        timestamp = pd.Timestamp(
            year=int(date_str[0:4]),
            month=int(date_str[4:6]),
            day=int(date_str[6:8]),
            hour=int(time_str[0:2]),
            minute=int(time_str[2:4]),
        )

        rows.append(
            {
                "storm_id": storm_id,
                "storm_name": storm_name,
                "timestamp": timestamp,
                "record_identifier": record_identifier,
                "status": status,
                "latitude": lat,
                "longitude": lon,
                "max_wind_kt": wind,
                "min_pressure_mb": pressure,
            }
        )

    if not rows:
        raise ValueError(
            "No HURDAT2 data lines were parsed from the given source. "
            "Check that it is an unmodified HURDAT2-format file."
        )

    df = pd.DataFrame(rows)
    # HURDAT2's documented missing-value sentinel is -999, but older
    # records (pre-1988-ish, before routine Dvorak wind estimates)
    # sometimes use -99 specifically for a missing wind speed while
    # everything else on the line is -999. Rather than enumerate every
    # historical sentinel convention, treat any negative value as
    # missing: wind speed and pressure are never physically negative,
    # so this is safe and catches -99, -999, and anything else in that
    # family without silently feeding a fake wind of -99 kt (or -999)
    # into downstream delta/transition statistics, where it would show
    # up as spurious +-100+ kt intensity jumps between observations.
    df.loc[df["min_pressure_mb"] < 0, "min_pressure_mb"] = np.nan
    df.loc[df["max_wind_kt"] < 0, "max_wind_kt"] = np.nan
    df = df.sort_values(["storm_id", "timestamp"]).reset_index(drop=True)
    return df


def prepare_historical_data(historical_data: pd.DataFrame) -> pd.DataFrame:
    """Validate a historical-observations table and add derived columns.

    Parameters
    ----------
    historical_data:
        Must contain at least ``storm_id``, ``timestamp``, ``latitude``,
        ``longitude``, ``max_wind_kt``. ``timestamp`` should be
        datetime-like (or parseable by :func:`pandas.to_datetime`).

    Returns
    -------
    pandas.DataFrame
        A copy of the input, sorted by (storm_id, timestamp), with
        ``delta_lat``, ``delta_lon``, and ``delta_wind`` columns added
        (the change from the *previous* observation of the same storm;
        ``NaN`` on each storm's first observation) plus a ``delta_hours``
        column recording the time gap each delta was computed over.

    Raises
    ------
    ValueError
        If any required column is missing, or if ``max_wind_kt`` is
        entirely missing/NaN.
    """
    missing = [c for c in HISTORICAL_REQUIRED_COLUMNS if c not in historical_data.columns]
    if missing:
        raise ValueError(
            f"historical_data is missing required column(s): {missing}. "
            f"Required columns are: {HISTORICAL_REQUIRED_COLUMNS}"
        )

    df = historical_data.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["storm_id", "timestamp"]).reset_index(drop=True)

    if df["max_wind_kt"].isna().all():
        raise ValueError("historical_data['max_wind_kt'] is entirely missing.")

    grouped = df.groupby("storm_id", sort=False)
    df["delta_lat"] = grouped["latitude"].diff()
    df["delta_lon"] = grouped["longitude"].diff()
    df["delta_wind"] = grouped["max_wind_kt"].diff()
    df["delta_hours"] = grouped["timestamp"].diff().dt.total_seconds() / 3600.0

    return df
