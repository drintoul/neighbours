"""Nearby Eats service — food options within a drive-time budget.

Reuses the neighbours app's OSM plumbing: Nominatim geocoding,
coverage-gated Overpass endpoints (self-hosted BC/WA/FL, public fallback),
and OSRM drive-time tables.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import osm, routing
from app.similarity import haversine_km

STATIC_DIR = Path(__file__).parent / "static"

# How far a 10-minute drive can reach on fast roads — the straight-line
# fetch radius; the OSRM table then decides what actually makes the cut.
FETCH_RADIUS_M = int(os.environ.get("FETCH_RADIUS_M", "12000"))
DRIVE_BUDGET_S = int(os.environ.get("DRIVE_BUDGET_S", "600"))
# OSRM's default max table size is 100 locations (source + 99 targets).
MAX_TARGETS = 99

SEARCH_RATE_LIMIT = int(os.environ.get("RESTAURANT_RATE_LIMIT", "4"))
SEARCH_RATE_WINDOW_S = int(os.environ.get("RESTAURANT_RATE_WINDOW_S", "60"))
_search_times: dict[str, deque] = {}

_geocode_cache = osm.TTLCache(ttl_seconds=6 * 3600)
_geo_cache = osm.TTLCache(ttl_seconds=6 * 3600)

# Optional Google Places enrichment — ratings, review counts, price tier,
# place-page link. Needs a billing-enabled GCP key; without one the app
# runs fine, listings just lack the Google fields.
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_RADIUS_M = int(os.environ.get("GOOGLE_RADIUS_M", "4000"))
GOOGLE_PAGES = 3  # Text Search: 20 results/page, 60 max
_FIELD_MASK = ",".join(
    [
        "places.displayName",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.googleMapsUri",
        "places.formattedAddress",
        "places.currentOpeningHours.openNow",
        "places.websiteUri",
        "nextPageToken",
    ]
)
_PRICE = {
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}
_google_cache = osm.TTLCache(ttl_seconds=6 * 3600)
_google_limited_until = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    try:
        await asyncio.wait_for(
            osm.verify_endpoints(app.state.http), timeout=45
        )
    except Exception:
        pass
    yield
    await app.state.http.aclose()


app = FastAPI(title="Nearby Eats", lifespan=lifespan)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """no-cache on HTML/JS/CSS so browsers always revalidate — deploys were
    invisible behind heuristic caching."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=300)
    # Optional GPS coords — skips geocoding entirely
    lat: float | None = None
    lon: float | None = None


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or request.client.host
    )


def _rate_limited(request: Request) -> JSONResponse | None:
    ip = _client_ip(request)
    now = time.time()
    times = _search_times.setdefault(ip, deque())
    while times and now - times[0] > SEARCH_RATE_WINDOW_S:
        times.popleft()
    if len(times) >= SEARCH_RATE_LIMIT:
        retry_in = int(SEARCH_RATE_WINDOW_S - (now - times[0])) + 1
        return JSONResponse(
            {"detail": f"Rate limit — try again in ~{retry_in}s."},
            status_code=429,
            headers={"Retry-After": str(retry_in)},
        )
    times.append(now)
    return None


_url_alive_cache = osm.TTLCache(ttl_seconds=6 * 3600)

# Sites that serve a 200 "page not found" (soft 404) — phrase markers in body.
_SOFT404 = (
    "page not found",
    "couldn't find the page",
    "could not find the page",
    "could not be found",
    "404 error",
    "this page doesn't exist",
    "this page does not exist",
    "page cannot be found",
    "nothing here",
)


async def _url_alive(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str) -> bool:
    cached = _url_alive_cache.get(url)
    if cached is not None:
        return cached
    async with sem:
        ok = False
        try:
            # Streaming GET: most sites respond; body never downloaded.
            async with client.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NearbyEats)"},
            ) as resp:
                ok = resp.status_code < 400
                if ok:
                    # Soft-404 sniff: scan the start of the body for
                    # not-found boilerplate.
                    body = ""
                    async for chunk in resp.aiter_text():
                        body += chunk
                        if len(body) > 32768:
                            break
                    low = body.lower()
                    if any(m in low for m in _SOFT404):
                        ok = False
        except Exception:
            ok = False
        _url_alive_cache.set(url, ok)
        return ok


