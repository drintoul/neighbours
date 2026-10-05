"""Driving routes via OSRM — self-hosted instance preferred, public demo
server as fallback. Endpoint syntax mirrors OVERPASS_URLS: comma-separated,
each optionally carrying `|bbox:w:s:e:n` coverage hints (repeatable) and a
`|nolimit` flag to skip the politeness throttle."""

from __future__ import annotations

import asyncio
import os
import time

import httpx

MIN_REQUEST_INTERVAL_S = float(os.environ.get("MIN_REQUEST_INTERVAL_S", "1.5"))


def _parse_router_endpoints() -> list[tuple[str, list[tuple] | None, set]]:
    endpoints = []
    for entry in (
        os.environ.get("ROUTER_URLS") or "https://router.project-osrm.org"
    ).split(","):
        parts = entry.strip().split("|")
        if not parts[0]:
            continue
        boxes = [
            tuple(float(v) for v in p[5:].split(":"))
            for p in parts[1:]
            if p.startswith("bbox:")
        ]
        flags = {p for p in parts[1:] if not p.startswith("bbox:")}
        endpoints.append((parts[0].rstrip("/"), boxes or None, flags))
    return endpoints


ROUTER_ENDPOINTS = _parse_router_endpoints()
_NO_THROTTLE = {u for u, _, flags in ROUTER_ENDPOINTS if "nolimit" in flags}

_last_request_at: dict[str, float] = {}


def _covers(point: tuple[float, float], boxes: list[tuple]) -> bool:
    lat, lon = point
    return any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] for b in boxes)


def _routers_for(points: list[tuple[float, float]]) -> list[str]:
    """Routers whose coverage contains every point (both ends of a route must
    be inside the local dataset, else the query goes to a public mirror)."""
    return [
        url
        for url, boxes, _ in ROUTER_ENDPOINTS
        if boxes is None or all(_covers(p, boxes) for p in points)
    ]


class RoutingError(Exception):
    pass


async def _get(client: httpx.AsyncClient, url: str, path: str) -> dict:
    from .osm import USER_AGENT

    if url not in _NO_THROTTLE:
        wait = MIN_REQUEST_INTERVAL_S - (
            time.monotonic() - _last_request_at.get(url, -1e9)
        )
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at[url] = time.monotonic()
    resp = await client.get(
        f"{url}{path}",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RoutingError(f"{url} returned HTTP {resp.status_code}")
    data = resp.json()
    if data.get("code") != "Ok":
        raise RoutingError(f"{url}: {data.get('code')} {data.get('message', '')}")
    return data


def _coords(points: list[tuple[float, float]]) -> str:
    # OSRM wants lon,lat order
    return ";".join(f"{lon},{lat}" for lat, lon in points)


async def drive_table(
    source: tuple[float, float],
    targets: list[tuple[float, float]],
    client: httpx.AsyncClient,
) -> list[dict | None]:
    """Drive duration (s) + distance (m) from `source` to each target —
    one table request. Per-target None where unroutable; RoutingError when
    every endpoint fails."""
    points = [source] + targets
    routers = _routers_for(points)
    if not routers:
        raise RoutingError("No router covers these points")
    last_error: Exception | None = None
    for url in routers:
        try:
            data = await _get(
                client,
                url,
                f"/table/v1/driving/{_coords(points)}"
                "?sources=0&annotations=duration,distance",
            )
            durations = data.get("durations") or [[]]
            distances = data.get("distances") or [[]]
            out = []
            for i in range(len(targets)):
                d = durations[0][i + 1] if i + 1 < len(durations[0]) else None
                m = distances[0][i + 1] if i + 1 < len(distances[0]) else None
                out.append(
                    None
                    if d is None
                    else {
                        "drive_s": round(d),
                        # distance can be absent even when duration exists
                        "drive_km": None if m is None else round(m / 1000, 1),
                    }
                )
            return out
        except (httpx.HTTPError, ValueError, RoutingError) as exc:
            last_error = RoutingError(f"{url}: {exc}")
    raise last_error or RoutingError("No routers configured")


async def route_geometry(
    source: tuple[float, float],
    target: tuple[float, float],
    client: httpx.AsyncClient,
) -> dict:
    """Full route GeoJSON + duration/distance between two points."""
    points = [source, target]
    routers = _routers_for(points)
    if not routers:
        raise RoutingError("No router covers these points")
    last_error: Exception | None = None
    for url in routers:
        try:
            data = await _get(
                client,
                url,
                f"/route/v1/driving/{_coords(points)}"
                "?overview=full&geometries=geojson",
            )
            route = data["routes"][0]
            return {
                "geometry": route["geometry"],
                "drive_s": round(route["duration"]),
                "drive_km": round(route["distance"] / 1000, 1),
            }
        except (httpx.HTTPError, ValueError, KeyError, IndexError, RoutingError) as exc:
            last_error = RoutingError(f"{url}: {exc}")
    raise last_error or RoutingError("No routers configured")
