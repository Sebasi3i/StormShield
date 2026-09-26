"""
Main entry point: :func:`simulate_hurricanes`.

This module is orchestration only -- it wires together the genesis
model (:mod:`.genesis`), the analog transition model (:mod:`.transition`),
the land mask (:mod:`.landmask`), and the intensity/category helpers
(:mod:`.intensity`) into the two output tables described in the package
README: a storm-track table and a storm-summary table.

Nothing downstream of "storm ID, time, lat, lon, max wind" -- no
property exposure, no wind field, no damage or claims -- belongs in this
module. That boundary is deliberate: see the README's module-boundary
section.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from .data import prepare_historical_data
from .genesis import GenesisSample, sample_genesis
from .intensity import category_from_wind, decay_wind_over_land
from .landmask import is_over_land
from .schema import (
    DISSIPATION_THRESHOLD_KT,
    EXTRATROPICAL_LATITUDE_CUTOFF,
    LANDFALL_MIN_WIND_KT,
    MAX_PHYSICAL_WIND_KT,
    SUMMARY_COLUMNS,
    TRACK_COLUMNS,
)
from .transition import AnalogTransitionModel

__all__ = ["simulate_hurricanes", "SimulationResult"]

_SUPPORTED_BASINS = {"Atlantic"}


class SimulationResult(NamedTuple):
    """Return value of :func:`simulate_hurricanes`.

    Behaves like a plain ``(tracks, summary)`` tuple for unpacking, and
    also supports attribute access (``result.tracks``,
    ``result.summary``) for readability at call sites.
    """

    tracks: pd.DataFrame
    summary: pd.DataFrame


def _simulate_single_track(
    storm_id: str,
    genesis: GenesisSample,
    transition_model: AnalogTransitionModel,
    rng: np.random.Generator,
    time_step_hours: float,
    max_steps: int,
) -> list[dict]:
    """Advance one synthetic storm from genesis to lysis (or the step
    cap) and return its raw per-step rows (schema columns added later,
    once genesis_lat/genesis_lon/max_intensity_kt are known)."""
    rows: list[dict] = []
    lat, lon, wind = genesis.latitude, genesis.longitude, genesis.max_wind_kt
    timestamp = genesis.timestamp

    for step in range(max_steps):
        over_land = is_over_land(lat, lon)
        rows.append(
            {
                "storm_id": storm_id,
                "step": step,
                "timestamp": timestamp,
                "latitude": lat,
                "longitude": lon,
                "max_wind_kt": wind,
                "is_over_land": bool(over_land),
            }
        )

        # Decide whether this storm continues past this step.
        dissipated = wind < DISSIPATION_THRESHOLD_KT
        recurved_away = lat >= EXTRATROPICAL_LATITUDE_CUTOFF
        last_allowed_step = step == max_steps - 1
        if dissipated or recurved_away or last_allowed_step:
            break

        if over_land:
            # Override the statistical model: intensity relaxes toward
            # the land-residual value; the center still advects using
            # the analog model's position delta so the storm keeps
            # moving inland/along the coast rather than freezing.
            delta_lat, delta_lon, _ = transition_model.sample_delta(rng, lat, lon, wind, timestamp)
            wind = decay_wind_over_land(wind, time_step_hours)
        else:
            delta_lat, delta_lon, delta_wind = transition_model.sample_delta(rng, lat, lon, wind, timestamp)
            wind = min(max(wind + delta_wind, 0.0), MAX_PHYSICAL_WIND_KT)

        lat = lat + delta_lat
        lon = lon + delta_lon
        timestamp = timestamp + pd.Timedelta(hours=time_step_hours)

    return rows


def _finalize_track_columns(rows: list[dict]) -> pd.DataFrame:
    """Add category, is_active, and the per-storm broadcast columns
    (genesis_lat, genesis_lon, max_intensity_kt) to a single storm's
    rows, and return them in the documented column order."""
    df = pd.DataFrame(rows)
    df["category"] = df["max_wind_kt"].apply(category_from_wind)
    df["is_active"] = True
    df["genesis_lat"] = df["latitude"].iloc[0]
    df["genesis_lon"] = df["longitude"].iloc[0]
    df["max_intensity_kt"] = df["max_wind_kt"].max()
    return df[TRACK_COLUMNS]


def _summarize_track(storm_id: str, track: pd.DataFrame) -> dict:
    """Build one storm-summary row from that storm's finalized track
    rows."""
    landfall_rows = track[
        track["is_over_land"] & (track["max_wind_kt"] >= LANDFALL_MIN_WIND_KT)
    ]
    # "Landfall" = the first time the center crosses onto land while at
    # least tropical-storm strength (not every subsequent over-land step).
    first_landfall = landfall_rows.iloc[0] if len(landfall_rows) else None

    return {
        "storm_id": storm_id,
        "genesis_time": track["timestamp"].iloc[0],
        "lysis_time": track["timestamp"].iloc[-1],
        "duration_hours": (
            track["timestamp"].iloc[-1] - track["timestamp"].iloc[0]
        ).total_seconds()
        / 3600.0,
        "genesis_lat": track["latitude"].iloc[0],
        "genesis_lon": track["longitude"].iloc[0],
        "min_lat": track["latitude"].min(),
        "max_lat": track["latitude"].max(),
        "max_wind_kt": track["max_wind_kt"].max(),
        "landfall": first_landfall is not None,
        "landfall_time": first_landfall["timestamp"] if first_landfall is not None else pd.NaT,
        "landfall_lat": first_landfall["latitude"] if first_landfall is not None else np.nan,
        "landfall_lon": first_landfall["longitude"] if first_landfall is not None else np.nan,
        "landfall_wind_kt": first_landfall["max_wind_kt"] if first_landfall is not None else np.nan,
    }


def simulate_hurricanes(
    historical_data: pd.DataFrame,
    num_storms: int = 1000,
    seed: int = 42,
    time_step_hours: float = 6,
    max_duration_days: float = 25,
    basin: str = "Atlantic",
    season_year: int | None = None,
) -> SimulationResult:
    """Generate a Monte Carlo set of synthetic hurricane tracks.

    Parameters
    ----------
    historical_data:
        Historical storm observations (e.g. loaded via
        :func:`hurricane_simulator.data.load_hurdat2`), with at least
        ``storm_id``, ``timestamp``, ``latitude``, ``longitude``,
        ``max_wind_kt``. Derived transition columns are computed
        automatically if not already present.
    num_storms:
        Number of synthetic storms to generate.
    seed:
        Random seed; identical inputs + seed always produce identical
        output.
    time_step_hours:
        Simulation time step, in hours. 6 hours (matching HURDAT2's
        native synoptic cadence) for V1.
    max_duration_days:
        Hard cap on any single storm's lifetime.
    basin:
        Ocean basin to simulate. Only ``"Atlantic"`` is supported in
        V1; other values raise ``ValueError``.
    season_year:
        Calendar year used for synthetic storms' timestamps (day-of-year
        is bootstrapped from historical genesis dates, so seasonality is
        preserved regardless of this choice). Defaults to the year after
        the most recent year in ``historical_data``.

    Returns
    -------
    SimulationResult
        A ``(tracks, summary)`` named tuple:

        - ``tracks``: one row per simulated storm per time step (see
          ``schema.TRACK_COLUMNS`` for the full column list). This is
          also the hazard boundary handed to a downstream wind field
          model -- ``storm_id, timestamp, latitude, longitude,
          max_wind_kt`` -- plus convenience columns.
        - ``summary``: one row per simulated storm (see
          ``schema.SUMMARY_COLUMNS``), useful for validating the
          synthetic catalog against historical climatology and as a
          quick-look table for the insurance model.

    Notes
    -----
    This function is a pure hazard generator: it never computes wind at
    a specific property, damage, or claims. That is intentionally left
    to downstream modules (wind field model -> damage model).
    """
    if basin not in _SUPPORTED_BASINS:
        raise ValueError(f"basin={basin!r} is not supported in V1. Supported basins: {sorted(_SUPPORTED_BASINS)}")
    if num_storms <= 0:
        raise ValueError("num_storms must be positive.")
    if time_step_hours <= 0:
        raise ValueError("time_step_hours must be positive.")
    if max_duration_days <= 0:
        raise ValueError("max_duration_days must be positive.")

    prepared = prepare_historical_data(historical_data)

    if season_year is None:
        season_year = int(prepared["timestamp"].dt.year.max()) + 1

    rng = np.random.default_rng(seed)
    transition_model = AnalogTransitionModel.fit(prepared)
    genesis_samples = sample_genesis(rng, prepared, num_storms, season_year)

    max_steps = max(1, int(round(max_duration_days * 24.0 / time_step_hours)))

    # Independent random substreams per storm: reproducible regardless
    # of iteration order, and safe to parallelize later if needed.
    storm_rngs = rng.spawn(num_storms)

    track_frames = []
    summary_rows = []
    for i in range(num_storms):
        storm_id = f"SYN{i + 1:04d}"
        raw_rows = _simulate_single_track(
            storm_id=storm_id,
            genesis=genesis_samples[i],
            transition_model=transition_model,
            rng=storm_rngs[i],
            time_step_hours=time_step_hours,
            max_steps=max_steps,
        )
        track_df = _finalize_track_columns(raw_rows)
        track_frames.append(track_df)
        summary_rows.append(_summarize_track(storm_id, track_df))

    tracks = pd.concat(track_frames, ignore_index=True) if track_frames else pd.DataFrame(columns=TRACK_COLUMNS)
    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)

    return SimulationResult(tracks=tracks, summary=summary)
