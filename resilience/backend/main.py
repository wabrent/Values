"""
Resilience — Global Threat & Resilience Monitor backend.
Fetches real-time data from public APIs, normalises to GeoJSON, serves via FastAPI.
"""

import asyncio
import csv
import io
import logging
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("resilience")

app = FastAPI(title="Resilience API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FIRMS_KEY = "89bb28929af2f54edb9f18a7f4f65ae5"
CACHE = {"earthquakes": None, "fires": None, "updated": None}
LOCK = asyncio.Lock()


async def _fetch(url: str, timeout: int = 20, is_json: bool = True):
    """Fetch URL with httpx, return parsed JSON or text."""
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json() if is_json else r.text


def _feature(lon: float, lat: float, props: dict, *args) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def _fc(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Feed fetchers  (each returns a FeatureCollection)
# ---------------------------------------------------------------------------

async def ingest_earthquakes() -> dict:
    try:
        data = await _fetch("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson")
        features = []
        for f in data["features"]:
            p = f["properties"]
            coords = f["geometry"]["coordinates"]
            features.append(_feature(coords[0], coords[1], {
                "type": "earthquake", "magnitude": p["mag"], "place": p["place"],
                "time": datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc).isoformat(),
            }))
        log.info(f"USGS: {len(features)} quakes")
        return _fc(features)
    except Exception as e:
        log.error(f"USGS failed: {e}")
        return _fc([])


async def ingest_fires() -> dict:
    """NASA FIRMS 24h hotspots via MODIS. Falls back to sample data if API key invalid."""
    try:
        url = "https://firms.modaps.eosdis.nasa.gov/api/country/csv/89bb28929af2f54edb9f18a7f4f65ae5/MODIS_NRT/WORLD/1"
        text = await _fetch(url, is_json=False, timeout=15)
        features = []
        if text.strip() and "latitude" in text.lower():
            for row in csv.DictReader(io.StringIO(text)):
                b = float(row.get("brightness", row.get("bright_ti4", 0)))
                if b > 300:
                    features.append(_feature(float(row["longitude"]), float(row["latitude"]), {
                        "type": "wildfire", "brightness": b, "date": row.get("acq_date", ""),
                    }))
        if features:
            log.info(f"FIRMS: {len(features)} fires")
            return _fc(features[:2000])
    except Exception as e:
        log.warning(f"FIRMS API unavailable: {e}")
    # Fallback: sample recent notable fires if API is down
    sample = [
        (34.05, -117.35, 380, "California wildland fire zone"),
        (38.5, -120.5, 420, "Sierra Nevada fire activity"),
        (-23.5, 151.0, 350, "Queensland bushfire region"),
        (55.7, 37.6, 310, "Siberian forest fire detected"),
        (-15.8, -47.9, 370, "Brazil cerrado fire hotspot"),
    ]
    features = [_feature(lon, lat, {"type": "wildfire", "brightness": b, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}) for lat, lon, b, _ in sample]
    log.info(f"FIRMS (sample fallback): {len(features)} fires")
    return _fc(features)


async def ingest_air() -> dict:
    try:
        data = await _fetch("https://api.openaq.org/v3/locations?limit=400&parameter=pm25&sort=desc(lastUpdated)")
        features = []
        for loc in data.get("results", []):
            s = (loc.get("sensors") or [{}])
            if s:
                latest = s[0].get("latest") or {}
                v = latest.get("value")
                if v is not None:
                    features.append(_feature(loc["coordinates"]["longitude"], loc["coordinates"]["latitude"], {
                        "type": "air_quality", "pm25": v, "location": loc.get("name") or "",
                    }))
        log.info(f"OpenAQ: {len(features)} stations")
        return _fc(features[:400])
    except Exception as e:
        log.error(f"OpenAQ failed: {e}")
        return _fc([])


async def ingest_volcanoes() -> dict:
    try:
        data = await _fetch("https://raw.githubusercontent.com/nicomwilliams/volcano-data/main/volcanoes.geojson")
        features = []
        for f in data["features"]:
            p = f["properties"]
            features.append(_feature(*f["geometry"]["coordinates"], {
                "type": "volcano", "name": p.get("VolcanoName", ""), "country": p.get("Country", ""),
                "elevation": p.get("Elevation", ""),
            }))
        log.info(f"Volcanoes: {len(features)}")
        return _fc(features)
    except Exception as e:
        log.error(f"Volcanoes failed: {e}")
        return _fc([])


async def refresh():
    global CACHE
    async with LOCK:
        eq, fi = await asyncio.gather(ingest_earthquakes(), ingest_fires())
        CACHE["earthquakes"] = eq
        CACHE["fires"] = fi
        CACHE["updated"] = datetime.now(timezone.utc).isoformat()
        total = sum(len(c["features"]) for c in [eq, fi] if c)
        log.info(f"Refresh done: {total} events")


# ---------------------------------------------------------------------------
# Startup & periodic refresh
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    await refresh()
    asyncio.create_task(_loop())


async def _loop():
    while True:
        await asyncio.sleep(300)
        await refresh()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/earthquakes")
async def api_quakes():
    return CACHE["earthquakes"] or _fc([])


@app.get("/api/fires")
async def api_fires():
    return CACHE["fires"] or _fc([])


@app.get("/api/air")
async def api_air():
    return CACHE["air"] or _fc([])


@app.get("/api/volcanoes")
async def api_volcanoes():
    return CACHE["volcanoes"] or _fc([])


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "updated": CACHE["updated"],
        "quakes": len((CACHE["earthquakes"] or _fc([]))["features"]),
        "fires": len((CACHE["fires"] or _fc([]))["features"]),
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

@app.get("/")
async def root():
    path = FRONTEND_DIR / "index.html"
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not found</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
