"""
SENTINEL — Simple Emergency Notification & Threat Intelligence Network
Aggregates: USGS earthquakes + NASA FIRMS wildfires + OpenAQ air quality
Serves unified GeoJSON API for any map frontend.
"""

import asyncio
import csv
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel")

app = FastAPI(title="SENTINEL API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Cache
_cache: dict = {"earthquakes": [], "fires": [], "air": [], "updated": None}
_cache_lock = asyncio.Lock()
FIRMS_KEY = "89bb28929af2f54edb9f18a7f4f65ae5"  # demo key from NASA


# ---------------------------------------------------------------------------
# Feed ingestion
# ---------------------------------------------------------------------------

async def fetch_usgs() -> list:
    """USGS M2.5+ earthquakes, last 24 hours."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
            data = r.json()
        return [
            {
                "type": "earthquake",
                "magnitude": f["properties"]["mag"],
                "place": f["properties"]["place"],
                "time": datetime.fromtimestamp(f["properties"]["time"] / 1000, tz=timezone.utc).isoformat(),
                "coordinates": f["geometry"]["coordinates"],
                "source": "USGS",
            }
            for f in data.get("features", [])
        ]
    except Exception as e:
        logger.error(f"USGS failed: {e}")
        return []


async def fetch_firms() -> list:
    """NASA FIRMS VIIRS 24h wildfire hotspots."""
    try:
        url = f"https://firms.modaps.eosdis.nasa.gov/api/country/csv/{FIRMS_KEY}/VIIRS_NOAA20_NRT/WORLD/1"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
        reader = csv.DictReader(io.StringIO(r.text))
        return [
            {
                "type": "wildfire",
                "brightness": float(row.get("bright_ti4", 0)),
                "date": row.get("acq_date", ""),
                "time": row.get("acq_time", ""),
                "coordinates": [float(row["longitude"]), float(row["latitude"])],
                "source": "NASA FIRMS",
            }
            for row in reader
            if float(row.get("bright_ti4", 0)) > 300
        ][:2000]  # cap at 2000 to keep payload manageable
    except Exception as e:
        logger.error(f"FIRMS failed: {e}")
        return []


async def fetch_openaq() -> list:
    """OpenAQ PM2.5 stations, most recently updated."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.openaq.org/v3/locations?limit=300&parameter_id=2&sort=desc(lastUpdated)"
            )
            data = r.json()
        results = []
        for loc in data.get("results", []):
            sensors = loc.get("sensors", [])
            if sensors and sensors[0].get("latest"):
                val = sensors[0]["latest"]["value"]
                results.append(
                    {
                        "type": "air_quality",
                        "pm25": val,
                        "location": loc.get("name") or loc.get("locality", ""),
                        "coordinates": [loc["coordinates"]["longitude"], loc["coordinates"]["latitude"]],
                        "source": "OpenAQ",
                    }
                )
        return results[:300]
    except Exception as e:
        logger.error(f"OpenAQ failed: {e}")
        return []


async def refresh_cache():
    global _cache
    async with _cache_lock:
        eq, fi, aq = await asyncio.gather(fetch_usgs(), fetch_firms(), fetch_openaq())
        _cache["earthquakes"] = eq
        _cache["fires"] = fi
        _cache["air"] = aq
        _cache["updated"] = datetime.now(timezone.utc).isoformat()
        total = len(eq) + len(fi) + len(aq)
        logger.info(f"Cache refreshed: {len(eq)} quakes + {len(fi)} fires + {len(aq)} air = {total} events")


# ---------------------------------------------------------------------------
# Background refresh loop
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    await refresh_cache()
    asyncio.create_task(_periodic_refresh())


async def _periodic_refresh():
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        await refresh_cache()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def get_events(
    types: Optional[str] = Query(None, description="Comma-separated: earthquake,wildfire,air_quality"),
    limit: int = Query(5000, ge=1, le=10000),
):
    events = []
    wanted = set(types.split(",")) if types else {"earthquake", "wildfire", "air_quality"}
    if "earthquake" in wanted:
        events.extend(_cache["earthquakes"])
    if "wildfire" in wanted:
        events.extend(_cache["fires"])
    if "air_quality" in wanted:
        events.extend(_cache["air"])
    return {
        "updated": _cache["updated"],
        "total": len(events),
        "events": events[:limit],
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "updated": _cache["updated"],
        "quakes": len(_cache["earthquakes"]),
        "fires": len(_cache["fires"]),
        "air": len(_cache["air"]),
    }


