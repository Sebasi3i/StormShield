"""Build Florida hurricane ASOS 1-minute gust calibration tables.

Inputs (raw/):
  fl_hurricanes_asos1min_raw.csv.gz  IEM copy of NCEI ASOS 1-minute data (sectioned file)
  hurdat2_fl_subset.txt, hurdat2_fl_extra.txt  NHC HURDAT2 rows for the 11 storms

Outputs (out/): see README.md
"""
import gzip
import io
import pathlib

import numpy as np
import pandas as pd
from global_land_mask import globe

ROOT = pathlib.Path(__file__).parent
RAW = ROOT / "raw"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

EARTH_R_KM = 6371.0
NM_KM = 1.852
MIN_MEAN_KT = 10  # threshold for the calibration-ready pairs table


# ---------------------------------------------------------------- raw ASOS
def read_sections(path):
    text = gzip.open(path, "rt").read()
    sections = {}
    for block in text.split("### ")[1:]:
        head, _, body = block.partition("\n")
        sections[head.strip()] = body
    return sections


sec = read_sections(RAW / "fl_hurricanes_asos1min_raw.csv.gz")
windows = {}
for ln in sec["MANIFEST"].splitlines():
    if ln.startswith("window,"):
        _, k, s, e = ln.split(",")
        windows[k] = (pd.Timestamp(s), pd.Timestamp(e))

stations = pd.read_csv(io.StringIO(sec["STATIONS"]))

frames = []
for key, body in sec.items():
    if not key.startswith("STORM "):
        continue
    storm_key = key.split(" ", 1)[1]
    df = pd.read_csv(io.StringIO(body), na_values=["M"], keep_default_na=False)
    df = df.rename(columns={"valid(UTC)": "time_utc"})
    df.insert(0, "storm_key", storm_key)
    frames.append(df)
obs = pd.concat(frames, ignore_index=True)
obs["time_utc"] = pd.to_datetime(obs["time_utc"], utc=True)
obs["storm_id"] = obs["storm_key"].str.split("_").str[0]
obs["storm_name"] = obs["storm_key"].str.split("_").str[1]
for c in ["drct", "sknt", "gust_drct", "gust_sknt", "pres1", "precip"]:
    obs[c] = pd.to_numeric(obs[c], errors="coerce")
obs = obs.sort_values(["storm_key", "station", "time_utc"]).drop_duplicates(
    ["storm_key", "station", "time_utc"]
)
obs = obs.reset_index(drop=True)
print("rows", len(obs))

# ------------------------------------------- field-shift misparse recovery
# In some stretches IEM's parser of the NCEI page-1 line is off by one field:
#   drct <- true 2-min speed, sknt <- a direction (most likely the gust
#   direction; lost if >125), gust_drct <- true gust speed,
#   gust_sknt <- runway number (stuck constant). The 2-min mean direction is lost.
# Signature: gust_sknt identical for >=10 consecutive rows, value <=36, and
# sknt > gust_sknt (or sknt missing) in most rows of the run.
key = obs["storm_key"] + "|" + obs["station"]
newrun = (obs["gust_sknt"] != obs["gust_sknt"].shift()) | (key != key.shift())
run_id = newrun.cumsum()
suspect_row = obs["sknt"].isna() | (obs["sknt"] > obs["gust_sknt"])
run = pd.DataFrame({"run": run_id, "g": obs["gust_sknt"], "s": suspect_row}).groupby("run").agg(
    n=("g", "size"), g=("g", "first"), frac=("s", "mean")
)
shift_runs = run[(run["n"] >= 10) & (run["g"] <= 36) & (run["frac"] >= 0.5)].index
shifted = run_id.isin(shift_runs) & obs["gust_sknt"].notna()
obs["field_shift_recovered"] = shifted
shift_dir = obs.loc[shifted, "sknt"].copy()
obs.loc[shifted, "sknt"] = obs.loc[shifted, "drct"].where(obs.loc[shifted, "drct"] <= 125)
obs.loc[shifted, "drct"] = np.nan
obs.loc[shifted, "gust_sknt"] = obs.loc[shifted, "gust_drct"].where(obs.loc[shifted, "gust_drct"] <= 125)
obs.loc[shifted, "gust_drct"] = shift_dir
print("field-shift rows recovered:", int(shifted.sum()), "in", len(shift_runs), "runs")

