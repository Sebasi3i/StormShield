"""
Genesis model: where and when synthetic storms are born.

V1 approach -- a **climatological bootstrap with jitter**:

1. Take the first observation of every historical storm (its genesis
   point): latitude, longitude, initial wind, and day-of-year.
2. For each synthetic storm, draw one historical genesis record at
   random (with replacement) and perturb it with small Gaussian noise.

This is deliberately simple (no explicit sea-surface-temperature or
wind-shear fields), but it inherits the real climatology for free: since
we're bootstrapping from actual historical genesis points, hot spots
like the Main Development Region, the Gulf of Mexico, and the Caribbean
naturally show up in the synthetic set in roughly their historical
proportions, and the jitter keeps every synthetic storm from spawning at
one of a few exact repeated points.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .landmask import is_over_land

__all__ = ["GenesisRegion", "GenesisSample", "sample_genesis"]

#: Standard deviation of the Gaussian jitter applied to bootstrapped
#: genesis points, in degrees latitude/longitude.
_POSITION_JITTER_DEG = 0.75

#: Standard deviation of the Gaussian jitter applied to bootstrapped
#: genesis wind speed, in knots.
_WIND_JITTER_KT = 3.0

#: Floor under jittered genesis wind speed -- a tropical cyclone has to
#: start out at least this strong to be worth tracking.
_MIN_GENESIS_WIND_KT = 20.0

#: Real tropical cyclones don't form over land. Because jitter can push
#: a bootstrapped point (especially one near a coastline, e.g. Gulf/
#: Caribbean genesis) onto land, re-roll the jitter for any offending
#: sample up to this many times before falling back to the exact,
#: unjittered historical point (which -- having been a real genesis
#: location -- is essentially guaranteed to be over water).
_MAX_LAND_REJECTION_ATTEMPTS = 5

#: How far (in degrees lat/lon, straight Euclidean distance -- no
#: cos(latitude) correction, consistent with the rest of this module's
#: degree-space jitter) a point may sit from the *nearest* historical
#: genesis observation and still count as a "plausible neighbor" for
#: :class:`GenesisRegion`. Chosen from this package's own historical
#: fixture: nearest-neighbor spacing between historical genesis points
#: has a median of ~0.8 deg and a 90th percentile of ~1.5 deg, so 3.0
#: deg comfortably bridges normal gaps *within* a genesis cluster (Main
#: Development Region, Gulf, Caribbean, subtropical Atlantic) without
#: being so large that it merges genuinely distinct clusters or spills
#: into open ocean/land areas with no real climatological basis. Retune
#: this if fit against a very differently-shaped historical dataset.
GENESIS_REGION_BUFFER_DEG = 3.0


@dataclass(frozen=True)
class GenesisSample:
    """One synthetic storm's starting state."""

    latitude: float
    longitude: float
    max_wind_kt: float
    timestamp: pd.Timestamp


def _historical_genesis_records(historical_data: pd.DataFrame) -> pd.DataFrame:
    """First (earliest-timestamp) observation of every storm.

    Drops any storm whose first observation is missing latitude,
    longitude, or wind (real HURDAT2 has a handful of these -- mostly
    pre-1988-ish storms whose very first fix predates routine wind
    estimates and carries a missing-value sentinel instead of a
    number). Such a record can't serve as a genesis template: a
    bootstrapped storm needs a real starting wind, and letting a NaN
    through would silently propagate into every downstream calculation
    that depends on it (dissipation checks, category, landfall wind
    comparisons -- all quietly false/no-op against NaN rather than
    raising).
    """
    ordered = historical_data.sort_values(["storm_id", "timestamp"])
    records = ordered.groupby("storm_id", sort=False).first().reset_index()
    return records.dropna(subset=["latitude", "longitude", "max_wind_kt"])