async def validate_websites(spots: list[dict], client: httpx.AsyncClient) -> None:
    """Drop website links that don't resolve — keeps listings, hides the link."""
    urls = {s["website"] for s in spots if s.get("website")}
    if not urls:
        return
    sem = asyncio.Semaphore(12)
    verdicts = dict(
        zip(urls, await asyncio.gather(*(_url_alive(client, sem, u) for u in urls)))
    )
    for s in spots:
        if s.get("website") and not verdicts[s["website"]]:
            s["website"] = ""


def _clean_url(raw: str) -> str:
    """OSM URL tags are messy: ';'-joined multiples, bare domains, mobile
    Facebook hosts, and occasionally non-business links. Normalize to a
    single absolute https URL or return ''."""
    u = (raw or "").split(";")[0].strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif not u.lower().startswith(("http://", "https://")):
        # Bare domain like "panago.com" — a relative href would resolve
        # against our own site, which is the "wrong URL" bug.
        u = "https://" + u if "." in u and " " not in u else ""
    # A Facebook page isn't the restaurant's website — drop it.
    if re.search(r"//(www\.|m\.)?(facebook|fb)\.(com|me)", u.lower()):
        return ""
    return u


async def fetch_eateries(lat: float, lon: float, client: httpx.AsyncClient) -> list[dict]:
    """Named food-service POIs within FETCH_RADIUS_M of the point."""
    kinds = "restaurant|cafe|fast_food|bar|pub|food_court|ice_cream|biergarten"
    query = f"""
[out:json][timeout:60];
(
  node["amenity"~"{kinds}"](around:{FETCH_RADIUS_M},{lat},{lon});
  way["amenity"~"{kinds}"](around:{FETCH_RADIUS_M},{lat},{lon});
  relation["amenity"~"{kinds}"](around:{FETCH_RADIUS_M},{lat},{lon});
);
out center 1500;
"""
    data = await osm.overpass_query(query, client, point=(lat, lon))
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        center = el.get("center") or {}
        elat = el.get("lat", center.get("lat"))
        elon = el.get("lon", center.get("lon"))
        if elat is None or elon is None:
            continue
        out.append(
            {
                "name": name,
                "lat": float(elat),
                "lon": float(elon),
                "amenity": tags.get("amenity", ""),
                "cuisine": tags.get("cuisine", ""),
                "opening_hours": tags.get("opening_hours", ""),
                "phone": tags.get("phone") or tags.get("contact:phone") or "",
                "website": _clean_url(
                    tags.get("website")
                    or tags.get("contact:website")
                    or tags.get("url")
                    or ""
                ),
                "address": (
                    ", ".join(
                        x
                        for x in (
                            " ".join(
                                x
                                for x in (
                                    tags.get("addr:housenumber", ""),
                                    tags.get("addr:street", ""),
                                )
                                if x
                            ),
                            tags.get("addr:city", ""),
                            tags.get("addr:postcode", ""),
                        )
                        if x
                    )
                    # Street-without-number isn't a real address either
                    if tags.get("addr:housenumber")
                    else ""
                ),
            }
        )
    return out


