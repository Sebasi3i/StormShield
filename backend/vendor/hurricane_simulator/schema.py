"""
Column-name contracts for the hurricane simulator.

Centralizing these names in one module means every other module (data
loading, the Monte Carlo engine, tests, downstream consumers) imports the
same constants instead of retyping string literals. If a column ever needs
to be renamed, this is the only file that changes.

This module also documents the two public output tables and the minimum
"hazard boundary" that gets handed to the next module in the pipeline
(the wind field model). See ``hurricane_simulator/__init__.py`` for the
full pipeline diagram.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Historical input schema (HURDAT2-derived)
# ---------------------------------------------------------------------------

#: Minimum columns required in ``historical_data`` passed to
#: :func:`hurricane_simulator.simulate_hurricanes`.
HISTORICAL_REQUIRED_COLUMNS = [
    "storm_id",
    "timestamp",
    "latitude",
    "longitude",
    "max_wind_kt",
]

#: Per-storm, timestep-to-timestep transition columns. These are derived
#: from the required columns (see ``data.add_derived_columns``) and are
#: what the Monte Carlo transition model is fit on.
HISTORICAL_DERIVED_COLUMNS = [
    "delta_lat",
    "delta_lon",
    "delta_wind",
]

# ---------------------------------------------------------------------------
# Output: storm-track table (one row per simulated storm per time step)
# ---------------------------------------------------------------------------

#: The non-negotiable hazard boundary handed to the wind field model:
#: storm ID, time, lat, lon, V_max. Nothing about land, property, or
#: dollars belongs on this list -- see the module-boundary note in the
#: package README.
HAZARD_BOUNDARY_COLUMNS = [
    "storm_id",
    "timestamp",
    "latitude",
    "longitude",
    "max_wind_kt",
]

#: Minimum output columns for the storm-track table, per spec.
TRACK_MIN_COLUMNS = [
    "storm_id",
    "step",
    "timestamp",
    "latitude",
    "longitude",
    "max_wind_kt",
]

#: Extra convenience columns added to every track row.
TRACK_EXTRA_COLUMNS = [
    "category",
    "is_over_land",
    "is_active",
    "genesis_lat",
    "genesis_lon",
    "max_intensity_kt",
]

#: Full, ordered column list for the storm-track table.
TRACK_COLUMNS = TRACK_MIN_COLUMNS + TRACK_EXTRA_COLUMNS

# ---------------------------------------------------------------------------
# Output: storm-summary table (one row per simulated storm)
# ---------------------------------------------------------------------------

SUMMARY_COLUMNS = [
    "storm_id",
    "genesis_time",
    "lysis_time",
    "duration_hours",
    "genesis_lat",
    "genesis_lon",
    "min_lat",
    "max_lat",
    "max_wind_kt",
    "landfall",
    "landfall_time",
    "landfall_lat",
    "landfall_lon",
    "landfall_wind_kt",
]

# ---------------------------------------------------------------------------
# Physical / modeling constants
# ---------------------------------------------------------------------------

#: Below this intensity a system is no longer tracked as a tropical
#: cyclone and the synthetic track ends (lysis). 20 kt is comfortably
#: below the 34 kt tropical-storm threshold, giving weakening storms a
#: couple of steps to "fizzle out" rather than being cut off exactly at
#: the TS/TD boundary.
DISSIPATION_THRESHOLD_KT = 20.0

#: A system is considered to have made landfall the first time its
#: center is over land at tropical-storm strength or greater. Weak,
#: disorganized systems crossing a coastline are not "landfalls" in the
#: sense the insurance model cares about.
LANDFALL_MIN_WIND_KT = 34.0

#: Above this latitude we treat the storm as having undergone
#: extratropical transition / recurved out of the tropical basin, which
#: ends the synthetic track. This is a deliberately simple V1 rule.
EXTRATROPICAL_LATITUDE_CUTOFF = 55.0

#: Safety valve on the bootstrap intensity model: nothing stops a chain
#: of unlucky positive delta_wind draws from compounding past anything
#: seen historically, so wind is hard-capped at roughly the strongest
#: sustained winds ever recorded on Earth (~190 kt, Hurricane Patricia).
#: This bounds an unrealistic tail without materially affecting the
#: bulk of the distribution.
MAX_PHYSICAL_WIND_KT = 185.0

#: Saffir-Simpson-style classification bins, in ascending order of
#: (lower bound, label). A wind speed is assigned the highest bin whose
#: lower bound it meets or exceeds.
CATEGORY_BINS = [
    (0.0, "TD"),
    (34.0, "TS"),
    (64.0, "1"),
    (83.0, "2"),
    (96.0, "3"),
    (113.0, "4"),
    (137.0, "5"),
]
