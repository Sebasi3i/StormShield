"""Fetch one-minute ASOS observations for a list of storms and write the raw file
that build.py reads.

build.py expects raw/fl_hurricanes_asos1min_raw.csv.gz: a gzip text file with
"### "-headed sections:

    ### MANIFEST          lines "window,<STORMID>_<NAME>,<start>,<end>" (UTC)
    ### STATIONS          the stations table (sid,name,lon,lat,elevation_m,...)
    ### STORM <STORMID>_<NAME>
                          IEM one-minute CSV: station,valid(UTC),drct,sknt,
                          gust_drct,gust_sknt,pres1,precip  ("M" for missing)

This script builds that file from the Iowa Environmental Mesonet's one-minute
service (a mirror of NCEI's ASOS one-minute archive), for every storm in a manifest
CSV and every station in stations.csv. It can be run for the extension storms alone
(default: storms_2004_2005.csv) or for a full rebuild of all storms by passing the
original 11 as well; build.py concatenates whatever the raw file holds.

    python fetch_asos_1min.py                          # 2004-2005 storms
    python fetch_asos_1min.py storms_2004_2005.csv storms_2016_2024.csv
    python fetch_asos_1min.py --out raw/fl_hurricanes_asos1min_raw.csv.gz

The service's request form is https://mesonet.agron.iastate.edu/request/asos/1min.phtml.
The query below follows that form's download URL; if IEM changes its parameters,
adjust `iem_url` and nothing else. The host was not reachable from the environment
this script was written in, so the first run should be checked by hand: open one
fetched section and confirm the columns above are present.
"""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
DEFAULT_OUT = RAW / "fl_hurricanes_asos1min_raw.csv.gz"
DEFAULT_MANIFEST = ROOT / "storms_2004_2005.csv"

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
VARS = ["drct", "sknt", "gust_drct", "gust_sknt", "pres1", "precip"]
# Stations per request: IEM accepts many, but smaller batches fail more gracefully.
BATCH = 10
PAUSE_SECONDS = 1.0


def iem_url(stations: list[str], start: pd.Timestamp, end: pd.Timestamp) -> str:
    query = [("station", sid) for sid in stations] + [
        ("vars", ",".join(VARS)),
        ("sts", start.strftime("%Y-%m-%dT%H:%MZ")),
        ("ets", end.strftime("%Y-%m-%dT%H:%MZ")),
        ("sample", "1min"),
        ("tz", "UTC"),
        ("what", "download"),
        ("delim", "comma"),
    ]
    return IEM + "?" + urllib.parse.urlencode(query)


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as error:  # noqa: BLE001 - retry any transport failure
            if attempt == retries - 1:
                raise
            print(f"  retry after {error}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_storm(key: str, start: pd.Timestamp, end: pd.Timestamp, stations: list[str]) -> str:
    parts = []
    for index in range(0, len(stations), BATCH):
        batch = stations[index : index + BATCH]
        text = fetch(iem_url(batch, start, end))
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        if not lines:
            continue
        if parts:
            lines = lines[1:]  # drop the repeated header
        parts.extend(lines)
        time.sleep(PAUSE_SECONDS)
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifests", nargs="*", default=[str(DEFAULT_MANIFEST)])
    parser.add_argument("--stations", default=str(ROOT / "stations.csv"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    stations = pd.read_csv(args.stations)
    station_ids = stations["sid"].dropna().astype(str).tolist()
    storms = pd.concat([pd.read_csv(path, parse_dates=["window_start_utc", "window_end_utc"]) for path in args.manifests])

    out = io.StringIO()
    out.write("### MANIFEST\n")
    for row in storms.itertuples(index=False):
        out.write(f"window,{row.storm_id}_{row.storm_name},{row.window_start_utc:%Y-%m-%d %H:%M},{row.window_end_utc:%Y-%m-%d %H:%M}\n")
    out.write("### STATIONS\n")
    stations[["sid", "name", "lon", "lat", "elevation_m", "archive_begin", "archive_end"]].to_csv(out, index=False)

    for row in storms.itertuples(index=False):
        key = f"{row.storm_id}_{row.storm_name}"
        print(f"{key}: {row.window_start_utc} to {row.window_end_utc}, {len(station_ids)} stations")
        out.write(f"### STORM {key}\n")
        out.write(fetch_storm(key, row.window_start_utc, row.window_end_utc, station_ids))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        handle.write(out.getvalue())
    print(f"wrote {args.out}; now run build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
