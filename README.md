# Neighbourhood Similarity Finder

![Screenshot of the app: map of Saint Petersburg, FL with match markers, result
cards showing per-category comparison bars, and the feature-importance
sidebar](screenshot.png)

Enter an address, pick a search radius (up to 100 km), and find nearby
neighbourhoods with a similar mix of amenities — restaurants, cafés, nightlife,
shopping, groceries, parks, waterfront, arts & culture, fitness, schools,
healthcare, hotels, transit and cycling infrastructure — and a similar level of
industrial nuisances.

Everything runs on **free OpenStreetMap services** — no API keys, no accounts,
no quotas to manage. Geocoding comes from **Nominatim** and point-of-interest
data from the **Overpass API**.

This instance is initialized with a **self-hosted Overpass database covering
one province and two states — British Columbia, Washington and Florida** —
so searches there are answered locally, fast and reliably. The local data
was built from Geofabrik extracts dated **2026-10-03**; the UI shows the
live data date under "How does it work?" (served from the instance's
`/api/timestamp`). Re-run `scripts/build-overpass-data.sh` to refresh it.
**Other geographies work too** — they just rely on the public Overpass
mirrors, which are slower and may occasionally be unavailable (the app
retries and fails over automatically, but a search may still time out —
just try again). See [Self-hosted Overpass](#self-hosted-overpass-bc--wa--fl).

## Why I built this

My wife and I loved our neighbourhood of Madison Park in Seattle, and later
our neighbourhood in Saint Petersburg, Florida. When we started thinking about
where to buy our next house, we wanted to find neighbourhoods with the same
*feel* — the same mix of restaurants, shops, parks, and waterfront — rather
than searching by price or square footage alone. This tool does exactly that:
give it an address in a neighbourhood you like, and it ranks nearby
neighbourhoods by how similar their amenity mix is.

## What does "similar" mean?

Each neighbourhood is reduced to a *profile*: the number of points of interest
in 15 categories, counted within a fixed local radius (default ~1.5 km) of its
centre:

| Category | What it counts (OSM tags, simplified) |
|---|---|
| Restaurants | `amenity=restaurant`, `fast_food`, `food_court`, … |
| Cafés | `amenity=cafe`, `coffee_shop`, `ice_cream` |
| Bars & nightlife | `amenity=bar`, `pub`, `nightclub`, `biergarten` |
| Shopping | any `shop=*` except grocery-type stores and bike shops |
| Groceries | `shop=supermarket`, `bakery`, `greengrocer`, `deli`, … |
| Hotels & tourism | `tourism=hotel`, `hostel`, `guest_house`, `motel` |
| Healthcare | hospitals, clinics, pharmacies, dentists, `healthcare=*` |
| Schools | `amenity=school`, `kindergarten`, `university` |
| Parks & green space | `leisure=park`, `garden`, `playground`, woods, meadows |
| Waterfront | `natural=water`, `coastline`, `beach`, waterways, marinas |
| Arts & culture | cinemas, theatres, museums, galleries, libraries |
| Sports & fitness | gyms, pools, pitches, courts, golf courses |
| Public transit | bus stops, train/tram/subway stations, ferry terminals |
| Cycling | `highway=cycleway`, bike parking/rental, `shop=bicycle` |
| Industrial & nuisances | `landuse=industrial`, rail lines, aerodromes, power plants, waste facilities |

The last one is the odd category out: it measures *negative* features, so a
match scores similarity of character including how industrial an area feels —
a café-rich neighbourhood next to a rail yard won't match an identical café
count in a leafy suburb as closely.

Candidates are scored by **cosine similarity of log-scaled profiles**. Two
consequences worth understanding:

- **Mix, not magnitude.** `log1p` compresses big counts, so a dense city core
  (500 restaurants) and a smaller centre (60 restaurants) still score highly if
  the *proportions* across categories match. Similarity measures character, not
  size.
- **It reflects OSM data coverage.** Scores are only as good as the local map
  data. Well-mapped regions produce better comparisons than sparsely mapped
  ones.

## How it works

```
address ──► geocode (Nominatim)
              │
              ▼
     profile source area ──────►  discover named places
     (POI counts in ~1.5 km)      (place=suburb/neighbourhood/…)
              │                            │
              │                    ≤ MAX_CANDIDATES (25): nearest few
              │                    per 4 equal-width distance bands
              │                            │
              ▼                            ▼
               profile each candidate concurrently
              (with Overpass mirror failover + retries)
                            │
                            ▼
              cosine similarity ──► ranked matches (top TOP_MATCHES, 10)
```

In more detail:

1. **Geocoding** — the address is resolved to coordinates with Nominatim.
2. **Source profile** — all amenity/shop/leisure/natural/transit POIs within
   `PROFILE_RADIUS_M` are fetched in one Overpass query and classified into the
   15 categories.
3. **Candidate discovery** — named `place` features (neighbourhoods, quarters,
   suburbs, towns, villages) inside your search radius are queried, deduplicated
   by name, and overlapping centroids are suppressed.
4. **Candidate sampling** — selection is deterministic (no randomness): the
   distance range from ~1 km to the farthest named place is split into **4
   equal-width bands**, and the nearest candidates from each band are kept
   (≈`MAX_CANDIDATES` total). That guarantees a mix of close and distant
   neighbourhoods rather than only the closest ring.
5. **Candidate profiling** — each candidate gets the same POI profile, fetched
   concurrently (`OVERPASS_CONCURRENCY`) across multiple Overpass mirrors with
   automatic failover and one retry per failure.
6. **Ranking** — cosine similarity of log-scaled profiles → percentage score.

## Run

```bash
cp .env.example .env        # adjust values if needed
docker compose up --build
```

Then open `http://localhost:${APP_PORT}` (8000 by default).

The UI shows the source address as a pin with a red circle marking its ~1.5 km
neighbourhood radius, a dashed circle for your search radius, and colour-coded
match markers (red → green = low → high similarity). Click a result card to
jump to it on the map; the bars in each card compare your neighbourhood's
category counts (grey) with the match's (blue). You can also **click anywhere
on the map** — it drops a green marker and reverse-geocodes that spot into the
search bar. Searches are shareable: the URL updates to
`?address=…&radius=…`, and opening such a link runs the search immediately.

### Weighting features

The **Feature importance** sidebar (left of the map) has one slider per
category with three coarse stops: **Less** (0.5×), **Same** (1×), and
**More** (2×) — one notch in each direction from neutral. Everything starts
at *Same*; dragging a slider **instantly re-ranks** all evaluated candidates
in the browser — the profile data is already local, so no new API calls are
needed. Your settings persist across searches; **Reset** returns all sliders
to *Same*.

## Expose publicly with Cloudflare Tunnel

The app can be published on a hostname you control without opening ports:

1. In the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com),
   go to **Networks → Tunnels → Create a tunnel** (choose *cloudflared*).
