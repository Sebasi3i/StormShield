"""
hurricane_simulator -- a Monte Carlo synthetic tropical cyclone track
generator for the Atlantic basin.

Pipeline this package occupies (see README.md for the full writeup)::

    HURRICANE SIMULATOR  (this package)
            |
            v
    storm trajectory + intensity   (storm_id, t, lat, lon, V_max)
            |
            v
    WIND FIELD MODEL           (not part of this package)
            |
            v
    wind at each property
            |
            v
    DAMAGE MODEL                (not part of this package)
            |
            v
    insurance claims

This package is a pure hazard generator: given historical storm
observations, it produces synthetic storm tracks and a per-storm
summary. It never computes property-level wind, damage, or claims --
that separation is intentional and enforced by only exporting the
functions below.

Quick start
-----------
>>> import hurricane_simulator as hs
>>> historical = hs.load_hurdat2("hurdat2-1851-2023.txt")
>>> tracks, summary = hs.simulate_hurricanes(historical, num_storms=1000, seed=42)
"""

from .data import load_hurdat2, prepare_historical_data
from .dashboard import launch_dashboard
from .simulator import SimulationResult, simulate_hurricanes
from .visualization import plot_simulation

__all__ = [
    "simulate_hurricanes",
    "SimulationResult",
    "load_hurdat2",
    "prepare_historical_data",
    "plot_simulation",
    "launch_dashboard",
]

__version__ = "0.1.0"
