"""Clients for free OpenStreetMap services: Nominatim (geocoding) and Overpass (POI data)."""

from __future__ import annotations

import asyncio
import os
import pickle
import time
from contextvars import ContextVar

import httpx

NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org")

OVERPASS_URLS = [
    u.strip()
    for u in os.environ.get(
        "OVERPASS_URLS",
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.osm.ch/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter,"
        "https://overpass.openstreetmap.fr/api/interpreter",
    ).split(",")
    if u.strip()
]

USER_AGENT = os.environ.get(
    "USER_AGENT", "neighbourhood-similarity/1.0 (https://devin.ai)"
)

# Minimum seconds between request starts per host — keeps us well inside the
# fair-use policies of the public Nominatim (1 req/s) and Overpass servers.
MIN_REQUEST_INTERVAL_S = float(os.environ.get("MIN_REQUEST_INTERVAL_S", "1.5"))
# Extra delay before retrying a query on the next Overpass mirror.
FAILOVER_DELAY_S = float(os.environ.get("FAILOVER_DELAY_S", "3"))
# Circuit breaker: after an endpoint fails, skip it for this many seconds
# (multiplied by its consecutive-failure count) so dead mirrors stop eating
# the per-request timeout on every candidate.
FAIL_COOLDOWN_S = float(os.environ.get("FAIL_COOLDOWN_S", "60"))

# Round-robin pointer into OVERPASS_URLS so concurrent queries spread across mirrors.
_endpoint_index = 0
_endpoint_lock = asyncio.Lock()

# Per-host throttle state (keyed by endpoint URL).
_rate_locks: dict[str, asyncio.Lock] = {}
_last_request_at: dict[str, float] = {}

# Circuit-breaker state: url -> (consecutive failures, skip-until timestamp).
_endpoint_fails: dict[str, int] = {}
_endpoint_backoff: dict[str, float] = {}

# When every mirror fails, all in-flight queries share one pause before
# retrying — a gate so 24 concurrent candidates wait once, not 24 times.
# The wait length is surfaced to the client as a countdown.
ALL_MIRRORS_WAIT_S = float(os.environ.get("ALL_MIRRORS_WAIT_S", "60"))
_retry_gate = asyncio.Event()
_retry_gate.set()
# Per-request callback (set by the search handler) that receives the wait
# duration so the UI can show a live countdown.
_wait_notify: ContextVar = ContextVar("overpass_wait_notify", default=None)


def set_wait_notifier(fn):
    return _wait_notify.set(fn)


def reset_wait_notifier(token):
    _wait_notify.reset(token)


async def _global_pause():
    """Pause once across all concurrent queries, then let everyone retry.

    The first caller to arrive clears the gate, notifies the UI, and sleeps;
    latecomers just wait on the same gate and all resume together.
    """
    if _retry_gate.is_set():
        _retry_gate.clear()
        try:
            notify = _wait_notify.get()
            if notify:
                await notify(ALL_MIRRORS_WAIT_S)
            await asyncio.sleep(ALL_MIRRORS_WAIT_S)
        finally:
            _retry_gate.set()
    else:
        await _retry_gate.wait()


def _mark_failure(url: str):
    fails = _endpoint_fails.get(url, 0) + 1
    _endpoint_fails[url] = fails
    _endpoint_backoff[url] = time.monotonic() + FAIL_COOLDOWN_S * fails


def _mark_success(url: str):
    _endpoint_fails[url] = 0
    _endpoint_backoff[url] = 0.0


async def _throttle(host_key: str):
    """Wait until MIN_REQUEST_INTERVAL_S has passed since the last request to
    this host, then mark the start time. Requests to the same host serialize
    here; different hosts proceed independently."""
    lock = _rate_locks.setdefault(host_key, asyncio.Lock())
    async with lock:
        wait = MIN_REQUEST_INTERVAL_S - (
            time.monotonic() - _last_request_at.get(host_key, -1e9)
        )
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at[host_key] = time.monotonic()


async def _next_overpass_url() -> str:
    global _endpoint_index
    async with _endpoint_lock:
        url = OVERPASS_URLS[_endpoint_index % len(OVERPASS_URLS)]
        _endpoint_index += 1
        return url


