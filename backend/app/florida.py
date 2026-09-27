"""Where Florida is, for the hazard side of the platform.

The hurricane simulator knows land from sea but not one state from another, so a
"storm that hits Florida" needs an outline of the state. This one is a simplified
polygon of the coastline and the Alabama and Georgia borders, with the Keys as a thin
spur, in (longitude, latitude) pairs. It is deliberately a little generous along the
coast: a track point is judged against the simulator's own land mask as well, so the
outline only has to say "this land is Florida", never "this is land".

Precision is a few kilometres, which is coarser than a six-hourly track position anyway.
"""

from __future__ import annotations

# Clockwise from the Alabama border on the Gulf side, in (longitude, latitude).
FLORIDA_OUTLINE: list[tuple[float, float]] = [
    (-87.63, 31.00),  # Alabama border, north-west corner
    (-85.00, 31.00),  # along the Alabama line
    (-84.86, 30.71),  # Chattahoochee: the Georgia line begins
    (-83.00, 30.66),
    (-82.05, 30.60),
    (-81.45, 30.75),  # St. Marys entrance, Atlantic
    (-81.25, 29.65),  # St. Augustine
    (-80.55, 28.45),  # Cape Canaveral
    (-80.05, 26.95),  # Jupiter
    (-80.02, 26.30),  # Boca Raton
    (-80.10, 25.75),  # Miami
    (-80.30, 25.20),  # Key Largo
    (-81.00, 24.60),  # Middle Keys
    (-82.00, 24.45),  # Key West and beyond
    (-81.20, 25.00),  # back along the Florida Bay side
    (-81.25, 25.45),  # Cape Sable / Everglades
    (-81.40, 25.85),
    (-81.85, 26.15),  # Naples
    (-82.15, 26.50),  # Fort Myers / Sanibel
    (-82.30, 26.85),
    (-82.45, 27.20),  # Venice
    (-82.80, 27.55),  # Anna Maria
    (-82.90, 27.90),  # Pinellas
    (-82.80, 28.40),  # Hudson
    (-82.80, 29.00),  # Crystal River
    (-83.10, 29.12),  # Cedar Key
    (-83.35, 29.45),  # Horseshoe Beach
    (-83.50, 29.75),  # Big Bend
    (-84.05, 30.05),  # St. Marks
    (-84.45, 29.95),
    (-85.05, 29.60),  # Apalachicola
    (-85.45, 29.65),  # Cape San Blas
    (-85.70, 30.10),  # Panama City
    (-86.50, 30.35),  # Destin
    (-87.25, 30.30),  # Pensacola
    (-87.60, 30.25),  # Perdido Key
]


def contains(latitude: float, longitude: float) -> bool:
    """True when the point is inside the Florida outline (ray casting)."""
    inside = False
    count = len(FLORIDA_OUTLINE)
    for index in range(count):
        x1, y1 = FLORIDA_OUTLINE[index]
        x2, y2 = FLORIDA_OUTLINE[(index + 1) % count]
        if (y1 > latitude) != (y2 > latitude):
            crossing = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing:
                inside = not inside
    return inside


def florida_points(track: list[dict], min_wind_kt: float) -> list[dict]:
    """The track points over Florida land at or above `min_wind_kt`, in track order.

    A point counts when the simulator says it is over land AND it falls inside the
    Florida outline. Points at sea just off the coast do not count, however strong.
    """
    return [
        point
        for point in track
        if point["is_over_land"]
        and point["max_wind_kt"] >= min_wind_kt
        and contains(point["latitude"], point["longitude"])
    ]
