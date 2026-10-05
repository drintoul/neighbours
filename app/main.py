"""Neighbourhood Similarity Finder — FastAPI backend.

Flow:
  1. Geocode the user's address (Nominatim).
  2. Build a "profile" of the source neighbourhood: counts of POI categories
     (restaurants, parks, waterfront, transit, ...) within a local radius.
  3. Find named neighbourhoods/suburbs/towns within the user's search radius.
  4. Profile a geographically-spread sample of candidates concurrently.
  5. Rank candidates by cosine similarity of log-scaled profiles.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import osm, routing
from .profile import CATEGORIES, CATEGORY_LABELS, build_profile
from .similarity import haversine_km, similarity_score

# Radius used to characterise a single neighbourhood.
PROFILE_RADIUS_M = int(os.environ.get("PROFILE_RADIUS_M", "1500"))
# Max candidates to profile — public Overpass quotas make bigger fan-outs slow.
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "25"))
# Concurrent Overpass profile queries. Only affects throughput against the
# self-hosted instance — public mirrors stay serialized by per-host throttle.
CONCURRENCY = int(os.environ.get("OVERPASS_CONCURRENCY", "8"))
TOP_MATCHES = int(os.environ.get("TOP_MATCHES", "10"))
# Base wait before retrying a failed candidate (jitter is added on top).
RETRY_DELAY_S = float(os.environ.get("RETRY_DELAY_S", "10"))
# Hard budget for the whole candidate-profiling stage — return partial
# results rather than letting the client time out.
PROFILE_BUDGET_S = float(os.environ.get("PROFILE_BUDGET_S", "240"))
# Budget for the mandatory geocode+discovery stage before erroring out.
MANDATORY_BUDGET_S = float(os.environ.get("MANDATORY_BUDGET_S", "150"))
# Keepalive interval — Cloudflare drops proxied streams after ~100s of
# silence, so we emit a ping whenever no real event has gone out for a while.
HEARTBEAT_S = 15
# Directory for persisted caches (bind-mounted volume in Docker).
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))

STATIC_DIR = Path(__file__).parent / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
_geocode_cache = osm.TTLCache(
    ttl_seconds=24 * 3600, persist_path=str(DATA_DIR / "geocode.pkl")
)
_places_cache = osm.TTLCache(
    ttl_seconds=6 * 3600, persist_path=str(DATA_DIR / "places.pkl")
)
_profile_cache = osm.TTLCache(
    ttl_seconds=6 * 3600, persist_path=str(DATA_DIR / "profiles.pkl")
)
_reverse_cache = osm.TTLCache(
    ttl_seconds=24 * 3600, persist_path=str(DATA_DIR / "reverse.pkl")
)
_CACHES = [_geocode_cache, _places_cache, _profile_cache, _reverse_cache]

CACHE_SAVE_INTERVAL_S = 120


async def _periodic_cache_save():
    while True:
        await asyncio.sleep(CACHE_SAVE_INTERVAL_S)
        for cache in _CACHES:
            cache.save()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    # Drop cached all-zero profiles: earlier Overpass versions of this bug
    # cached server-side-timeout responses (HTTP 200 + empty elements) as
    # real profiles. Genuine zeros simply get re-fetched on next search.
    # Also drop profiles built before a category was added (missing keys).
    _profile_cache.drop_where(
        lambda p: not any(p.values()) or len(p) != len(CATEGORIES)
    )
    _profile_cache.save()
    try:
        await asyncio.wait_for(osm.verify_endpoints(app.state.http), timeout=45)
    except asyncio.TimeoutError:
        pass
    save_task = asyncio.create_task(_periodic_cache_save())
    yield
    save_task.cancel()
    for cache in _CACHES:
        cache.save()
    await app.state.http.aclose()


app = FastAPI(title="Neighbourhood Similarity Finder", lifespan=lifespan)


@app.middleware("http")
async def static_revalidate(request, call_next):
    """Static assets: force browsers to revalidate (304 when unchanged) so
    updated JS/CSS is picked up immediately after a rebuild."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


class SearchRequest(BaseModel):
    address: str = Field(min_length=2, max_length=300)
    radius_km: float = Field(default=25, ge=1, le=100)
    # Optional per-category preference weights (0–4; 1 = neutral).
    # >1 prefers candidates with more of that category, <1 prefers less.
    weights: dict[str, float] | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Cache the self-hosted data timestamp for an hour — it only changes when
# the local Overpass data is rebuilt.
_data_as_of: tuple[float, str | None] = (0.0, None)