# ------------------------------------------------ gust paired to 2-min mean
# ASOS reports a 2-min mean each minute but the peak gust only for the past
# 1 minute, so the gust belonging to the 2-min window ending at t is
# max(gust[t], gust[t-1 min]) (same alignment as Kaplan/AOML README).
g = obs.groupby(["storm_key", "station"], sort=False)
prev_time = g["time_utc"].shift(1)
prev_gust = g["gust_sknt"].shift(1)
contiguous = (obs["time_utc"] - prev_time) == pd.Timedelta(minutes=1)
obs["gust_2min_kt"] = np.where(
    contiguous & obs["gust_sknt"].notna() & prev_gust.notna(),
    np.fmax(obs["gust_sknt"], prev_gust),
    np.nan,
)
with np.errstate(divide="ignore", invalid="ignore"):
    obs["gf_3s_2min"] = np.where(obs["sknt"] > 0, obs["gust_2min_kt"] / obs["sknt"], np.nan)

# ------------------------------------------------------------- best tracks
def parse_hurdat(paths):
    rows = []
    for p in paths:
        sid = name = None
        for ln in open(p):
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith("#"):
                sid, name = ln[1:].split(",")
                continue
            f = ln.split(",")
            lat = float(f[4][:-1]) * (1 if f[4].endswith("N") else -1)
            lon = float(f[5][:-1]) * (-1 if f[5].endswith("W") else 1)
            rows.append(
                dict(
                    storm_id=sid, storm_name=name,
                    time_utc=pd.Timestamp(f"{f[0]} {f[1][:2]}:{f[1][2:]}", tz="UTC"),
                    record=f[2], status=f[3], lat=lat, lon=lon,
                    vmax_kt=float(f[6]), pmin_mb=float(f[7]),
                    r34_ne=float(f[8]), r34_se=float(f[9]), r34_sw=float(f[10]), r34_nw=float(f[11]),
                    r50_ne=float(f[12]), r50_se=float(f[13]), r50_sw=float(f[14]), r50_nw=float(f[15]),
                    r64_ne=float(f[16]), r64_se=float(f[17]), r64_sw=float(f[18]), r64_nw=float(f[19]),
                    rmw_nm=float(f[20]),
                )
            )
    bt = pd.DataFrame(rows).drop_duplicates(["storm_id", "time_utc"]).sort_values(["storm_id", "time_utc"])
    bt.loc[bt["rmw_nm"] < 0, "rmw_nm"] = np.nan
    bt.loc[bt["pmin_mb"] < 0, "pmin_mb"] = np.nan
    return bt.reset_index(drop=True)


bt = parse_hurdat([RAW / "hurdat2_fl_subset.txt", RAW / "hurdat2_fl_extra.txt"])
bt.to_csv(OUT / "best_tracks.csv", index=False, date_format="%Y-%m-%d %H:%M")


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    x = np.sin(dl) * np.cos(p2)
    y = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def epoch_s(ser):
    return (ser - EPOCH).dt.total_seconds().to_numpy()


def interp_valid(t, tk, yk):
    """Linear interp using only finite knots; NaN outside the finite range."""
    m = np.isfinite(yk)
    if m.sum() == 0:
        return np.full_like(t, np.nan, dtype=float)
    out = np.interp(t, tk[m], yk[m])
    out[(t < tk[m].min()) | (t > tk[m].max())] = np.nan
    return out


st_idx = stations.set_index("sid")
obs["st_lat"] = obs["station"].map(st_idx["lat"])
obs["st_lon"] = obs["station"].map(st_idx["lon"])
assert obs["st_lat"].notna().all()

geo_cols = [
    "ctr_lat", "ctr_lon", "dist_km", "azimuth_from_center_deg", "storm_heading_deg",
    "storm_speed_kt", "rel_azimuth_deg", "vmax_kt", "pmin_mb", "rmw_nm",
    "r34_quad_nm", "r50_quad_nm", "r64_quad_nm", "status",
]
for c in geo_cols:
    obs[c] = np.nan
obs["status"] = obs["status"].astype(object)

