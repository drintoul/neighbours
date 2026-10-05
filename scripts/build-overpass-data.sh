#!/usr/bin/env bash
# Builds osm-data/db/regions.osm.pbf for the self-hosted Overpass service:
# downloads Geofabrik extracts, filters each down to the POI tag keys the app
# actually queries (much faster Overpass import, much smaller DB), and merges
# them. Re-run to refresh the data, then recreate the `overpass` service.
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRACTS=osm-data/extracts
DB_DIR=osm-data/db
mkdir -p "$EXTRACTS" "$DB_DIR"

# Geofabrik regional extracts (North America).
REGIONS=(
  "british-columbia|https://download.geofabrik.de/north-america/canada/british-columbia-latest.osm.pbf"
  "washington|https://download.geofabrik.de/north-america/us/washington-latest.osm.pbf"
  "florida|https://download.geofabrik.de/north-america/us/florida-latest.osm.pbf"
)

# Tags the app's POI/profile/place queries use (see app/osm.py), restricted
# to the values each query actually matches. `nw/` (not `nwr/`) skips
# relations — keeping them would pull every member way+node of giant
# boundary relations, and matching ways drag in all their nodes, so
# value-level patterns matter most for highway/railway/tourism/etc.
PATTERNS=(
  nw/amenity nw/shop nw/leisure
  nw/tourism=museum nw/tourism=gallery nw/tourism=attraction
  nw/tourism=theme_park nw/tourism=zoo nw/tourism=aquarium
  nw/tourism=artwork nw/tourism=hotel nw/tourism=hostel
  nw/tourism=guest_house nw/tourism=motel
  nw/natural=water nw/natural=coastline nw/natural=beach nw/natural=bay
  nw/natural=wood nw/natural=scrub nw/natural=grassland
  nw/waterway
  nw/landuse=recreation_ground nw/landuse=village_green nw/landuse=grass
  nw/landuse=forest nw/landuse=meadow nw/landuse=industrial
  nw/landuse=landfill nw/landuse=quarry nw/landuse=brownfield
  nw/aeroway=aerodrome nw/aeroway=helipad
  nw/man_made=works nw/man_made=wastewater_plant
  nw/power=plant
  nw/healthcare nw/public_transport
  nw/railway=station nw/railway=tram_stop nw/railway=halt
  nw/railway=subway_entrance nw/railway=rail
  nw/highway=bus_stop nw/highway=cycleway
  nw/place
)

for entry in "${REGIONS[@]}"; do
  name="${entry%%|*}"
  url="${entry##*|}"
  raw="$EXTRACTS/$name.osm.pbf"
  if [ ! -f "$raw" ]; then
    echo ">> downloading $name"
    curl -fL --retry 3 -o "$raw" "$url"
  else
    echo ">> $name already downloaded (delete $raw to re-fetch)"
  fi
done

FILTERED=()
for entry in "${REGIONS[@]}"; do
  name="${entry%%|*}"
  out="$EXTRACTS/$name.filtered.osm.pbf"
  # osmium adds referenced objects (nodes of matching ways, members of
  # matching relations) by default — geometry and `out center` keep working.
  echo ">> filtering $name"
  osmium tags-filter --overwrite "$EXTRACTS/$name.osm.pbf" \
    -o "$out" "${PATTERNS[@]}"
  FILTERED+=("$out")
done

echo ">> merging regions -> $DB_DIR/regions.osm.pbf"
osmium merge --overwrite "${FILTERED[@]}" -o "$DB_DIR/regions.osm.pbf"

# The wiktorn/overpass-api importer pipes the planet file through bunzip2,
# so it must be bzip2-compressed XML, not PBF. Single-threaded — takes a
# while (~10GB+ of XML).
echo ">> converting to bz2 XML for the Overpass importer (slow)"
osmium cat --overwrite "$DB_DIR/regions.osm.pbf" -o "$DB_DIR/regions.osm.bz2"

echo ">> done — start the import with: docker compose up -d overpass"
