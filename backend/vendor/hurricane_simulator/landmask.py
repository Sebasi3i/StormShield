"""
Land/sea determination.

Backed by the ``global-land-mask`` package, which bundles a ~0.1 degree
resolution land/sea raster and does the lookup entirely offline (no
network or shapefile download required, and no NOAA/NHC dependency).
"""

from __future__ import annotations

import numpy as np

try:
    from global_land_mask import globe as _globe
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The 'global-land-mask' package is required for land/sea "
        "detection. Install it with: pip install global-land-mask"
    ) from exc

__all__ = ["is_over_land"]


def is_over_land(latitude, longitude):
    """Return whether the given point(s) are over land.

    Parameters
    ----------
    latitude, longitude:
        Scalars or array-likes of equal shape, in decimal degrees
        (longitude in [-180, 180], West negative).

    Returns
    -------
    bool or numpy.ndarray of bool
        Matches the shape of the input.
    """
    lat = np.asarray(latitude, dtype=float)
    lon = np.asarray(longitude, dtype=float)
    # global_land_mask expects longitude in [-180, 180]; wrap defensively.
    lon = ((lon + 180.0) % 360.0) - 180.0
    result = _globe.is_land(lat, lon)
    if np.isscalar(latitude) or (hasattr(latitude, "ndim") and latitude.ndim == 0):
        return bool(result)
    return result