2. Copy the tunnel token into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
3. Set the tunnel's public hostname to forward to `http://neighbourhood-similarity:8000`
   (service type **HTTP**).
4. `docker compose up --build` — that's it. The `tunnel` service is always
   part of the stack: with a token set it runs the tunnel; without one it
   just idles (check `docker compose logs tunnel` to confirm which).

## Configuration

Copy `.env.example` to `.env` and edit — `.env` is gitignored and read
automatically by `docker compose`. Every value also has a built-in default:

| Variable | Default | Description |
|---|---|---|
| `APP_PORT` | `8000` | Host port the UI is published on |
| `DATA_DIR` | `./data` | Host dir bind-mounted at `/data` — caches persist across restarts |
| `NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | Geocoding endpoint |
| `OVERPASS_URLS` | three public mirrors | Comma-separated Overpass endpoints (failover); supports `\|bbox:` + `\|nolimit` hints |
| `ROUTER_URLS` | public OSRM demo | OSRM endpoints for drive times/route previews — same endpoint syntax |
| `USER_AGENT` | `neighbourhood-similarity/1.0` | Sent with all OSM requests — add your contact info |
| `PROFILE_RADIUS_M` | `1500` | Radius that defines one neighbourhood's character |
| `MAX_CANDIDATES` | `25` | Max candidate neighbourhoods profiled per search |
| `OVERPASS_CONCURRENCY` | `4` | Concurrent Overpass queries |
| `TOP_MATCHES` | `10` | Matches returned/displayed |
| `MIN_REQUEST_INTERVAL_S` | `1.5` | Min seconds between requests to the same host |
| `FAILOVER_DELAY_S` | `3` | Wait before a failed query retries on the next mirror |
| `RETRY_DELAY_S` | `10` | Base wait before retrying a failed candidate (+0–4 s jitter) |
| `FAIL_COOLDOWN_S` | `60` | Skip a failed mirror for N sec × its consecutive-failure count |
| `ALL_MIRRORS_WAIT_S` | `60` | Pause before retrying when every mirror fails (shown as a countdown in the UI) |
| `PROFILE_BUDGET_S` | `240` | Hard cap on candidate profiling — partial results returned |
| `MANDATORY_BUDGET_S` | `150` | Hard cap on geocode+discovery before erroring out |
| `SEARCH_RATE_LIMIT` | `1` | Max `/api/search` calls per IP per window (anti-abuse for public deployments) |
| `SEARCH_RATE_WINDOW_S` | `60` | Rate-limit window in seconds |
| `ROUTE_RATE_LIMIT` | `20` | Max `/api/route` calls per IP per window |
| `ROUTE_RATE_WINDOW_S` | `60` | Route rate-limit window in seconds |

For heavy use, point `OVERPASS_URLS`/`NOMINATIM_URL` at your own instances —
public endpoints are rate-limited and shared.

### Self-hosted Overpass (BC + WA + FL)

The compose stack includes a `overpass` service
(`wiktorn/overpass-api`) serving a merged, tag-filtered extract of British
Columbia, Washington, and Florida. To build the data and start it:

```bash
./scripts/build-overpass-data.sh   # download + tag-filter + merge extracts
docker compose up -d overpass      # first boot imports the PBF (~minutes)
```

Then put it first in `OVERPASS_URLS`, tagged with its coverage so searches
outside the three regions skip it and go straight to the public mirrors
(`bbox:` is `west:south:east:north`, one per region):

```env
OVERPASS_URLS=http://overpass/api/interpreter|nolimit|bbox:-138.5:48.33:-114.15:59.9|bbox:-124.85:45.6:-116.95:48.95|bbox:-87.6:24.4:-80.0:30.95,https://overpass-api.de/api/interpreter,…
```

`nolimit` skips the per-host politeness throttle (it exists for public
mirrors, not our own instance). Raw downloads and the Overpass database live
under `osm-data/` (gitignored — PBF extracts and generated DB files are never
committed). Re-run the script and recreate the `overpass` container to
refresh data. Endpoints without a `bbox` hint are treated as global and
queried for any location.

### Self-hosted routing (OSRM)

Result cards show drive time and distance to each match, and clicking a card
draws the actual driving route on the map. These come from an `osrm` service
(`osrm/osrm-backend`, car profile) built from the same three raw extracts —
the tag-filtered Overpass PBF can't be reused (it drops the road network).
Build the routing graph once:

```bash
./scripts/build-routing-data.sh   # merge raw extracts + osrm extract/partition/customize
docker compose up -d osrm
```

`ROUTER_URLS` uses the same `|bbox:`/`|nolimit` syntax as `OVERPASS_URLS`;
routes where both endpoints are inside BC/WA/FL go to the local router,
everything else uses the public OSRM demo server. If no router answers,
searches still return matches — just without drive stats.

## Data & caching

Profiles, geocode results, and place lists are cached in `DATA_DIR`
(`./data` by default, bind-mounted into the container at `/data`) as pickle
files:

- `geocode.pkl` — address → coordinates (24 h TTL)
- `places.pkl` — point+radius → named places (6 h TTL)
- `profiles.pkl` — point → category counts (6 h TTL)

Caches are saved every 2 minutes and on graceful shutdown, so a container
restart keeps warm data. **Repeated searches in the same area get faster** as
the profile cache accumulates. Delete `data/` to clear everything.

## Performance & limits

- **Cold searches take 30–120 s.** Profiling ~25 candidates against rate-limited
  public Overpass mirrors is the bottleneck. Larger radii and denser areas are
  slower. Subsequent searches benefit from the cache.
- **Candidates may be skipped.** If a mirror refuses a query after retry, that
  neighbourhood is silently dropped — `candidates_evaluated` in the response
  shows how many actually scored.
- **Only named places are compared.** Areas without `place=*` features in OSM
  can't be candidates.
- **Be polite.** Public OSM services are community resources. The default
  concurrency and candidate caps exist to stay within their fair-use policies;
  self-host if you need more. Requests are additionally throttled to a
  configurable minimum interval per host (`MIN_REQUEST_INTERVAL_S`), mirror
  failovers pause first (`FAILOVER_DELAY_S`), and candidate retries are
  jittered (`RETRY_DELAY_S`).

## API

`POST /api/search` — body: `{"address": "...", "radius_km": 25, "weights": {"parks": 2, "restaurants": 0.5}}`

`weights` is optional: per-category multipliers (0–4, clamped; 1 = neutral)
that scale each category before the cosine comparison. The response's
`matches` are the top `TOP_MATCHES`; `evaluated` contains every scored
candidate so clients can re-rank locally.

The endpoint **streams newline-delimited JSON** (`application/x-ndjson`):

- `{"type":"progress","message":"…","done":n,"total":n}` — live pipeline status
- `{"type":"wait","seconds":60,"message":"…"}` — all mirrors failed; the server
  pauses this long before retrying (the UI counts it down)
- `{"type":"result","data":{…}}` — final payload, shown below
- `{"type":"error","detail":"…"}` — terminal error

```json
{
  "source": {
    "name": "Mitte",
    "display_name": "Alexanderplatz, …, Berlin",
    "lat": 52.52, "lon": 13.41,
    "profile": {"restaurants": 503, "parks": 1169, "…": 0}
  },
  "profile_radius_m": 1500,
  "search_radius_km": 5,
  "candidates_found": 113,
  "candidates_evaluated": 15,
  "matches": [
    {"name": "Kollwitzkiez", "place_type": "quarter", "lat": 52.53,
     "lon": 13.42, "distance_km": 1.45, "score": 100,
     "profile": {"restaurants": 210, "…": 0}}
  ]
}
```

Other endpoints: `GET /api/health`, `GET /api/categories`,
`GET /api/reverse?lat=..&lon=..` (reverse geocoding for map clicks).

## Tests

```bash
docker compose exec neighbourhood-similarity python -m pytest tests -q
```

Covers OSM tag classification, log-scaled/weighted similarity, candidate
sampling, and cache persistence — no network needed.

## Project layout

```
docker-compose.yaml      # app + cloudflared tunnel (idles unless token set)
Dockerfile               # python:3.12-slim + uvicorn
app/
  main.py                # FastAPI: /api/search orchestration, caching, sampling
  osm.py                 # Nominatim + Overpass clients, mirror failover, TTLCache
  profile.py             # OSM tag → 11-category classification
  similarity.py          # log-scaled cosine similarity, haversine
  static/                # Leaflet single-page UI (vanilla JS, no build step)
tests/                   # pytest suite (runs inside the app container)
data/                    # persistent cache (gitignored, bind-mounted)
```

## License

MIT — see [LICENSE](LICENSE).
