"""The Florida outline: known places inside and outside, and the track-point test."""

from __future__ import annotations

import pytest

from app import florida


@pytest.mark.parametrize(
    "place, latitude, longitude",
    [
        ("Miami", 25.76, -80.19),
        ("Miami Beach", 25.79, -80.13),
        ("Tampa", 27.95, -82.46),
        ("St. Petersburg", 27.77, -82.64),
        ("Orlando", 28.54, -81.38),
        ("Jacksonville", 30.33, -81.66),
        ("Tallahassee", 30.44, -84.28),
        ("Pensacola", 30.42, -87.22),
        ("Naples", 26.14, -81.79),
        ("Key West", 24.56, -81.78),
        ("Cedar Key", 29.14, -83.04),
    ],
)
def test_florida_places_are_inside(place, latitude, longitude):
    assert florida.contains(latitude, longitude), place


@pytest.mark.parametrize(
    "place, latitude, longitude",
    [
        ("Mobile, AL", 30.69, -88.04),
        ("Dothan, AL", 31.22, -85.39),
        ("Valdosta, GA", 30.83, -83.28),
        ("Savannah, GA", 32.08, -81.10),
        ("Havana", 23.11, -82.37),
        ("Nassau", 25.05, -77.35),
        ("Open Gulf", 27.0, -85.0),
        ("Open Atlantic", 28.0, -78.0),
    ],
)
def test_places_outside_florida_are_outside(place, latitude, longitude):
    assert not florida.contains(latitude, longitude), place


def _point(latitude, longitude, wind, over_land=True):
    return {
        "latitude": latitude,
        "longitude": longitude,
        "max_wind_kt": wind,
        "is_over_land": over_land,
        "timestamp": "2026-09-10 00:00:00",
    }


def test_florida_points_need_land_florida_and_the_wind_threshold():
    track = [
        _point(26.0, -79.0, 120.0, over_land=False),  # strong, but at sea off Florida
        _point(23.1, -82.4, 120.0),  # strong, over land, but Cuba
        _point(25.8, -80.2, 95.9),  # Miami, just under Category 3
        _point(25.8, -80.2, 96.0),  # Miami, Category 3
        _point(27.9, -82.5, 110.0),  # Tampa
    ]

    points = florida.florida_points(track, 96.0)

    assert [point["max_wind_kt"] for point in points] == [96.0, 110.0]


def test_florida_points_is_empty_for_a_miss():
    assert florida.florida_points([_point(30.7, -88.0, 130.0)], 96.0) == []
