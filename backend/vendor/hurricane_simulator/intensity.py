"""
Intensity classification and the inland wind-decay model.

Two independent, small pieces of physics-ish bookkeeping live here:

- :func:`category_from_wind` -- a stateless Saffir-Simpson-style lookup,
  used purely for the human-readable ``category`` column on the track
  table. It has no effect on the simulation dynamics.
- :func:`decay_wind_over_land` -- the one place the simulator overrides
  the statistical (analog/bootstrap) transition model: once a storm's
  center is over land, its intensity is pulled toward a low residual
  value on an exponential decay curve, in the spirit of well-known
  inland-decay formulations (e.g. Kaplan & DeMaria's), reimplemented
  here as a simple, independently-tunable approximation rather than a
  reproduction of any particular paper's fitted model.
"""

from __future__ import annotations

import math

from .schema import CATEGORY_BINS, DISSIPATION_THRESHOLD_KT

__all__ = ["category_from_wind", "decay_wind_over_land"]


def category_from_wind(max_wind_kt: float) -> str:
    """Classify a max sustained wind speed (knots) into a Saffir-Simpson
    bin: ``'TD'``, ``'TS'``, or ``'1'`` through ``'5'``."""
    if max_wind_kt is None or (isinstance(max_wind_kt, float) and math.isnan(max_wind_kt)):
        return "TD"
    label = CATEGORY_BINS[0][1]
    for lower_bound, bin_label in CATEGORY_BINS:
        if max_wind_kt >= lower_bound:
            label = bin_label
        else:
            break
    return label


#: Wind speed (kt) a storm's intensity decays *toward* while stalled over
#: land. Deliberately kept a safety margin BELOW DISSIPATION_THRESHOLD_KT
#: rather than equal to it: the decay curve below only ever approaches
#: its target asymptotically and mathematically never crosses it, so if
#: the residual equaled the dissipation threshold a storm continuously
#: over land would converge toward -- but never trigger -- lysis, and
#: would wander (position deltas are still sampled every step) at a
#: near-floor intensity for the rest of its allotted lifetime instead of
#: actually dissipating. The 5 kt margin lets a typical storm cross under
#: the threshold within roughly 1-2 days of continuous land interaction.
_LAND_RESIDUAL_WIND_KT = DISSIPATION_THRESHOLD_KT - 5.0

#: Time (hours) for a storm to lose half the intensity gap between its
#: current wind and the residual value, while continuously over land.
#: ~18h is a rough, deliberately-simple stand-in for the real,
#: storm-size- and terrain-dependent decay timescale.
_LAND_DECAY_HALF_LIFE_HOURS = 18.0

_LAND_DECAY_RATE = math.log(2.0) / _LAND_DECAY_HALF_LIFE_HOURS


def decay_wind_over_land(current_wind_kt: float, time_step_hours: float) -> float:
    """Apply one time step of exponential inland weakening.

    ``new_wind = R + (current_wind - R) * exp(-rate * dt)``, where ``R``
    is the residual wind speed and ``rate`` is calibrated to the half
    life above. Only ever *reduces* wind (or leaves it unchanged if
    already at/below the residual) -- it is applied instead of, not in
    addition to, the statistical transition model for steps where the
    storm center is over land.
    """
    decay_factor = math.exp(-_LAND_DECAY_RATE * time_step_hours)
    new_wind = _LAND_RESIDUAL_WIND_KT + (current_wind_kt - _LAND_RESIDUAL_WIND_KT) * decay_factor
    return min(current_wind_kt, new_wind)