async def verify_endpoints(client: httpx.AsyncClient):
    """Startup sanity check for Overpass mirrors.

    A broken mirror can answer HTTP 200 with an empty dataset — no remark,
    no error — which is indistinguishable from a legitimately empty rural
    query and silently poisons cached profiles. Probe each mirror against a
    guaranteed-dense point; one returning zero elements gets disabled for
    the session.
    """
    probe = (
        '[out:json][timeout:20];'
        'node["amenity"="cafe"](around:400,40.758,-73.9855);'
        "out 1;"
    )

    async def check(url: str):
        healthy = False
        try:
            await _throttle(url)
            resp = await client.post(
                url,
                data={"data": probe},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                healthy = "remark" not in data and bool(data.get("elements"))
        except (httpx.HTTPError, ValueError):
            pass
        if healthy:
            _mark_success(url)
        else:
            # Effectively session-long disable — far beyond a normal cooldown.
            _endpoint_backoff[url] = time.monotonic() + 86400
            print(f"[osm] Overpass mirror failed sanity check, disabled: {url}")

    await asyncio.gather(*(check(u) for u in OVERPASS_URLS))


class GeocodeError(Exception):
    pass


class OverpassError(Exception):
    pass


class TTLCache:
    """Tiny cache with TTL, optionally persisted to a pickle file.

    Timestamps are wall-clock so entries keep their correct age across
    process restarts.
    """

    def __init__(
        self,
        ttl_seconds: int = 24 * 3600,
        max_size: int = 2048,
        persist_path: str | None = None,
    ):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.persist_path = persist_path
        self._data: dict = {}
        if persist_path:
            self.load()

    def get(self, key):
        entry = self._data.get(key)
        if entry is None:
            return None
        value, ts = entry
        if time.time() - ts > self.ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key, value):
        if len(self._data) >= self.max_size:
            # drop oldest quarter
            for k in list(self._data.keys())[: self.max_size // 4]:
                self._data.pop(k, None)
        self._data[key] = (value, time.time())

    def drop_where(self, pred):
        for k in [k for k, (v, _ts) in self._data.items() if pred(v)]:
            self._data.pop(k, None)

    def load(self):
        try:
            with open(self.persist_path, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, dict):
                self._data = data
        except (OSError, pickle.PickleError, EOFError, ValueError):
            pass

    def save(self):
        if not self.persist_path:
            return
        tmp = f"{self.persist_path}.tmp"
        try:
            with open(tmp, "wb") as f:
                pickle.dump(self._data, f)
            os.replace(tmp, self.persist_path)
        except OSError:
            pass


async def geocode(address: str, client: httpx.AsyncClient) -> dict:
    """Geocode an address with Nominatim. Returns {lat, lon, display_name, address}."""
    await _throttle(NOMINATIM_URL)
    resp = await client.get(
        f"{NOMINATIM_URL}/search",
        params={
            "q": address,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    if resp.status_code == 403:
        raise GeocodeError(
            "Geocoding service rejected our User-Agent (HTTP 403). Set a real "
            "contact in USER_AGENT in .env — placeholder addresses are blocked."
        )
    if resp.status_code != 200:
        raise GeocodeError(f"Geocoding service returned HTTP {resp.status_code}")
    results = resp.json()
    if not results:
        raise GeocodeError(f"Could not find address: {address}")
    top = results[0]
    addr = top.get("address", {})
    name = (
        addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("quarter")
        or addr.get("city_district")
        or addr.get("town")
        or addr.get("city")
        or addr.get("village")
        or top.get("display_name", "").split(",")[0]
    )
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top.get("display_name", address),
        "name": name,
    }


async def reverse_geocode(lat: float, lon: float, client: httpx.AsyncClient) -> dict:
    """Resolve coordinates to an address with Nominatim /reverse."""
    await _throttle(NOMINATIM_URL)
    resp = await client.get(
        f"{NOMINATIM_URL}/reverse",
        params={
            "lat": lat,
            "lon": lon,
            "format": "json",
            "zoom": 18,
            "addressdetails": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    if resp.status_code == 403:
        raise GeocodeError(
            "Geocoding service rejected our User-Agent (HTTP 403). Set a real "
            "contact in USER_AGENT in .env — placeholder addresses are blocked."
        )
    if resp.status_code != 200:
        raise GeocodeError(f"Geocoding service returned HTTP {resp.status_code}")
    data = resp.json()
    display = data.get("display_name")
    if not display:
        raise GeocodeError("No address found at that location")
    addr = data.get("address", {})
    short = (
        addr.get("road")
        or addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("village")
        or addr.get("town")
        or addr.get("city")
        or display.split(",")[0]
    )
    if addr.get("house_number") and addr.get("road"):
        short = f"{addr['house_number']} {addr['road']}"
    return {
        "lat": lat,
        "lon": lon,
        "display_name": display,
        "short_name": short,
    }


async def overpass_query(query: str, client: httpx.AsyncClient) -> dict:
    """Run an Overpass QL query across configured mirrors.

    Endpoints in circuit-breaker cooldown are skipped — a mirror that just
    timed out stops burning the full request timeout on every candidate.
    """
    urls: list[str] = []
    for _ in range(len(OVERPASS_URLS)):
        url = await _next_overpass_url()
        if url not in urls:
            urls.append(url)

    last_error: Exception | None = None
    for round_ in range(2):
        if round_:
            # Every mirror just failed — pause globally (client shows a
            # countdown), then give them all one more chance.
            await _global_pause()
        now = time.monotonic()
        available = [u for u in urls if _endpoint_backoff.get(u, 0) <= now]
        if not available:
            # Everything is cooling down — retry the soonest-available mirror.
            available = [min(urls, key=lambda u: _endpoint_backoff.get(u, 0))]
        for i, url in enumerate(available):
            if i > 0:
                # Give the previous mirror a moment before moving on.
                await asyncio.sleep(FAILOVER_DELAY_S)
            await _throttle(url)
            try:
                resp = await client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=90,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Overpass reports server-side timeouts/runtime errors as
                    # HTTP 200 with a "remark" field (and usually no elements).
                    # Treating it as success would cache an all-empty profile.
                    if "remark" not in data:
                        _mark_success(url)
                        return data
                    last_error = OverpassError(f"{url}: {data['remark']}")
                else:
                    last_error = OverpassError(
                        f"{url} returned HTTP {resp.status_code}"
                    )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = OverpassError(f"{url}: {exc}")
            _mark_failure(url)
    raise last_error or OverpassError("No Overpass endpoints configured")


async def find_places(lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[dict]:
    """Find named neighbourhoods/suburbs/towns near a point within radius_m."""
    place_re = "neighbourhood|suburb|quarter|city_district|borough|town|village|hamlet|residential"
    query = f"""
[out:json][timeout:60];
(
  node["place"~"{place_re}"](around:{radius_m},{lat},{lon});
  way["place"~"{place_re}"](around:{radius_m},{lat},{lon});
  relation["place"~"{place_re}"](around:{radius_m},{lat},{lon});
);
out center 250;
"""
    data = await overpass_query(query, client)
    places = []
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
        places.append(
            {
                "name": name,
                "lat": float(elat),
                "lon": float(elon),
                "place_type": tags.get("place", "place"),
            }
        )
    return places


async def fetch_pois(lat: float, lon: float, radius_m: int, client: httpx.AsyncClient) -> list[dict]:
    """Fetch POI elements (tags only) around a point for profile classification."""
    query = f"""
[out:json][timeout:60];
(
  nwr["amenity"](around:{radius_m},{lat},{lon});
  nwr["shop"](around:{radius_m},{lat},{lon});
  nwr["leisure"](around:{radius_m},{lat},{lon});
  nwr["tourism"~"museum|gallery|attraction|theme_park|zoo|aquarium|artwork"](around:{radius_m},{lat},{lon});
  nwr["natural"~"water|coastline|beach|bay|wood|scrub|grassland"](around:{radius_m},{lat},{lon});
  nwr["waterway"](around:{radius_m},{lat},{lon});
  nwr["landuse"~"recreation_ground|village_green|grass|forest|meadow"](around:{radius_m},{lat},{lon});
  nwr["public_transport"](around:{radius_m},{lat},{lon});
  nwr["railway"~"station|tram_stop|halt|subway_entrance"](around:{radius_m},{lat},{lon});
  nwr["highway"~"bus_stop"](around:{radius_m},{lat},{lon});
);
out tags 40000;
"""
    data = await overpass_query(query, client)
    return data.get("elements", [])
