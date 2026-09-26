"""Plots for exploring Monte Carlo hurricane simulations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .landmask import is_over_land

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from .simulator import SimulationResult

__all__ = ["plot_simulation"]

_TRACK_COLUMNS = {"storm_id", "timestamp", "latitude", "longitude", "max_wind_kt"}
_SUMMARY_COLUMNS = {"storm_id", "duration_hours", "max_wind_kt"}


def _summary_from_tracks(tracks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for storm_id, storm in tracks.groupby("storm_id", sort=False):
        timestamps = pd.to_datetime(storm["timestamp"])
        rows.append(
            {
                "storm_id": storm_id,
                "duration_hours": (
                    timestamps.max() - timestamps.min()
                ).total_seconds()
                / 3600.0,
                "max_wind_kt": storm["max_wind_kt"].max(),
                "landfall": (
                    bool(
                        (
                            storm["is_over_land"]
                            & (storm["max_wind_kt"] >= 34.0)
                        ).any()
                    )
                    if "is_over_land" in storm
                    else None
                ),
            }
        )
    return pd.DataFrame(rows)


def plot_simulation(
    result: SimulationResult | pd.DataFrame,
    summary: pd.DataFrame | None = None,
    *,
    max_tracks: int = 100,
    seed: int = 42,
) -> Figure:
    """Plot track density, representative storm tracks, and Monte Carlo outcomes.

    Parameters
    ----------
    result:
        A :class:`SimulationResult` or the track DataFrame returned by
        :func:`simulate_hurricanes`.
    summary:
        Optional per-storm summary DataFrame. Required when ``result`` is
        a DataFrame and summary distributions should use its values.
    max_tracks:
        Maximum number of individual paths to draw over the all-track
        density. Sampling is reproducible with ``seed``.
    seed:
        Random seed used only to select the displayed individual tracks.

    Returns
    -------
    matplotlib.figure.Figure
        The caller can display it with ``figure.show()`` or save it with
        ``figure.savefig(...)``.
    """
    if isinstance(result, pd.DataFrame):
        tracks = result
    else:
        tracks = result.tracks
        if summary is None:
            summary = result.summary

    missing = _TRACK_COLUMNS.difference(tracks.columns)
    if missing:
        raise ValueError(f"tracks is missing required columns: {sorted(missing)}")
    if tracks.empty:
        raise ValueError("tracks must contain at least one simulated track row")
    if not isinstance(max_tracks, int) or isinstance(max_tracks, bool) or max_tracks < 1:
        raise ValueError("max_tracks must be a positive integer")

    if summary is None:
        summary = _summary_from_tracks(tracks)
    else:
        missing = _SUMMARY_COLUMNS.difference(summary.columns)
        if missing:
            raise ValueError(f"summary is missing required columns: {sorted(missing)}")
    if summary.empty:
        raise ValueError("summary must contain at least one simulated storm")
    if summary["storm_id"].duplicated().any():
        raise ValueError("summary must contain at most one row per storm_id")
    missing_summaries = set(tracks["storm_id"].unique()).difference(summary["storm_id"])
    if missing_summaries:
        raise ValueError(
            "summary is missing simulated storms: "
            f"{sorted(missing_summaries, key=str)}"
        )

    try:
        import matplotlib.pyplot as plt
        from matplotlib.colors import Normalize
        from matplotlib.lines import Line2D
    except ImportError as exc:
        raise ImportError(
            "Plotting requires matplotlib. Install it with: "
            "pip install hurricane-simulator[visualization]"
        ) from exc

    storm_ids = tracks["storm_id"].drop_duplicates().to_numpy()
    rng = np.random.default_rng(seed)
    displayed_ids = rng.choice(
        storm_ids, size=min(max_tracks, len(storm_ids)), replace=False
    )
    selected = set(displayed_ids)
    all_lons = tracks["longitude"].to_numpy(dtype=float)
    all_lats = tracks["latitude"].to_numpy(dtype=float)
    lon_min = max(-180.0, float(np.nanmin(all_lons)) - 5.0)
    lon_max = min(180.0, float(np.nanmax(all_lons)) + 5.0)
    lat_min = max(-60.0, float(np.nanmin(all_lats)) - 5.0)
    lat_max = min(75.0, float(np.nanmax(all_lats)) + 5.0)

    figure, (map_ax, intensity_ax, duration_ax) = plt.subplots(
        1,
        3,
        figsize=(16, 6),
        gridspec_kw={"width_ratios": [2.2, 1, 1]},
        constrained_layout=True,
    )

    map_lons = np.arange(np.floor(lon_min), np.ceil(lon_max) + 0.5, 0.5)
    map_lats = np.arange(np.floor(lat_min), np.ceil(lat_max) + 0.5, 0.5)
    land_lons, land_lats = np.meshgrid(map_lons, map_lats)
    land = is_over_land(land_lats, land_lons)
    map_ax.contourf(
        land_lons,
        land_lats,
        land.astype(int),
        levels=[0.5, 1.5],
        colors=["#e6e2d8"],
        alpha=0.8,
        zorder=0,
    )
    density = map_ax.hexbin(
        all_lons,
        all_lats,
        gridsize=55,
        mincnt=1,
        cmap="Blues",
        linewidths=0,
        alpha=0.75,
        zorder=1,
    )
    figure.colorbar(density, ax=map_ax, label="Track observations")

    peak_by_storm = summary.set_index("storm_id")["max_wind_kt"]
    color_norm = Normalize(
        vmin=float(summary["max_wind_kt"].min()),
        vmax=max(
            float(summary["max_wind_kt"].max()),
            float(summary["max_wind_kt"].min()) + 1,
        ),
    )
    cmap = plt.get_cmap("plasma")
    figure.colorbar(
        plt.cm.ScalarMappable(norm=color_norm, cmap=cmap),
        ax=map_ax,
        label="Sampled storm peak wind (kt)",
    )
    for storm_id, storm in tracks[tracks["storm_id"].isin(selected)].groupby(
        "storm_id", sort=False
    ):
        storm = storm.sort_values("timestamp")
        color = cmap(color_norm(float(peak_by_storm.loc[storm_id])))
        map_ax.plot(
            storm["longitude"],
            storm["latitude"],
            color=color,
            linewidth=1.15,
            alpha=0.85,
            zorder=3,
        )
        map_ax.scatter(
            storm["longitude"].iloc[0],
            storm["latitude"].iloc[0],
            color="#16803c",
            s=15,
            edgecolor="white",
            linewidth=0.4,
            zorder=4,
        )
        map_ax.scatter(
            storm["longitude"].iloc[-1],
            storm["latitude"].iloc[-1],
            color="#c43c39",
            s=15,
            edgecolor="white",
            linewidth=0.4,
            zorder=4,
        )

    map_ax.set(
        xlim=(lon_min, lon_max),
        ylim=(lat_min, lat_max),
        xlabel="Longitude (°; west negative)",
        ylabel="Latitude (°N)",
        title=f"Atlantic storm tracks ({len(storm_ids):,} simulated)",
    )
    map_ax.grid(color="white", linewidth=0.6, alpha=0.7)
    map_ax.legend(
        handles=[
            Line2D(
                [0],
                [0],
                color="#555555",
                linewidth=1.2,
                label=f"Sampled paths ({len(selected)})",
            ),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#16803c", label="Genesis"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#c43c39", label="Last point"),
        ],
        loc="lower left",
        fontsize="small",
        framealpha=0.9,
    )

    intensity_ax.hist(
        summary["max_wind_kt"], bins=18, color="#6f58a5", edgecolor="white"
    )
    intensity_ax.set(
        xlabel="Peak wind (kt)",
        ylabel="Storm count",
        title="Peak intensity",
    )
    intensity_ax.grid(axis="y", alpha=0.25)

    duration_ax.hist(
        summary["duration_hours"] / 24.0,
        bins=18,
        color="#278c8c",
        edgecolor="white",
    )
    duration_ax.set(
        xlabel="Duration (days)",
        ylabel="Storm count",
        title="Storm duration",
    )
    duration_ax.grid(axis="y", alpha=0.25)

    if "landfall" in summary and summary["landfall"].notna().any():
        landfall_rate = summary["landfall"].astype(bool).mean()
        duration_ax.text(
            0.97,
            0.96,
            f"Landfall rate: {landfall_rate:.1%}",
            transform=duration_ax.transAxes,
            ha="right",
            va="top",
            fontsize="small",
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
        )
    return figure