def _norm(name: str) -> str:
    """Letters+digits only — 'Cora Breakfast & Lunch!' ~ 'cora' for matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def enrich_google(
    spots: list[dict], lat: float, lon: float, client: httpx.AsyncClient
) -> None:
    """Overlay Google Places rating/count/price/link + fill empty addresses.

    One cached Text Search per area (~1km key). Places are matched back to
    OSM spots by proximity + fuzzy name. No key / quota error / circuit open
    → silently skipped; listings just lack the extra fields.
    """
    global _google_limited_until
    if not GOOGLE_API_KEY or time.time() < _google_limited_until:
        return

    key = f"{lat:.2f},{lon:.2f}"
    places = _google_cache.get(key)
    if places is None:
        places = []
        headers = {
            "X-Goog-Api-Key": GOOGLE_API_KEY,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body = {
            "textQuery": "restaurants",
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": float(GOOGLE_RADIUS_M),
                }
            },
            "pageSize": 20,
        }
        for _ in range(GOOGLE_PAGES):
            try:
                resp = await client.post(
                    GOOGLE_URL, json=body, headers=headers, timeout=15
                )
                if resp.status_code == 429:
                    # Quota/rate exhausted — back off for an hour
                    _google_limited_until = time.time() + 3600
                    return
                if resp.status_code != 200:
                    return
                data = resp.json()
            except httpx.HTTPError:
                return
            places.extend(data.get("places", []))
            token = data.get("nextPageToken")
            if not token:
                break
            # nextPageToken isn't valid immediately
            await asyncio.sleep(2)
            body["pageToken"] = token
        # Only cache real results — quota errors mustn't suppress future
        # enrichment once the key/billing is fixed.
        if places:
            _google_cache.set(key, places)

    used: set[int] = set()
    for p in places:
        loc = p.get("location", {})
        plat, plon = loc.get("latitude"), loc.get("longitude")
        pname = _norm((p.get("displayName") or {}).get("text", ""))
        if plat is None or plon is None or not pname:
            continue
        # Match to the closest unclaimed OSM spot within ~150m whose name
        # is similar — same-name-different-branch or OSM-name-variation
        # both handled by the fuzzy test.
        best_i, best_d = -1, 0.15
        for i, s in enumerate(spots):
            if i in used:
                continue
            d = haversine_km(plat, plon, s["lat"], s["lon"])
            if d >= best_d:
                continue
            sname = _norm(s["name"])
            similar = (
                sname == pname
                or sname in pname
                or pname in sname
                or difflib.SequenceMatcher(None, sname, pname).ratio() >= 0.75
            )
            if similar:
                best_i, best_d = i, d
        if best_i < 0:
            continue
        used.add(best_i)
        s = spots[best_i]
        if p.get("rating") is not None:
            s["rating"] = p["rating"]
            s["review_count"] = p.get("userRatingCount", 0)
        if p.get("priceLevel") in _PRICE:
            s["price"] = _PRICE[p["priceLevel"]]
        if (p.get("currentOpeningHours") or {}).get("openNow") is not None:
            s["open_now"] = bool(p["currentOpeningHours"]["openNow"])
        if p.get("googleMapsUri"):
            s["google_maps_uri"] = p["googleMapsUri"]
        # Google's websiteUri is the location page for chains — prefer it
        # over OSM website tags, which usually point at the corporate site.
        if p.get("websiteUri"):
            s["website"] = _clean_url(p["websiteUri"]) or s.get("website", "")
        # Google has an address for places OSM doesn't tag — real numbered
        # street addresses, so no '≈' flag.
        if not s.get("address") and (p.get("formattedAddress") or "")[:1].isdigit():
            s["address"] = p["formattedAddress"]


# OSM often tags housenumber+street+postcode but omits addr:city — fill it
# from the geocoded area (or the spot's own reverse-geocode city) so
# listings don't show truncated addresses.
_POSTAL = re.compile(r"[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d|\d{5}(-\d{4})?$")


def _with_city(address: str, city: str) -> str:
    """Insert the city into "street[, postcode]" addresses missing it."""
    if not city or city.lower() in address.lower():
        return address
    parts = address.split(", ")
    if _POSTAL.fullmatch(parts[-1] or ""):
        parts.insert(-1, city)
    else:
        parts.append(city)
    return ", ".join(parts)


# Last-resort address source: Nominatim reverse geocode of the pin coords.
# Serial and throttled (~1.5s/call) but cached 24h — only the first search
# in an area pays the delay. Flagged approximate in the UI.
_rev_cache = osm.TTLCache(ttl_seconds=24 * 3600)


async def fill_missing_addresses(spots: list[dict], client: httpx.AsyncClient) -> None:
    missing = [s for s in spots if not s.get("address")][:30]
    for s in missing:
        if s.get("address"):
            continue
        key = f"{s['lat']:.4f},{s['lon']:.4f}"
        cached = _rev_cache.get(key)
        if cached is None:
            try:
                r = await osm.reverse_geocode(s["lat"], s["lon"], client)
                # Road-only results ("South Fraser Way") aren't real
                # addresses — require a house number.
                addr = r["short_name"] if r["short_name"][:1].isdigit() else ""
                cached = (addr, r.get("city") or "")
            except (osm.GeocodeError, httpx.HTTPError):
                cached = ("", "")
            _rev_cache.set(key, cached)
        if cached[0]:
            s["address"] = _with_city(cached[0], cached[1])
            s["address_approx"] = True


# Broad cuisine buckets — raw tags like "nepalese" or "poke" fold into a
# ~15-group set so sections stay scannable. The raw cuisine tag still shows
# in each row's meta line.
_GROUPS = {
    "pizza": "Pizza & Italian",
    "italian": "Pizza & Italian",
    "pasta": "Pizza & Italian",
    "calzone": "Pizza & Italian",
    "chinese": "Chinese",
    "szechuan": "Chinese",
    "cantonese": "Chinese",
    "dim_sum": "Chinese",
    "japanese": "Japanese & Sushi",
    "sushi": "Japanese & Sushi",
    "ramen": "Japanese & Sushi",
    "udon": "Japanese & Sushi",
    "thai": "Asian",
    "vietnamese": "Asian",
    "korean": "Asian",
    "asian": "Asian",
    "filipino": "Asian",
    "nepalese": "Asian",
    "malaysian": "Asian",
    "indonesian": "Asian",
    "noodle": "Asian",
    "noodles": "Asian",
    "pho": "Asian",
    "hawaiian": "Asian",
    "poke": "Asian",
    "indian": "Indian & Middle Eastern",
    "curry": "Indian & Middle Eastern",
    "kebab": "Indian & Middle Eastern",
    "persian": "Indian & Middle Eastern",
    "turkish": "Indian & Middle Eastern",
    "lebanese": "Indian & Middle Eastern",
    "middle_eastern": "Indian & Middle Eastern",
    "afghani": "Indian & Middle Eastern",
    "pakistani": "Indian & Middle Eastern",
    "falafel": "Indian & Middle Eastern",
    "burger": "Burgers & American",
    "american": "Burgers & American",
    "diner": "Burgers & American",
    "steakhouse": "Burgers & American",
    "barbecue": "Burgers & American",
    "bbq": "Burgers & American",
    "grill": "Burgers & American",
    "chicken": "Chicken & Wings",
    "wings": "Chicken & Wings",
    "fried_chicken": "Chicken & Wings",
    "mexican": "Mexican & Latin",
    "taco": "Mexican & Latin",
    "burrito": "Mexican & Latin",
    "tex-mex": "Mexican & Latin",
    "latin_american": "Mexican & Latin",
    "caribbean": "Mexican & Latin",
    "jamaican": "Mexican & Latin",
    "brazilian": "Mexican & Latin",
    "peruvian": "Mexican & Latin",
    "greek": "Mediterranean",
    "mediterranean": "Mediterranean",
    "pita": "Mediterranean",
    "spanish": "Mediterranean",
    "tapas": "Mediterranean",
    "portuguese": "Mediterranean",
    "sandwich": "Sandwiches & Deli",
    "deli": "Sandwiches & Deli",
    "bagel": "Sandwiches & Deli",
    "sub": "Sandwiches & Deli",
    "coffee_shop": "Cafés & Coffee",
    "coffee": "Cafés & Coffee",
    "bubble_tea": "Cafés & Coffee",
    "tea": "Cafés & Coffee",
    "juice": "Cafés & Coffee",
    "breakfast": "Breakfast & Brunch",
    "brunch": "Breakfast & Brunch",
    "crepe": "Breakfast & Brunch",
    "waffle": "Breakfast & Brunch",
    "pancake": "Breakfast & Brunch",
    "dessert": "Dessert & Bakery",
    "ice_cream": "Dessert & Bakery",
    "cake": "Dessert & Bakery",
    "donut": "Dessert & Bakery",
    "pastry": "Dessert & Bakery",
    "bakery": "Dessert & Bakery",
    "chocolate": "Dessert & Bakery",
    "french": "European",
    "german": "European",
    "ukrainian": "European",
    "polish": "European",
    "russian": "European",
    "austrian": "European",
    "fries": "Fast Food",
    "hot_dog": "Fast Food",
    "fish": "Seafood",
    "seafood": "Seafood",
    "fish_and_chips": "Seafood",
    "salad": "Healthy & Salad",
    "vegetarian": "Healthy & Salad",
    "vegan": "Healthy & Salad",
    "health_food": "Healthy & Salad",
    "organic": "Healthy & Salad",
}

_AMENITY_GROUP = {
    "cafe": "Cafés & Coffee",
    "fast_food": "Fast Food",
    "food_court": "Fast Food",
    "bar": "Pubs & Bars",
    "pub": "Pubs & Bars",
    "biergarten": "Pubs & Bars",
    "ice_cream": "Dessert & Bakery",
}


def cuisine_group(item: dict) -> str:
    """Broad bucket for the cuisine tag; falls back to the amenity type."""
    raw = (
        (item.get("cuisine") or "")
        .split(";")[0]
        .split(",")[0]
        .strip()
        .lower()
        .replace(" ", "_")
    )
    if raw:
        return _GROUPS.get(raw, "Other")
    return _AMENITY_GROUP.get(item.get("amenity"), "Other")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _ev(t: str, **kw) -> bytes:
    return (json.dumps({"t": t, **kw}) + "\n").encode()


@app.post("/api/search")
async def search(req: SearchRequest, request: Request):
    limited = _rate_limited(request)
    if limited:
        return limited

    async def stream():
        client = request.app.state.http

        # GPS coords arrive either as payload fields or as a "lat, lon"
        # typed/pasted query (the 📍 button fills the box this way).
        coords: tuple[float, float] | None = None
        if req.lat is not None and req.lon is not None:
            coords = (req.lat, req.lon)
        else:
            m = re.fullmatch(
                r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", req.query
            )
            if m:
                coords = (float(m.group(1)), float(m.group(2)))

        if coords:
            # GPS search — reverse-geocode just for the hotel card display
            yield _ev("progress", msg="Using your current location…")
            try:
                r = await osm.reverse_geocode(coords[0], coords[1], client)
                geo = {
                    "lat": coords[0],
                    "lon": coords[1],
                    "display_name": r["display_name"],
                    "name": r["short_name"],
                    "city": r.get("city") or "",
                }
            except (osm.GeocodeError, httpx.HTTPError):
                geo = {
                    "lat": coords[0],
                    "lon": coords[1],
                    "display_name": (
                        f"Current location ({coords[0]:.5f}, {coords[1]:.5f})"
                    ),
                    "name": "Your location",
                    "city": "",
                }
            lat, lon = geo["lat"], geo["lon"]
        elif len(req.query.strip()) >= 2:
            # Nominatim is fussy about punctuation/structure — try a few
            # cleaned-up variants of the same query before declaring failure.
            yield _ev("progress", msg="Looking up that address…")
            raw = req.query.strip()
            candidates = [raw]
            cleaned = re.sub(
                r"\s*,\s*", ", ", re.sub(r"[/\\|;]+", ",", raw)
            ).strip(", ")
            if cleaned != raw:
                candidates.append(cleaned)
            if "," in raw:
                candidates.append(raw.replace(",", " ").replace("/", " "))
            # "Name, City1, City2" failures: try "Name, CityX" per segment —
            # combined-city queries often match nothing while one city works.
            parts = [p.strip() for p in cleaned.split(",") if p.strip()]
            if len(parts) > 2:
                candidates += [f"{parts[0]}, {p}" for p in parts[1:4]]
            # "Name, Multi Word Place": Nominatim wants all words to match —
            # trim the place tail word-by-word ("biloxi ocean" → "biloxi").
            if len(parts) == 2 and len(parts[1].split()) > 1:
                words = parts[1].split()
                candidates += [
                    f"{parts[0]}, {' '.join(words[:i])}"
                    for i in range(len(words) - 1, 0, -1)
                ]
            geo = None
            for q in candidates:
                geo = _geo_cache.get(q.lower())
                if geo is not None:
                    break
                try:
                    geo = await osm.geocode(q, client)
                except osm.GeocodeError:
                    continue
                _geo_cache.set(q.lower(), geo)
                break
            if geo is None:
                yield _ev(
                    "error",
                    detail=(
                        f"Could not find: {raw}. Try a street address, "
                        '"Name, City", or a landmark.'
                    ),
                )
                return
            lat, lon = geo["lat"], geo["lon"]
        else:
            yield _ev(
                "error",
                detail="Enter an address or tap the location button.",
            )
            return

        yield _ev(
            "progress",
            msg=f"Found {geo['name']} — finding nearby restaurants…",
        )
        try:
            spots = await fetch_eateries(lat, lon, client)
        except osm.OverpassError as exc:
            yield _ev("error", detail=f"Map data unavailable: {exc}")
            return

        for s in spots:
            s["dist_km"] = round(haversine_km(lat, lon, s["lat"], s["lon"]), 2)
        # Closest candidates first — the route table caps at 99 targets.
        spots.sort(key=lambda s: s["dist_km"])
        spots = spots[:MAX_TARGETS]

        yield _ev(
            "progress",
            msg=f"{len(spots)} spots found — calculating drive times…",
        )
        routed = False
        try:
            drives = await routing.drive_table(
                (lat, lon), [(s["lat"], s["lon"]) for s in spots], client
            )
            for s, d in zip(spots, drives):
                if d:
                    s.update(d)
            routed = True
            spots = [s for s in spots if s.get("drive_s", 1e9) <= DRIVE_BUDGET_S]
        except routing.RoutingError:
            # No router covers this area — fall back to a straight-line cut
            # so results are still useful (flagged as crow-fly distances).
            spots = [s for s in spots if s["dist_km"] <= 5.0]

        yield _ev(
            "progress",
            msg="Checking ratings, hours & website links…",
        )
        await enrich_google(spots, lat, lon, client)
        await validate_websites(spots, client)
        yield _ev("progress", msg="Verifying street addresses…")
        await fill_missing_addresses(spots, client)
        # Hide listings with no usable address from any source
        spots = [s for s in spots if s.get("address")]
        # OSM rarely tags addr:city — append the searched area's city so
        # addresses read "street, city, postcode". Reverse-geocoded spots
        # already carry their own (more accurate) city.
        city = geo.get("city") or ""
        for s in spots:
            if not s.get("address_approx"):
                s["address"] = _with_city(s["address"], city)

        groups: dict[str, list[dict]] = {}
        for s in spots:
            s["group"] = cuisine_group(s)
            groups.setdefault(s["group"], []).append(s)
        for items in groups.values():
            items.sort(key=lambda s: s.get("drive_s", s["dist_km"] * 60))

        yield _ev(
            "done",
            data={
                "hotel": geo,
                "routed": routed,
                "budget_min": DRIVE_BUDGET_S // 60,
                # Most listings first, alphabetical tiebreak, "Other" last
                "groups": dict(
                    sorted(
                        groups.items(),
                        key=lambda kv: (kv[0] == "Other", -len(kv[1]), kv[0]),
                    )
                ),
                "count": len(spots),
            },
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/robots.txt")
async def robots():
    return FileResponse(STATIC_DIR / "robots.txt")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