@app.get("/api/categories")
async def categories():
    global _data_as_of
    if time.monotonic() - _data_as_of[0] > 3600:
        _data_as_of = (
            time.monotonic(),
            await osm.regional_data_timestamp(app.state.http),
        )
    return {"categories": CATEGORY_LABELS, "data_as_of": _data_as_of[1]}


@app.get("/api/reverse")
async def reverse(lat: float, lon: float):
    """Reverse-geocode a map click into an address for the search bar."""
    client: httpx.AsyncClient = app.state.http
    key = (round(lat, 5), round(lon, 5))
    cached = _reverse_cache.get(key)
    if cached is not None:
        return cached
    try:
        result = await osm.reverse_geocode(lat, lon, client)
    except osm.GeocodeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _reverse_cache.set(key, result)
    return result


def _pick_candidates(places: list[dict], lat: float, lon: float, max_n: int) -> list[dict]:
    """Dedupe and pick a distance-stratified, spatially spread sample."""
    seen_names: set[str] = set()
    unique: list[dict] = []
    for p in places:
        key = p["name"].strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        p["distance_km"] = round(haversine_km(lat, lon, p["lat"], p["lon"]), 2)
        unique.append(p)

    # Drop candidates essentially on top of the source point.
    unique = [p for p in unique if p["distance_km"] > 1.0]
    unique.sort(key=lambda p: p["distance_km"])

    # Suppress candidates within ~1.2km of an already-picked closer one so we
    # don't profile overlapping suburb/neighbourhood centroids.
    spread: list[dict] = []
    for p in unique:
        if all(
            haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) > 1.2
            for q in spread
        ):
            spread.append(p)

    # Stratified sampling across distance bands keeps near AND far candidates.
    if len(spread) <= max_n:
        return spread
    bands = 4
    per_band = max(1, max_n // bands)
    picked: list[dict] = []
    max_d = spread[-1]["distance_km"] or 1
    for b in range(bands):
        lo, hi = max_d * b / bands, max_d * (b + 1) / bands
        band = [p for p in spread if lo <= p["distance_km"] <= hi]
        picked.extend(band[:per_band])
    if len(picked) < max_n:
        chosen = {id(p) for p in picked}
        picked.extend(p for p in spread if id(p) not in chosen)
    # Final spatial dedupe, then cap.
    final: list[dict] = []
    for p in sorted(picked, key=lambda p: p["distance_km"])[: max_n * 2]:
        if all(haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) > 1.2 for q in final):
            final.append(p)
        if len(final) >= max_n:
            break
    return final


# Bump when the POI query or category definitions change — invalidates
# cached profiles built under the old query instead of serving stale ones.
PROFILE_CACHE_VERSION = "v2"


async def _profile_for(lat: float, lon: float, client: httpx.AsyncClient) -> dict:
    key = (PROFILE_CACHE_VERSION, round(lat, 3), round(lon, 3))
    cached = _profile_cache.get(key)
    if cached is not None:
        return cached
    elements = await osm.fetch_pois(lat, lon, PROFILE_RADIUS_M, client)
    profile = build_profile(elements)
    _profile_cache.set(key, profile)
    return profile


# Per-IP rate limit for the expensive endpoint — a search can fan out to ~25
# Overpass queries, and the site is publicly reachable via the tunnel.
SEARCH_RATE_LIMIT = int(os.environ.get("SEARCH_RATE_LIMIT", "1"))
SEARCH_RATE_WINDOW_S = int(os.environ.get("SEARCH_RATE_WINDOW_S", "60"))
_search_times: dict[str, deque] = {}

# Route previews are cheap (one OSRM call each) but keep a modest cap so a
# page can't hammer the public demo router via our proxy.
ROUTE_RATE_LIMIT = int(os.environ.get("ROUTE_RATE_LIMIT") or "20")
ROUTE_RATE_WINDOW_S = int(os.environ.get("ROUTE_RATE_WINDOW_S") or "60")
_route_times: dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    # Behind the Cloudflare tunnel, the real client IP arrives in
    # CF-Connecting-IP; request.client.host would be the tunnel container.
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or request.client.host
    )