class GenesisRegion:
    """Where synthetic storms are allowed to be born: historical genesis
    points, plus a buffer for climatologically "plausible neighbors"
    nearby that just didn't happen to appear in this particular
    historical record.

    This exists for interactive/exploratory use (e.g. the dashboard's
    click-to-start-a-storm map) where a user can pick *any* point,
    including ones with no climatological basis (mid-continent, deep
    subtropical/polar ocean). :func:`sample_genesis` doesn't need this
    itself -- it only ever bootstraps from real historical points (plus
    small jitter), so it can't wander outside the historical footprint
    in the first place.

    Backed by a k-d tree over historical genesis (latitude, longitude)
    points for fast nearest-neighbor distance queries, the same
    technique :class:`hurricane_simulator.transition.AnalogTransitionModel`
    uses.
    """

    def __init__(self, tree: cKDTree, buffer_deg: float):
        self._tree = tree
        self._buffer_deg = buffer_deg

    @classmethod
    def fit(
        cls,
        historical_data: pd.DataFrame,
        buffer_deg: float = GENESIS_REGION_BUFFER_DEG,
    ) -> "GenesisRegion":
        """Build the region from historical genesis points.

        Parameters
        ----------
        historical_data:
            Prepared historical observations (see
            :func:`hurricane_simulator.data.prepare_historical_data`).
        buffer_deg:
            How far (degrees) from the nearest historical genesis point
            still counts as a "plausible neighbor".
        """
        genesis_records = _historical_genesis_records(historical_data)
        if len(genesis_records) == 0:
            raise ValueError("historical_data has no storms to build a genesis region from.")
        points = genesis_records[["latitude", "longitude"]].to_numpy(dtype=float)
        tree = cKDTree(points)
        return cls(tree, buffer_deg)

    def contains(self, latitude, longitude):
        """Whether the given point(s) fall within a historical genesis
        region or its buffered "plausible neighbor" halo.

        Parameters
        ----------
        latitude, longitude:
            Scalars or equal-shape array-likes, in decimal degrees.

        Returns
        -------
        bool or numpy.ndarray of bool
            Matches the shape of the input.
        """
        lat_arr = np.atleast_1d(np.asarray(latitude, dtype=float))
        lon_arr = np.atleast_1d(np.asarray(longitude, dtype=float))
        query_points = np.column_stack([lat_arr, lon_arr])
        distance, _ = self._tree.query(query_points)
        result = distance <= self._buffer_deg
        if np.isscalar(latitude) or (hasattr(latitude, "ndim") and latitude.ndim == 0):
            return bool(result[0])
        return result

    def distance_to_nearest_historical_point_deg(self, latitude: float, longitude: float) -> float:
        """Distance (degrees) from a single point to the nearest
        historical genesis observation -- useful for a human-readable
        "how far outside the historical region is this" error message.
        """
        distance, _ = self._tree.query([[latitude, longitude]])
        return float(distance[0])


def sample_genesis(
    rng: np.random.Generator,
    historical_data: pd.DataFrame,
    num_storms: int,
    season_year: int,
) -> list[GenesisSample]:
    """Draw ``num_storms`` synthetic genesis states.

    Parameters
    ----------
    rng:
        Seeded NumPy random generator (the caller owns reproducibility).
    historical_data:
        Prepared historical observations (see
        :func:`hurricane_simulator.data.prepare_historical_data`).
    num_storms:
        How many genesis samples to draw.
    season_year:
        Calendar year used to build each synthetic storm's timestamps.
        The *day-of-year* is bootstrapped from historical genesis dates
        (so seasonality -- peak Aug-Oct in the Atlantic -- is
        preserved); only the year is a bookkeeping choice, since the
        historical record spans many different real years.
    """
    genesis_records = _historical_genesis_records(historical_data)
    n_hist = len(genesis_records)
    if n_hist == 0:
        raise ValueError("historical_data has no storms to bootstrap genesis from.")

    pick_idx = rng.integers(0, n_hist, size=num_storms)
    lat_jitter = rng.normal(0.0, _POSITION_JITTER_DEG, size=num_storms)
    lon_jitter = rng.normal(0.0, _POSITION_JITTER_DEG, size=num_storms)
    wind_jitter = rng.normal(0.0, _WIND_JITTER_KT, size=num_storms)

    day_of_year = genesis_records["timestamp"].dt.dayofyear.to_numpy()
    picked_doy = day_of_year[pick_idx]
    # Guard against Feb-29-shifted day-of-year values in a non-leap
    # season_year by clamping to the valid range for that year.
    days_in_year = 366 if pd.Timestamp(season_year, 12, 31).dayofyear == 366 else 365
    picked_doy = np.clip(picked_doy, 1, days_in_year)

    samples = []
    base_lats = genesis_records["latitude"].to_numpy()[pick_idx]
    base_lons = genesis_records["longitude"].to_numpy()[pick_idx]
    lats = base_lats + lat_jitter
    lons = base_lons + lon_jitter
    winds = np.maximum(
        genesis_records["max_wind_kt"].to_numpy()[pick_idx] + wind_jitter,
        _MIN_GENESIS_WIND_KT,
    )

    # Real cyclones don't form over land; re-roll jitter for any sample
    # that landed on land, then fall back to the exact (unjittered)
    # historical point for any stubborn holdouts.
    on_land = is_over_land(lats, lons)
    for _ in range(_MAX_LAND_REJECTION_ATTEMPTS):
        if not on_land.any():
            break
        n_retry = int(on_land.sum())
        lats[on_land] = base_lats[on_land] + rng.normal(0.0, _POSITION_JITTER_DEG, size=n_retry)
        lons[on_land] = base_lons[on_land] + rng.normal(0.0, _POSITION_JITTER_DEG, size=n_retry)
        on_land = is_over_land(lats, lons)
    if on_land.any():
        lats[on_land] = base_lats[on_land]
        lons[on_land] = base_lons[on_land]

    for i in range(num_storms):
        timestamp = pd.Timestamp(season_year, 1, 1) + pd.Timedelta(days=int(picked_doy[i]) - 1)
        # Round to the nearest synoptic 6-hour slot for a clean cadence.
        timestamp = timestamp.floor("6h")
        samples.append(
            GenesisSample(
                latitude=float(lats[i]),
                longitude=float(lons[i]),
                max_wind_kt=float(winds[i]),
                timestamp=timestamp,
            )
        )
    return samples
