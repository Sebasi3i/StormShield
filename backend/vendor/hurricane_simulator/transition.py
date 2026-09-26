"""
Transition model: how a storm's (lat, lon, wind) evolves one time step
at a time.

V1 approach -- a **nonparametric analog / k-NN bootstrap**, a standard
lightweight technique for synthetic track generation:

1. From the historical record, build one feature vector per observation
   that has a valid "what happened next" delta: current latitude,
   longitude, wind speed, and time of year (as sin/cos of day-of-year,
   so Dec 31 and Jan 1 are neighbors).
2. Standardize those features and index them in a k-d tree.
3. To advance a synthetic storm one step: standardize its current state
   the same way, find the K most similar historical states ("analogs"),
   and randomly adopt one analog's realized (delta_lat, delta_lon,
   delta_wind) -- with a little extra Gaussian smoothing so the
   synthetic set isn't limited to literally-repeated historical deltas.

This keeps the storm on a realistic manifold (typical translation
speed, curvature, and intensification/weakening rates for a system of
that intensity, in that location, at that time of year) without hand-
coding an explicit track-steering or intensity model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

__all__ = ["AnalogTransitionModel"]

#: Number of nearest historical analogs to sample from at each step.
_DEFAULT_K_NEIGHBORS = 12

#: Extra Gaussian smoothing added to a sampled analog delta, as a
#: fraction of that feature's historical standard deviation. Keeps the
#: synthetic set from being limited to exactly-repeated historical
#: deltas while staying small relative to real step-to-step variability.
_SMOOTHING_FRACTION = 0.15


def _cyclical_day_of_year(timestamps: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    doy = timestamps.dt.dayofyear.to_numpy(dtype=float)
    angle = 2.0 * np.pi * doy / 365.25
    return np.sin(angle), np.cos(angle)


@dataclass
class _FeatureScaler:
    mean: np.ndarray
    std: np.ndarray

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std


class AnalogTransitionModel:
    """K-d-tree analog/bootstrap model over historical state -> delta
    pairs. Fit once per call to ``simulate_hurricanes`` and reused for
    every synthetic storm and step.
    """

    def __init__(
        self,
        tree: cKDTree,
        deltas: np.ndarray,
        scaler: _FeatureScaler,
        delta_std: np.ndarray,
        k_neighbors: int,
    ):
        self._tree = tree
        self._deltas = deltas  # shape (n, 3): delta_lat, delta_lon, delta_wind
        self._scaler = scaler
        self._delta_std = delta_std
        self._k = k_neighbors

    @classmethod
    def fit(cls, historical_data: pd.DataFrame, k_neighbors: int = _DEFAULT_K_NEIGHBORS) -> "AnalogTransitionModel":
        """Build the analog index from prepared historical data.

        Expects the derived columns from
        :func:`hurricane_simulator.data.prepare_historical_data`
        (``delta_lat``, ``delta_lon``, ``delta_wind``, ``delta_hours``).
        Rows without a valid *next* delta (a storm's own last
        observation) or with an irregular time gap are excluded, since
        the fitted deltas represent "what happens over roughly one
        historical time step" and we only want clean, regularly-spaced
        transitions.
        """
        df = historical_data.copy()

        # A row's delta_* columns describe the transition *into* that
        # row from the previous one. To predict "what happens next"
        # from a given state we want the transition *out of* that row,
        # i.e. the next row's deltas. Shift within each storm.
        grouped = df.groupby("storm_id", sort=False)
        next_delta_lat = grouped["delta_lat"].shift(-1)
        next_delta_lon = grouped["delta_lon"].shift(-1)
        next_delta_wind = grouped["delta_wind"].shift(-1)
        next_delta_hours = grouped["delta_hours"].shift(-1)

        typical_step_hours = df["delta_hours"].median()
        if not np.isfinite(typical_step_hours) or typical_step_hours <= 0:
            typical_step_hours = 6.0

        valid = (
            next_delta_lat.notna()
            & next_delta_lon.notna()
            & next_delta_wind.notna()
            & next_delta_hours.notna()
            # keep only reasonably regular spacing (within ~50% of the
            # typical step) so we don't learn from multi-day data gaps
            & (next_delta_hours.sub(typical_step_hours).abs() <= 0.5 * typical_step_hours)
        )

        fit_df = df.loc[valid]
        if len(fit_df) < k_neighbors:
            raise ValueError(
                "Not enough clean historical transitions "
                f"({len(fit_df)}) to fit an analog model with "
                f"k_neighbors={k_neighbors}. Provide more historical "
                "storms/observations or lower k_neighbors."
            )

        sin_doy, cos_doy = _cyclical_day_of_year(fit_df["timestamp"])
        features = np.column_stack(
            [
                fit_df["latitude"].to_numpy(dtype=float),
                fit_df["longitude"].to_numpy(dtype=float),
                fit_df["max_wind_kt"].to_numpy(dtype=float),
                sin_doy,
                cos_doy,
            ]
        )
        mean = features.mean(axis=0)
        std = features.std(axis=0)
        std[std < 1e-6] = 1.0  # avoid divide-by-zero on constant columns
        scaler = _FeatureScaler(mean=mean, std=std)

        deltas = np.column_stack(
            [
                next_delta_lat.loc[valid].to_numpy(dtype=float),
                next_delta_lon.loc[valid].to_numpy(dtype=float),
                next_delta_wind.loc[valid].to_numpy(dtype=float),
            ]
        )
        delta_std = deltas.std(axis=0)
        delta_std[delta_std < 1e-6] = 1.0

        tree = cKDTree(scaler.transform(features))
        return cls(
            tree=tree,
            deltas=deltas,
            scaler=scaler,
            delta_std=delta_std,
            k_neighbors=min(k_neighbors, len(fit_df)),
        )

    def sample_delta(
        self,
        rng: np.random.Generator,
        latitude: float,
        longitude: float,
        max_wind_kt: float,
        timestamp: pd.Timestamp,
    ) -> tuple[float, float, float]:
        """Sample one (delta_lat, delta_lon, delta_wind) analog step
        for the given current state."""
        doy = timestamp.dayofyear
        angle = 2.0 * np.pi * doy / 365.25
        query_point = self._scaler.transform(
            np.array([[latitude, longitude, max_wind_kt, np.sin(angle), np.cos(angle)]])
        )
        _, neighbor_idx = self._tree.query(query_point, k=self._k)
        neighbor_idx = np.atleast_1d(neighbor_idx[0])

        chosen = neighbor_idx[rng.integers(0, len(neighbor_idx))]
        base_delta = self._deltas[chosen]

        smoothing = rng.normal(0.0, _SMOOTHING_FRACTION * self._delta_std)
        delta_lat, delta_lon, delta_wind = base_delta + smoothing
        return float(delta_lat), float(delta_lon), float(delta_wind)