for sid, sub in bt.groupby("storm_id"):
    m = obs["storm_id"] == sid
    t = epoch_s(obs.loc[m, "time_utc"])
    tk = epoch_s(sub["time_utc"])
    assert t.min() >= tk.min() + 5400 and t.max() <= tk.max() - 5400, sid
    lat = np.interp(t, tk, sub["lat"].to_numpy())
    lon = np.interp(t, tk, sub["lon"].to_numpy())
    # motion from positions 90 min either side
    la0, lo0 = np.interp(t - 5400, tk, sub["lat"]), np.interp(t - 5400, tk, sub["lon"])
    la1, lo1 = np.interp(t + 5400, tk, sub["lat"]), np.interp(t + 5400, tk, sub["lon"])
    heading = bearing_deg(la0, lo0, la1, lo1)
    speed = haversine_km(la0, lo0, la1, lo1) / 3.0 / NM_KM
    slat, slon = obs.loc[m, "st_lat"].to_numpy(), obs.loc[m, "st_lon"].to_numpy()
    dist = haversine_km(lat, lon, slat, slon)
    az = bearing_deg(lat, lon, slat, slon)
    quad = np.select([az < 90, az < 180, az < 270], ["ne", "se", "sw"], "nw")
    vals = dict(
        ctr_lat=lat, ctr_lon=lon, dist_km=dist, azimuth_from_center_deg=az,
        storm_heading_deg=heading, storm_speed_kt=speed,
        rel_azimuth_deg=(az - heading) % 360,
        vmax_kt=np.interp(t, tk, sub["vmax_kt"]),
        pmin_mb=interp_valid(t, tk, sub["pmin_mb"].to_numpy()),
        rmw_nm=interp_valid(t, tk, sub["rmw_nm"].to_numpy()),
    )
    for thr in ("34", "50", "64"):
        q = {k: np.interp(t, tk, sub[f"r{thr}_{k}"]) for k in ("ne", "se", "sw", "nw")}
        vals[f"r{thr}_quad_nm"] = np.select(
            [quad == "ne", quad == "se", quad == "sw"], [q["ne"], q["se"], q["sw"]], q["nw"]
        )
    idx = np.searchsorted(tk, t, side="right") - 1
    vals["status"] = sub["status"].to_numpy()[np.clip(idx, 0, len(tk) - 1)]
    for c, v in vals.items():
        obs.loc[m, c] = v

obs["r_over_rmw"] = obs["dist_km"] / (obs["rmw_nm"] * NM_KM)

# ------------------------------------------- station exposure (ocean fetch)
# Proxy only: GLOBE 1-km land/ocean mask (inland lakes count as land).
SECTOR_W = 10
fetch_rows = []
for _, s in stations.iterrows():
    for sec_c in range(0, 360, SECTOR_W):
        pts_lat, pts_lon, dists = [], [], []
        for db in np.linspace(-SECTOR_W / 2, SECTOR_W / 2, 5):
            b = np.radians(sec_c + db)
            for d in np.arange(0.5, 20.01, 0.5):
                dlat = d * np.cos(b) / 111.195
                dlon = d * np.sin(b) / (111.195 * np.cos(np.radians(s["lat"])))
                pts_lat.append(s["lat"] + dlat)
                pts_lon.append(s["lon"] + dlon)
                dists.append(d)
        ocean = globe.is_ocean(np.array(pts_lat), np.array(pts_lon))
        dists = np.array(dists)
        fetch_rows.append(
            dict(
                sid=s["sid"], sector_center_deg=sec_c,
                ocean_frac_0_3km=ocean[dists <= 3].mean(),
                ocean_frac_0_10km=ocean[dists <= 10].mean(),
                ocean_frac_0_20km=ocean.mean(),
            )
        )
fetch = pd.DataFrame(fetch_rows)

# distance to nearest ocean pixel (72 rays, 0.25 km steps to 60 km)
d2o = {}
for _, s in stations.iterrows():
    best = np.inf
    for b in np.radians(np.arange(0, 360, 5)):
        d = np.arange(0.25, 60.01, 0.25)
        la = s["lat"] + d * np.cos(b) / 111.195
        lo = s["lon"] + d * np.sin(b) / (111.195 * np.cos(np.radians(s["lat"])))
        hit = np.nonzero(globe.is_ocean(la, lo))[0]
        if hit.size:
            best = min(best, d[hit[0]])
    d2o[s["sid"]] = best if np.isfinite(best) else np.nan