@app.post("/api/search")
async def search(req: SearchRequest, request: Request):
    """Streams newline-delimited JSON progress events so the client can show
    real pipeline status, ending with a 'result' or 'error' object."""
    ip = _client_ip(request)
    now = time.time()
    times = _search_times.setdefault(ip, deque())
    while times and now - times[0] > SEARCH_RATE_WINDOW_S:
        times.popleft()
    if len(times) >= SEARCH_RATE_LIMIT:
        retry_in = int(SEARCH_RATE_WINDOW_S - (now - times[0])) + 1
        return JSONResponse(
            {
                "detail": f"Rate limit — at most {SEARCH_RATE_LIMIT} "
                f"search{'es' if SEARCH_RATE_LIMIT != 1 else ''} per "
                f"{SEARCH_RATE_WINDOW_S}s. Try again in ~{retry_in}s."
            },
            status_code=429,
            headers={"Retry-After": str(retry_in)},
        )
    times.append(now)
    # Reap IPs that have no recent activity so the map doesn't grow forever.
    if len(_search_times) > 500:
        for k in [k for k, v in _search_times.items() if not v]:
            _search_times.pop(k, None)
    return StreamingResponse(
        _run_search(req), media_type="application/x-ndjson"
    )


async def _run_search(req: SearchRequest):
    def emit(kind, **kw):
        return (json.dumps({"type": kind, **kw}) + "\n").encode()

    client: httpx.AsyncClient = app.state.http
    address = req.address.strip()
    radius_m = int(req.radius_km * 1000)

    # Shared event queue: candidate tasks report progress; the Overpass
    # client reports global all-mirrors-failed waits (as a countdown).
    events_q: asyncio.Queue = asyncio.Queue()
    notify_token = osm.set_wait_notifier(
        lambda secs: events_q.put_nowait({"kind": "wait", "seconds": secs})
    )

    try:
        # 1. Geocode
        yield emit("progress", message=f"Geocoding “{address}”…")
        geo_key = address.lower()
        geo = _geocode_cache.get(geo_key)
        if geo is None:
            geo = await osm.geocode(address, client)
            _geocode_cache.set(geo_key, geo)
        lat, lon = geo["lat"], geo["lon"]

        # 2+3. Source profile and candidate discovery run in parallel.
        yield emit(
            "progress",
            message=f"Found {geo['name']} — profiling it and finding named "
            f"neighbourhoods within {req.radius_km:g} km…",
        )
        places_key = (round(lat, 3), round(lon, 3), radius_m)
        places = _places_cache.get(places_key)

        async def _discover():
            if places is None:
                prof, pl = await asyncio.wait_for(
                    asyncio.gather(
                        _profile_for(lat, lon, client),
                        osm.find_places(lat, lon, radius_m, client),
                    ),
                    timeout=MANDATORY_BUDGET_S,
                )
                _places_cache.set(places_key, pl)
                return prof, pl
            prof = await asyncio.wait_for(
                _profile_for(lat, lon, client), timeout=MANDATORY_BUDGET_S
            )
            return prof, places

        task = asyncio.ensure_future(_discover())
        try:
            last_emit = time.monotonic()
            while not task.done():
                try:
                    ev = await asyncio.wait_for(events_q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if time.monotonic() - last_emit >= HEARTBEAT_S:
                        last_emit = time.monotonic()
                        yield emit("ping")
                    continue
                last_emit = time.monotonic()
                yield emit(
                    "wait",
                    seconds=ev["seconds"],
                    message="All map data servers are busy — "
                    f"retrying in {ev['seconds']:g}s…",
                )
            source_profile, places = task.result()
        except asyncio.TimeoutError:
            yield emit(
                "error",
                detail="Map data service is too slow right now — the initial "
                "queries timed out. Try again shortly.",
            )
            return

        if not any(source_profile.values()):
            yield emit(
                "error",
                detail=f"No mapped amenities found within "
                f"{PROFILE_RADIUS_M} m of {geo['name']} — this spot may be "
                "rural or sparsely mapped in OpenStreetMap. Try a nearby "
                "town or a different point on the map.",
            )
            return

        candidates = _pick_candidates(places, lat, lon, MAX_CANDIDATES)
        total = len(candidates)
        yield emit(
            "progress",
            message=f"Found {len(places)} named places — profiling {total} "
            "candidates…",
        )

        # 4. Profile candidates concurrently (semaphore to stay polite with
        # Overpass); each completion emits real progress.
        sem = asyncio.Semaphore(CONCURRENCY)
        done = 0

        async def profile_candidate(p: dict) -> dict | None:
            # Two passes; the semaphore is released between attempts so a
            # rate-limited retry doesn't starve other candidates.
            for attempt in range(2):
                async with sem:
                    try:
                        profile = await _profile_for(p["lat"], p["lon"], client)
                        return {**p, "profile": profile}
                    except osm.OverpassError:
                        pass
                if attempt == 0:
                    # Jitter spreads retries instead of re-bursting in lockstep.
                    await asyncio.sleep(RETRY_DELAY_S + random.uniform(0, 4))
            return None

        async def counted(p: dict) -> dict | None:
            nonlocal done
            try:
                return await profile_candidate(p)
            finally:
                done += 1
                events_q.put_nowait(
                    {"kind": "progress", "done": done, "name": p["name"]}
                )

        tasks = [asyncio.ensure_future(counted(p)) for p in candidates]
        deadline = time.monotonic() + PROFILE_BUDGET_S
        last_emit = time.monotonic()
        timed_out = False
        while not all(t.done() for t in tasks):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                for t in tasks:
                    t.cancel()
                break
            try:
                ev = await asyncio.wait_for(
                    events_q.get(), timeout=min(0.5, remaining)
                )
            except asyncio.TimeoutError:
                if time.monotonic() - last_emit >= HEARTBEAT_S:
                    last_emit = time.monotonic()
                    yield emit("ping")
                continue
            last_emit = time.monotonic()
            if ev.get("kind") == "wait":
                yield emit(
                    "wait",
                    seconds=ev["seconds"],
                    message="All map data servers are busy — "
                    f"retrying in {ev['seconds']:g}s…",
                )
            else:
                yield emit(
                    "progress",
                    done=ev["done"],
                    total=total,
                    message=f"Profiling neighbourhoods… {ev['done']}/{total} "
                    f"done ({ev['name']})",
                )
        results = [
            t.result()
            if t.done() and not t.cancelled() and t.exception() is None
            else None
            for t in tasks
        ]
        evaluated = [r for r in results if r is not None]
        if timed_out:
            yield emit(
                "progress",
                message=f"Time limit reached — scoring {len(evaluated)} "
                f"of {total} neighbourhoods…",
            )
        if not evaluated:
            yield emit(
                "error",
                detail="Map data service unavailable — all candidate queries "
                "failed. Try again shortly.",
            )
            return

        # 5. Rank by similarity (optionally weighted).
        yield emit(
            "progress",
            message=f"Scoring {len(evaluated)} neighbourhoods by similarity…",
        )
        weights = None
        if req.weights:
            weights = {
                cat: min(max(float(v), 0.0), 4.0)
                for cat, v in req.weights.items()
                if cat in CATEGORIES and isinstance(v, (int, float))
            } or None
        for r in evaluated:
            r["score"] = round(
                similarity_score(source_profile, r["profile"], weights) * 100
            )
        evaluated.sort(key=lambda r: r["score"], reverse=True)
        matches = evaluated[:TOP_MATCHES]

        # Attach drive time/distance to every evaluated candidate — one OSRM
        # table request. Best-effort: a router outage never fails a search.
        try:
            yield emit("progress", message="Calculating drive times…")
            drives = await routing.drive_table(
                (lat, lon),
                [(r["lat"], r["lon"]) for r in evaluated],
                client,
            )
            for r, d in zip(evaluated, drives):
                if d:
                    r.update(d)
        except routing.RoutingError:
            pass

        yield emit(
            "result",
            data={
                "source": {
                    "name": geo["name"],
                    "display_name": geo["display_name"],
                    "lat": lat,
                    "lon": lon,
                    "profile": source_profile,
                },
                "profile_radius_m": PROFILE_RADIUS_M,
                "search_radius_km": req.radius_km,
                "candidates_found": len(places),
                "candidates_evaluated": len(evaluated),
                "partial": timed_out,
                "matches": matches,
                # All scored candidates — lets the client re-rank locally when
                # the user adjusts weight sliders without another API call.
                "evaluated": evaluated,
            },
        )
    except osm.GeocodeError as exc:
        yield emit("error", detail=str(exc))
    except osm.OverpassError as exc:
        yield emit(
            "error",
            detail=f"Map data service unavailable: {exc}. Try again shortly.",
        )
    except Exception as exc:  # keep the stream well-formed on unexpected errors
        yield emit("error", detail=f"Search failed: {exc}")
    finally:
        osm.reset_wait_notifier(notify_token)


@app.get("/api/route")
async def route(from_lat: float, from_lon: float, to_lat: float, to_lon: float, request: Request):
    """Driving route geometry between two points (for map previews)."""
    ip = _client_ip(request)
    now = time.time()
    times = _route_times.setdefault(ip, deque())
    while times and now - times[0] > ROUTE_RATE_WINDOW_S:
        times.popleft()
    if len(times) >= ROUTE_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Route rate limit")
    times.append(now)
    try:
        return await routing.route_geometry(
            (from_lat, from_lon), (to_lat, to_lon), app.state.http
        )
    except routing.RoutingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse(STATIC_DIR / "robots.txt")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
