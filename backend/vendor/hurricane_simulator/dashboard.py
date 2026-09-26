"""Local browser dashboard for interactive, single-storm predictions."""

from __future__ import annotations

import json
import logging
import math
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .data import prepare_historical_data
from .genesis import GenesisRegion, GenesisSample
from .landmask import is_over_land
from .schema import MAX_PHYSICAL_WIND_KT
from .simulator import _finalize_track_columns, _simulate_single_track
from .transition import AnalogTransitionModel

if TYPE_CHECKING:
    from pandas import DataFrame

__all__ = ["launch_dashboard"]

_LOG = logging.getLogger(__name__)
_MAP_BOUNDS = {"west": -105.0, "east": -5.0, "south": 5.0, "north": 55.0}
_MAP_WIDTH = 900
_MAP_HEIGHT = 560
_TIME_STEP_HOURS = 6.0
_MAX_DURATION_DAYS = 25.0

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hurricane Track Predictor</title>
<style>
:root{color-scheme:dark;--bg:#09131f;--panel:#101f30;--line:#24384d;--muted:#9fb2c7;--text:#eaf2fa;--cyan:#65d4e8;--green:#79d7a2}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
header{padding:22px clamp(16px,4vw,48px) 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:end;gap:20px}
h1{font-size:clamp(22px,3vw,32px);margin:0;letter-spacing:-.03em}header p{color:var(--muted);margin:4px 0 0}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:18px;padding:18px clamp(12px,3vw,36px) 32px;max-width:1600px;margin:auto}
.map-panel,.controls,.stats,.notice{background:var(--panel);border:1px solid var(--line);border-radius:12px}
.map-panel{padding:12px;min-width:0}.map-head{display:flex;justify-content:space-between;align-items:center;padding:2px 4px 10px;gap:12px}.map-head strong{font-size:16px}.map-head span{color:var(--muted);font-size:13px}
#map{display:block;width:100%;height:auto;aspect-ratio:900/560;background:#102f48;border-radius:8px;cursor:crosshair;touch-action:manipulation}
.side{display:flex;flex-direction:column;gap:14px}.controls,.stats{padding:16px}
h2{font-size:16px;margin:0 0 12px}.field{margin:14px 0}.field label{display:flex;justify-content:space-between;gap:8px;margin-bottom:6px;font-size:13px;color:#cbd8e5}
input[type=range]{width:100%;accent-color:var(--cyan)}input[type=date],input[type=number]{width:100%;background:#0a1725;border:1px solid var(--line);border-radius:6px;padding:9px;color:var(--text)}
.buttons{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:16px}button{border:1px solid var(--line);border-radius:7px;background:#1a3045;color:var(--text);padding:9px 12px;font-weight:600;cursor:pointer}button:hover:not(:disabled){border-color:var(--cyan)}button:disabled{opacity:.45;cursor:default}#start{grid-column:1/-1;background:#087d90;border-color:#1598aa}
.status{min-height:42px;color:var(--muted);font-size:13px;margin-top:12px}.status.error{color:#ff9a9a}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.stat{background:#0b1927;border-radius:7px;padding:10px}.stat span{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}.stat strong{display:block;font-size:19px;margin-top:2px}
.notice{grid-column:1/-1;padding:12px 15px;color:#c1d0dd;font-size:12px}.notice strong{color:#f5c76a}
.legend{display:flex;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:12px}.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}.legend .track{background:#f8f2da}.legend .center{background:#ff5b5b}.legend .land{background:#6c766d}.legend .region{background:#3fae8c}
@media(max-width:900px){.layout{grid-template-columns:1fr}.side{display:grid;grid-template-columns:1fr 1fr}.notice{grid-column:1/-1}}
@media(max-width:560px){.side{display:flex}.map-head{align-items:start;flex-direction:column}.layout{padding:10px}.map-panel{padding:7px}}
</style>
</head>
<body>
<header><div><h1>Atlantic Track Predictor</h1><p>Click within the highlighted region to choose a starting point, then watch the analog forecast evolve.</p></div></header>
<main class="layout">
<section class="map-panel">
 <div class="map-head"><strong>Atlantic basin</strong><div class="legend"><span><i class="land"></i>land</span><span><i class="region"></i>historical region</span><span><i class="track"></i>track</span><span><i class="center"></i>storm center</span></div></div>
 <svg id="map" viewBox="0 0 900 560" role="img" aria-label="Clickable Atlantic map">
  <defs><radialGradient id="windfill"><stop offset="0" stop-color="#ff4c63" stop-opacity=".78"/><stop offset=".22" stop-color="#ff9d54" stop-opacity=".58"/><stop offset=".50" stop-color="#ffe477" stop-opacity=".35"/><stop offset=".78" stop-color="#66e3dc" stop-opacity=".21"/><stop offset="1" stop-color="#66e3dc" stop-opacity="0"/></radialGradient></defs>
  <g id="land"></g><g id="region"></g><g id="grid"></g><g id="field"></g><polyline id="track" fill="none" stroke="#fff4cf" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" opacity=".93"></polyline><circle id="genesis" r="4" fill="#80e4a5" stroke="#07131f" stroke-width="1.5" visibility="hidden"></circle><circle id="center" r="6" fill="#ff5b5b" stroke="white" stroke-width="1.6" visibility="hidden"></circle><g id="labels"></g>
 </svg>
</section>
<aside class="side">
 <section class="controls"><h2>Prediction setup</h2>
  <div class="field"><label for="wind">Initial maximum wind <b><span id="wind-value">45</span> kt</b></label><input id="wind" type="range" min="20" max="120" value="45"></div>
  <div class="field"><label for="date">Starting date</label><input id="date" type="date"></div>
  <div class="field"><label for="seed">Random seed</label><input id="seed" type="number" min="0" max="2147483647" value="42"></div>
  <div class="field"><label for="speed">Playback speed <b><span id="speed-value">1.0</span>×</b></label><input id="speed" type="range" min="1" max="10" value="5"></div>
  <div class="buttons"><button id="start" disabled>Predict from clicked point</button><button id="play" disabled>Pause</button><button id="step" disabled>Step forward</button><button id="reset">Clear</button></div>
  <div id="status" class="status" role="status" aria-live="polite">Loading the local basin map…</div>
 </section>
 <section class="stats"><h2>Storm state</h2><div class="stats-grid">
  <div class="stat"><span>Position</span><strong id="position">—</strong></div><div class="stat"><span>Maximum wind</span><strong id="intensity">—</strong></div>
  <div class="stat"><span>Category</span><strong id="category">—</strong></div><div class="stat"><span>Forecast step</span><strong id="forecast-step">—</strong></div>
  <div class="stat"><span>Simulation time</span><strong id="time">—</strong></div><div class="stat"><span>Center over land</span><strong id="land-state">—</strong></div>
 </div></section>
 <section class="notice"><strong>Visualization only:</strong> the map only accepts starting points within the tinted region -- historical Atlantic genesis locations plus a nearby buffer -- since storms have no real basis forming elsewhere. Tracks use the historical analog predictor; the colored radial wind overlay is illustrative and is not a validated wind-field, forecast, or hazard model.</section>
</aside></main>
<script>
(() => {
 const bounds={west:-105,east:-5,south:5,north:55}, W=900,H=560;
 const svg=document.getElementById('map'),land=document.getElementById('land'),region=document.getElementById('region'),grid=document.getElementById('grid');
 const track=document.getElementById('track'),field=document.getElementById('field'),center=document.getElementById('center'),genesis=document.getElementById('genesis');
 const $=id=>document.getElementById(id),start=$('start'),play=$('play'),stepButton=$('step'),status=$('status');
 let point=null,forecast=[],frame=0,timer=null,playing=false;
 let regionCells=new Set(),cellSize=1;
 const x=lon=>(lon-bounds.west)/(bounds.east-bounds.west)*W;
 const y=lat=>(bounds.north-lat)/(bounds.north-bounds.south)*H;
 function cellKey(longitude,latitude){return Math.floor((longitude-bounds.west)/cellSize)+','+Math.floor((latitude-bounds.south)/cellSize)}
 function isClickable(longitude,latitude){return regionCells.has(cellKey(longitude,latitude))}
 function node(name,attrs,parent){const el=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);parent.appendChild(el);return el}
 function say(message,error=false){status.textContent=message;status.classList.toggle('error',error)}
 function drawGrid(){
  for(let lon=-100;lon<0;lon+=10){const xx=x(lon);node('line',{x1:xx,y1:0,x2:xx,y2:H,stroke:'#7391a4','stroke-opacity':'.27','stroke-dasharray':'3 5'},grid);node('text',{x:xx+3,y:H-8,fill:'#afc4d0','font-size':12},grid).textContent=Math.abs(lon)+'°W'}
  for(let lat=10;lat<=50;lat+=10){const yy=y(lat);node('line',{x1:0,y1:yy,x2:W,y2:yy,stroke:'#7391a4','stroke-opacity':'.27','stroke-dasharray':'3 5'},grid);node('text',{x:7,y:yy-4,fill:'#afc4d0','font-size':12},grid).textContent=lat+'°N'}
 }
 function clearForecast(){if(timer)clearTimeout(timer);timer=null;playing=false;forecast=[];frame=0;track.setAttribute('points','');field.replaceChildren();center.setAttribute('visibility','hidden');genesis.setAttribute('visibility','hidden');play.disabled=true;stepButton.disabled=true;play.textContent='Pause';$('position').textContent='—';$('intensity').textContent='—';$('category').textContent='—';$('forecast-step').textContent='—';$('time').textContent='—';$('land-state').textContent='—'}
 function updateState(i){
  const row=forecast[i],cx=x(row.longitude),cy=y(row.latitude),v=Math.max(20,Number(row.max_wind_kt));
  center.setAttribute('cx',cx);center.setAttribute('cy',cy);center.setAttribute('visibility','visible');
  const previous=track.getAttribute('points');track.setAttribute('points',(previous?previous+' ':'')+cx+','+cy);
  if(i===0){genesis.setAttribute('cx',cx);genesis.setAttribute('cy',cy);genesis.setAttribute('visibility','visible')}
  field.replaceChildren();
  const radiusKm=110+v*2.6,degreeY=radiusKm/111,degreeX=degreeY/Math.max(.25,Math.cos(row.latitude*Math.PI/180));
  const rx=degreeX/(bounds.east-bounds.west)*W,ry=degreeY/(bounds.north-bounds.south)*H;
  node('ellipse',{cx,cy,rx,ry,fill:'url(#windfill)'},field);
  for(const threshold of [34,64,96,137]){if(v<threshold)continue;const f=Math.sqrt(Math.log(v/threshold)/Math.log(v/20));node('ellipse',{cx,cy,rx:Math.max(2,rx*f),ry:Math.max(2,ry*f),fill:'none',stroke:'#fff','stroke-opacity':'.62','stroke-width':'.8','stroke-dasharray':'3 4'},field)}
  $('position').textContent=row.latitude.toFixed(1)+'°N, '+Math.abs(row.longitude).toFixed(1)+'°W';
  $('intensity').textContent=Math.round(v)+' kt';
  $('category').textContent=row.category==='TD'?'Tropical depression':row.category==='TS'?'Tropical storm':'Category '+row.category;
  $('forecast-step').textContent=(i+1)+' / '+forecast.length;
  $('time').textContent=new Date(row.timestamp).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',timeZone:'UTC'})+' UTC';
  $('land-state').textContent=row.is_over_land?'Yes':'No';
  if(i===forecast.length-1){pause();say('Forecast complete — '+forecast.length+' six-hourly positions.');}
 }
 function pause(){if(timer)clearTimeout(timer);timer=null;playing=false;play.textContent='Play';}
 function animate(){if(!playing)return;if(frame<forecast.length){updateState(frame++);if(playing)timer=setTimeout(animate,Math.max(100,1100-Number($('speed').value)*100))}else pause()}
 function startPlayback(){if(frame>=forecast.length)frame=0;playing=true;play.textContent='Pause';animate()}
 async function predict(){
  if(!point){say('Click a point within the highlighted region first.',true);return}
  clearForecast();start.disabled=true;say('Running the analog predictor…');
  try{
   const response=await fetch('/api/predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:point.latitude,longitude:point.longitude,max_wind_kt:Number($('wind').value),seed:Number($('seed').value),timestamp:$('date').value+'T00:00:00'})});
   const data=await response.json();if(!response.ok)throw new Error(data.error||'Prediction request failed.');
   forecast=data.tracks;if(!forecast.length)throw new Error('Predictor returned no track positions.');
   play.disabled=false;stepButton.disabled=false;frame=0;say('Forecast generated. Playing six-hour steps…');startPlayback();
  }catch(error){say(error.message,true)}finally{start.disabled=false}
 }
 svg.addEventListener('click',event=>{
  const rect=svg.getBoundingClientRect(),sx=(event.clientX-rect.left)/rect.width*W,sy=(event.clientY-rect.top)/rect.height*H;
  const longitude=bounds.west+sx/W*(bounds.east-bounds.west),latitude=bounds.north-sy/H*(bounds.north-bounds.south);
  if(longitude<bounds.west||longitude>bounds.east||latitude<bounds.south||latitude>bounds.north)return;
  if(!isClickable(longitude,latitude)){say('No historical storms have formed near there. Click within the highlighted region.',true);return}
  point={latitude,longitude};clearForecast();start.disabled=false;
  $('position').textContent=latitude.toFixed(1)+'°N, '+Math.abs(longitude).toFixed(1)+'°W';
  node('circle',{cx:x(longitude),cy:y(latitude),r:5,fill:'#80e4a5',stroke:'#07131f','stroke-width':1.5},field);
  say('Start point selected. Click “Predict” to generate and animate a track.');
 });
 start.addEventListener('click',predict);
 play.addEventListener('click',()=>{if(playing)pause();else startPlayback()});
 stepButton.addEventListener('click',()=>{pause();if(forecast.length){if(frame>=forecast.length)frame=0;updateState(frame++)}});
 $('reset').addEventListener('click',()=>{point=null;clearForecast();field.replaceChildren();start.disabled=true;say('Click within the highlighted region to begin.')});
 $('wind').addEventListener('input',()=>$('wind-value').textContent=$('wind').value);
 $('speed').addEventListener('input',()=>$('speed-value').textContent=(Number($('speed').value)/5).toFixed(1));
 drawGrid();
 (async()=>{try{
  const r=await fetch('/api/map');if(!r.ok)throw new Error('Could not load the map data.');
  const data=await r.json();
  cellSize=data.cell_size;
  for(const c of data.land_cells)node('rect',{x:x(c.longitude),y:y(c.latitude+c.size),width:c.size/(bounds.east-bounds.west)*W+.7,height:c.size/(bounds.north-bounds.south)*H+.7,fill:'#626e6d','fill-opacity':'.72'},land);
  for(const c of data.region_cells){regionCells.add(cellKey(c.longitude,c.latitude));node('rect',{x:x(c.longitude),y:y(c.latitude+c.size),width:c.size/(bounds.east-bounds.west)*W+.7,height:c.size/(bounds.north-bounds.south)*H+.7,fill:'#3fae8c','fill-opacity':'.22'},region)}
  $('date').value=data.default_date;$('date').max=(data.default_date.slice(0,4)*1+2)+'-12-31';
  say('Click within the highlighted region to begin.');
 }catch(e){say(e.message,true)}})();
})();
</script>
</body></html>"""


def _make_prediction(
    transition_model: AnalogTransitionModel,
    genesis_region: GenesisRegion,
    payload: dict,
) -> list[dict]:
    try:
        latitude = float(payload["latitude"])
        longitude = float(payload["longitude"])
        max_wind_kt = float(payload["max_wind_kt"])
        seed = int(payload["seed"])
        timestamp = pd.Timestamp(payload["timestamp"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("latitude, longitude, max_wind_kt, seed, and timestamp are required.") from exc
    if not all(math.isfinite(value) for value in (latitude, longitude, max_wind_kt)):
        raise ValueError("Coordinates and initial wind must be finite numbers.")
    if not (_MAP_BOUNDS["south"] <= latitude <= _MAP_BOUNDS["north"]):
        raise ValueError("latitude must be between 5 and 55 degrees north.")
    if not (_MAP_BOUNDS["west"] <= longitude <= _MAP_BOUNDS["east"]):
        raise ValueError("longitude must be between 105 and 5 degrees west.")
    if not 20.0 <= max_wind_kt <= MAX_PHYSICAL_WIND_KT:
        raise ValueError(f"max_wind_kt must be between 20 and {MAX_PHYSICAL_WIND_KT:g} knots.")
    if not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be between 0 and 4294967295.")
    if pd.isna(timestamp):
        raise ValueError("timestamp must be a valid date and time.")
    timestamp = timestamp.floor("6h")

    # Authoritative server-side check: the client-side map only *shows*
    # the historical-plus-buffer region and pre-filters clicks against
    # it, but the API must not trust that -- reject any genesis point
    # that isn't actually within the historical footprint (or over
    # land, which real cyclones never form over regardless of distance
    # to the nearest historical point).
    if is_over_land(latitude, longitude):
        raise ValueError("Storms don't form over land -- pick a point over open water.")
    if not genesis_region.contains(latitude, longitude):
        distance = genesis_region.distance_to_nearest_historical_point_deg(latitude, longitude)
        raise ValueError(
            "This point is outside the historical Atlantic genesis region "
            f"({distance:.1f} deg from the nearest historical genesis "
            "location, beyond the plausible-neighbor buffer). Pick a "
            "point within the highlighted region."
        )

    rng = np.random.default_rng(seed)
    genesis = GenesisSample(latitude, longitude, max_wind_kt, timestamp)
    max_steps = int(round(_MAX_DURATION_DAYS * 24.0 / _TIME_STEP_HOURS))
    raw_rows = _simulate_single_track(
        "INTERACTIVE",
        genesis,
        transition_model,
        rng,
        _TIME_STEP_HOURS,
        max_steps,
    )
    track = _finalize_track_columns(raw_rows)
    return [
        {
            "step": int(row.step),
            "timestamp": row.timestamp.isoformat(),
            "latitude": float(row.latitude),
            "longitude": float(row.longitude),
            "max_wind_kt": float(row.max_wind_kt),
            "category": str(row.category),
            "is_over_land": bool(row.is_over_land),
        }
        for row in track.itertuples(index=False)
    ]


#: Grid resolution (degrees) shared by the land mask and the
#: historical-genesis-region overlays sent to the dashboard's map.
_MAP_CELL_SIZE_DEG = 1.0


def _map_grid(cell_size: float = _MAP_CELL_SIZE_DEG):
    """Lower-left corners and cell-center meshgrids covering _MAP_BOUNDS,
    shared by :func:`_land_cells` and :func:`_region_cells` so both
    overlays line up on the same grid."""
    longitudes = np.arange(_MAP_BOUNDS["west"], _MAP_BOUNDS["east"], cell_size)
    latitudes = np.arange(_MAP_BOUNDS["south"], _MAP_BOUNDS["north"], cell_size)
    lon_grid, lat_grid = np.meshgrid(
        longitudes + cell_size / 2,
        latitudes + cell_size / 2,
    )
    return longitudes, latitudes, lon_grid, lat_grid


def _cells_from_mask(mask: np.ndarray, longitudes: np.ndarray, latitudes: np.ndarray, cell_size: float) -> list[dict[str, float]]:
    rows, columns = np.nonzero(mask)
    return [
        {
            "longitude": float(longitudes[column]),
            "latitude": float(latitudes[row]),
            "size": cell_size,
        }
        for row, column in zip(rows, columns)
    ]


def _land_cells(cell_size: float = _MAP_CELL_SIZE_DEG) -> list[dict[str, float]]:
    longitudes, latitudes, lon_grid, lat_grid = _map_grid(cell_size)
    mask = is_over_land(lat_grid, lon_grid)
    return _cells_from_mask(mask, longitudes, latitudes, cell_size)


def _region_cells(genesis_region: GenesisRegion, cell_size: float = _MAP_CELL_SIZE_DEG) -> list[dict[str, float]]:
    """Grid cells that are both climatologically plausible genesis
    locations (within GenesisRegion's historical-plus-buffer footprint)
    and over open water -- i.e. exactly the set of cells the dashboard
    should let a user click in."""
    longitudes, latitudes, lon_grid, lat_grid = _map_grid(cell_size)
    in_region = genesis_region.contains(lat_grid.ravel(), lon_grid.ravel()).reshape(lat_grid.shape)
    over_water = ~is_over_land(lat_grid, lon_grid)
    mask = in_region & over_water
    return _cells_from_mask(mask, longitudes, latitudes, cell_size)


def launch_dashboard(
    historical_data: DataFrame,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Launch the local interactive Atlantic prediction dashboard.

    Click a location within the historical-genesis-region overlay (see
    :class:`hurricane_simulator.genesis.GenesisRegion`: historical
    Atlantic genesis points plus a plausible-neighbor buffer) to choose
    storm genesis; the browser then requests a seeded prediction from
    the fitted analog model and animates the resulting six-hourly
    positions. Points outside that region -- open land, or ocean far
    from any historical genesis point -- are rejected both by the map
    (clicks outside the highlighted area are ignored) and,
    authoritatively, by the server. The wind overlay is a visualization
    aid only, not a validated wind-field calculation.

    This function blocks until interrupted (Ctrl+C). By default, the
    server binds only to localhost.
    """
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535.")
    prepared = prepare_historical_data(historical_data)
    transition_model = AnalogTransitionModel.fit(prepared)
    genesis_region = GenesisRegion.fit(prepared)
    season_year = int(prepared["timestamp"].dt.year.max()) + 1
    default_date = pd.Timestamp(season_year, 8, 1).date().isoformat()
    land_cells = _land_cells()
    region_cells = _region_cells(genesis_region)

    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path == "/":
                encoded = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(encoded)
            elif self.path == "/api/map":
                self._send_json(
                    200,
                    {
                        "land_cells": land_cells,
                        "region_cells": region_cells,
                        "cell_size": _MAP_CELL_SIZE_DEG,
                        "default_date": default_date,
                    },
                )
            else:
                self._send_json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            if self.path != "/api/predict":
                self._send_json(404, {"error": "Not found."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 16_384:
                    raise ValueError("Request body must be between 1 and 16384 bytes.")
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                tracks = _make_prediction(transition_model, genesis_region, payload)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception:
                _LOG.exception("Interactive hurricane prediction failed.")
                self._send_json(500, {"error": "Prediction failed. Check the server log for details."})
                return
            self._send_json(200, {"tracks": tracks})

        def log_message(self, format: str, *args: object) -> None:
            _LOG.info("%s - %s", self.address_string(), format % args)

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    address = server.server_address
    url = f"http://{host}:{address[1]}/"
    print(f"Atlantic track dashboard running at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Atlantic track dashboard.")
    finally:
        server.server_close()