stations["dist_to_ocean_km"] = stations["sid"].map(d2o)
stations["on_ocean_pixel"] = globe.is_ocean(stations["lat"].to_numpy(), stations["lon"].to_numpy())

# join upwind sector (drct = direction wind blows FROM)
sector = (np.round(obs["drct"].fillna(obs["gust_drct"]) / SECTOR_W) * SECTOR_W) % 360
obs["_sector"] = sector
fetch_j = fetch.rename(columns={"sid": "station", "sector_center_deg": "_sector"})
obs = obs.merge(fetch_j, on=["station", "_sector"], how="left").drop(columns="_sector")
obs["dist_to_ocean_km"] = obs["station"].map(stations.set_index("sid")["dist_to_ocean_km"])

# ------------------------------------------------------------- QC flags
obs["qc_no_mean"] = obs["sknt"].isna()
obs["qc_no_gust_pair"] = obs["gust_2min_kt"].isna()
obs["qc_gust_lt_mean"] = obs["gust_2min_kt"] < obs["sknt"]
obs["qc_gf_extreme"] = (obs["sknt"] >= MIN_MEAN_KT) & ((obs["gf_3s_2min"] > 2.5) | (obs["gf_3s_2min"] < 1.0))
# isolated spikes: >= 25 kt (gust) / >= 20 kt (2-min mean) above both neighbours
grp = obs.groupby(["storm_key", "station"], sort=False)
g = grp["gust_sknt"]
nb_max = np.fmax(g.shift(1), g.shift(-1))
obs["qc_gust_spike"] = (obs["gust_sknt"] - nb_max) >= 25
m = grp["sknt"]
nb_max_m = np.fmax(m.shift(1), m.shift(-1))
obs["qc_mean_spike"] = (obs["sknt"] - nb_max_m) >= 20
obs["qc_bad_value"] = obs["qc_gust_lt_mean"] | obs["qc_gust_spike"] | obs["qc_mean_spike"]

# ------------------------------------------------------------- coverage
cov_rows = []
for (sk, stn), sub in obs.groupby(["storm_key", "station"]):
    t0, t1 = windows[sk]
    expected = int((t1 - t0) / pd.Timedelta(minutes=1))
    sub = sub.sort_values("time_utc")
    valid = sub[sub["sknt"].notna() & ~sub["qc_bad_value"]]
    # gaps >= 30 min in valid mean-wind record (incl. window edges)
    times = pd.concat([pd.Series([t0 - pd.Timedelta(minutes=1)]), valid["time_utc"], pd.Series([t1])]).reset_index(drop=True)
    gaps = times.diff().dt.total_seconds().div(60).sub(1)
    big = gaps[gaps >= 30]
    outage_strong = False
    longest_gap = float(gaps.max()) if len(gaps) else float(expected)
    for i in big.index:
        before = valid[(valid["time_utc"] <= times[i - 1]) & (valid["time_utc"] > times[i - 1] - pd.Timedelta(minutes=10))] if i >= 1 else valid.iloc[0:0]
        if len(before) and before["sknt"].max() >= 50:
            outage_strong = True
    imax = valid["sknt"].idxmax() if len(valid) else None
    gvalid = sub[sub["gust_sknt"].notna() & ~sub["qc_bad_value"]]
    gmax = gvalid["gust_sknt"].idxmax() if len(gvalid) else None
    cov_rows.append(
        dict(
            storm_key=sk, station=stn, expected_min=expected, rows=len(sub),
            frac_mean_valid=round(len(valid) / expected, 3),
            frac_gust_pair_valid=round((sub["gust_2min_kt"].notna() & ~sub["qc_bad_value"]).sum() / expected, 3),
            rows_field_shift_recovered=int(sub["field_shift_recovered"].sum()),
            rows_flagged_bad=int(sub["qc_bad_value"].sum()),
            longest_gap_min=longest_gap,
            gap_after_strong_wind=outage_strong,
            max_mean_kt=valid["sknt"].max() if len(valid) else np.nan,
            time_max_mean=sub.loc[imax, "time_utc"] if imax is not None else pd.NaT,
            max_gust_kt=gvalid["gust_sknt"].max() if len(gvalid) else np.nan,
            time_max_gust=sub.loc[gmax, "time_utc"] if gmax is not None else pd.NaT,
            min_dist_km=round(sub["dist_km"].min(), 1),
            min_pres_inhg=sub["pres1"].min(),
            n_pairs_ge10kt=int(((valid["sknt"] >= 10) & valid["gust_2min_kt"].notna()).sum()),
            n_pairs_ge34kt=int(((valid["sknt"] >= 34) & valid["gust_2min_kt"].notna()).sum()),
        )
    )