@app.get("/api/geojson")
async def get_geojson():
    features = []
    for e in _cache["earthquakes"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": e["coordinates"]},
            "properties": {"type": "earthquake", "magnitude": e["magnitude"], "place": e["place"], "time": e["time"]},
        })
    for e in _cache["fires"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": e["coordinates"]},
            "properties": {"type": "wildfire", "brightness": e["brightness"], "date": e["date"]},
        })
    for e in _cache["air"]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": e["coordinates"]},
            "properties": {"type": "air_quality", "pm25": e["pm25"], "location": e["location"]},
        })
    return {"type": "FeatureCollection", "features": features, "updated": _cache["updated"]}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return HTMLResponse(FRONTEND_HTML)


FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SENTINEL — live threat feed</title>
<script src="https://unpkg.com/maplibre-gl@4.6.0/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@4.6.0/dist/maplibre-gl.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#0a0c10;font-family:system-ui,sans-serif}
#map{position:absolute;top:0;left:0;width:100%;height:100%;z-index:1}
.marker{width:12px;height:12px;border-radius:50%;border:2px solid rgba(0,0,0,.6);cursor:pointer}
.marker.fire{background:#ff4444;box-shadow:0 0 10px #ff4444}
.marker.quake{background:#ff8c00;box-shadow:0 0 10px #ff8c00}
.marker.air-ok{background:#44cc66;box-shadow:0 0 8px #44cc66}
.marker.air-warn{background:#ffaa00;box-shadow:0 0 8px #ffaa00}
.marker.air-bad{background:#cc0000;box-shadow:0 0 8px #cc0000}
#hud{position:fixed;top:14px;left:14px;z-index:10;background:rgba(10,12,16,.9);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:14px 18px;font-size:.8rem;color:#ccc;backdrop-filter:blur(12px)}
#hud h2{font-size:.9rem;color:#e8702a;margin-bottom:2px}#hud .s{font-size:.65rem;color:rgba(255,255,255,.35)}
#counts{display:flex;gap:14px;margin-top:10px;font-family:monospace;font-size:.7rem}
#counts span{color:#ff8c00}#counts span:nth-child(2){color:#ff4444}#counts span:nth-child(3){color:#44cc66}
#status{position:fixed;bottom:14px;left:14px;z-index:10;font-family:monospace;font-size:.62rem;color:rgba(255,255,255,.25)}
</style>
</head>
<body>
<div id="map"></div>
<div id="hud"><h2>SENTINEL</h2><div class="s">Live threat feed · open source</div><div id="counts"><span>—</span><span>—</span><span>—</span></div></div>
<div id="status">connecting to backend...</div>
<script>
var m=new maplibregl.Map({container:'map',style:'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',center:[0,20],zoom:2.2,attributionControl:false});
m.addControl(new maplibregl.NavigationControl(),'bottom-right');

var markers=[];
async function refresh(){
  try{
    var r=await fetch('/api/events');var d=await r.json();
    document.getElementById('counts').innerHTML='<span>'+d.events.filter(function(e){return e.type==='earthquake'}).length+' quakes</span><span>'+d.events.filter(function(e){return e.type==='wildfire'}).length+' fires</span><span>'+d.events.filter(function(e){return e.type==='air_quality'}).length+' stations</span>';
    document.getElementById('status').textContent='updated '+d.updated+' · SENTINEL v1.0';
    markers.forEach(function(mk){mk.remove()});markers=[];
    d.events.forEach(function(e){
      var el=document.createElement('div');
      var cls=e.type==='earthquake'?'quake':e.type==='wildfire'?'fire':'air';
      if(e.type==='air_quality'){cls=e.pm25>75?'air-bad':e.pm25>35?'air-warn':'air-ok'}
      el.className='marker '+cls;
      el.title=e.type+': '+(e.magnitude||e.brightness||e.pm25||'');
      var mk=new maplibregl.Marker({element:el}).setLngLat(e.coordinates).addTo(m);
      el.onclick=function(){
        var t=e.type==='earthquake'?'M'+e.magnitude+' — '+e.place:e.type==='wildfire'?'Fire hotspot '+e.brightness+'K':'PM2.5: '+e.pm25+' µg/m³ — '+e.location;
        alert(t);
      };
      markers.push(mk);
    });
  }catch(err){document.getElementById('status').textContent='backend unreachable — retrying...'}
}
refresh();setInterval(refresh,60000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sentinel:app", host="0.0.0.0", port=8000, reload=True)