cov = pd.DataFrame(cov_rows).sort_values(["storm_key", "max_mean_kt"], ascending=[True, False])
cov.to_csv(OUT / "coverage_by_storm_station.csv", index=False, date_format="%Y-%m-%d %H:%M")

# ------------------------------------------------------------- outputs
cols = [
    "storm_id", "storm_name", "station", "time_utc",
    "sknt", "drct", "gust_sknt", "gust_drct", "gust_2min_kt", "gf_3s_2min",
    "pres1", "precip",
    "st_lat", "st_lon", "dist_to_ocean_km", "ocean_frac_0_3km", "ocean_frac_0_10km", "ocean_frac_0_20km",
    "ctr_lat", "ctr_lon", "dist_km", "azimuth_from_center_deg", "rel_azimuth_deg",
    "storm_heading_deg", "storm_speed_kt", "status", "vmax_kt", "pmin_mb", "rmw_nm", "r_over_rmw",
    "r34_quad_nm", "r50_quad_nm", "r64_quad_nm",
    "field_shift_recovered",
    "qc_no_mean", "qc_no_gust_pair", "qc_gust_lt_mean", "qc_gust_spike", "qc_mean_spike", "qc_gf_extreme",
]
full = obs[cols].rename(
    columns={
        "sknt": "mean2min_kt", "drct": "mean2min_dir_deg", "gust_sknt": "gust1min_peak_kt",
        "gust_drct": "gust1min_dir_deg", "pres1": "pres_inhg", "precip": "precip_in",
    }
).sort_values(["storm_id", "station", "time_utc"])
round_map = {c: 3 for c in ["gf_3s_2min", "ocean_frac_0_3km", "ocean_frac_0_10km", "ocean_frac_0_20km"]}
round_map.update({c: 2 for c in ["dist_km", "dist_to_ocean_km", "azimuth_from_center_deg", "rel_azimuth_deg",
                                 "storm_heading_deg", "storm_speed_kt", "vmax_kt", "pmin_mb", "rmw_nm",
                                 "r34_quad_nm", "r50_quad_nm", "r64_quad_nm"]})
round_map.update({"r_over_rmw": 3, "ctr_lat": 4, "ctr_lon": 4})
full = full.round(round_map)
full.to_parquet(OUT / "fl_gust_1min.parquet", index=False)

qc_any = full[[c for c in full.columns if c.startswith("qc_")]].any(axis=1)
pairs = full[
    ~qc_any
    & (full["mean2min_kt"] >= MIN_MEAN_KT)
    & (full["time_utc"].dt.minute % 2 == 0)  # non-overlapping 2-min windows
].drop(columns=[c for c in full.columns if c.startswith("qc_")])
pairs.to_csv(OUT / "fl_gust_pairs_2min_qc.csv.gz", index=False, date_format="%Y-%m-%d %H:%M", compression="gzip")

st_out = stations.copy()
st_cov = cov.groupby("station").agg(
    storms_with_data=("storm_key", "nunique"), max_mean_kt=("max_mean_kt", "max"),
    max_gust_kt=("max_gust_kt", "max"), pairs_ge34kt=("n_pairs_ge34kt", "sum"),
)
st_out = st_out.merge(st_cov, left_on="sid", right_index=True, how="left")
st_out["storms_with_data"] = st_out["storms_with_data"].fillna(0).astype(int)
st_out.to_csv(OUT / "stations.csv", index=False)
fetch.round(3).to_csv(OUT / "station_ocean_fetch_by_sector.csv", index=False)

print("full", full.shape, "pairs", pairs.shape)
print(pd.Series({c: int(full[c].sum()) for c in full.columns if c.startswith("qc_")}))
