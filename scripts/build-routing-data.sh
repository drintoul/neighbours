#!/usr/bin/env bash
# Builds osm-data/routing/regions.osrm* — the OSRM (MLD) routing graph for
# the self-hosted `osrm` compose service. Merges the RAW Geofabrik extracts
# (routing needs the full road network — the tag-filtered Overpass PBF is
# useless for this), then runs extract/partition/customize inside the
# osrm/osrm-backend image. Re-run to refresh the routing data.
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRACTS=osm-data/extracts
RT_DIR=osm-data/routing
mkdir -p "$RT_DIR"

MERGED="$RT_DIR/regions.osm.pbf"

# Merge the raw regional extracts (download them first with
# build-overpass-data.sh, or they are fetched here on demand).
declare -a RAW=()
for name in british-columbia washington florida; do
  raw="$EXTRACTS/$name.osm.pbf"
  if [ ! -f "$raw" ]; then
    echo ">> missing $raw — run scripts/build-overpass-data.sh first"
    exit 1
  fi
  RAW+=("$raw")
done

echo ">> merging raw extracts -> $MERGED"
osmium merge --overwrite "${RAW[@]}" -o "$MERGED"

# OSRM pipeline (car profile). MLD needs extract -> partition -> customize.
for step in extract partition customize; do
  echo ">> osrm-$step"
  case "$step" in
    extract)
      docker run --rm -v "$PWD/$RT_DIR":/data osrm/osrm-backend \
        osrm-extract -p /opt/car.lua "/data/regions.osm.pbf"
      ;;
    *)
      docker run --rm -v "$PWD/$RT_DIR":/data osrm/osrm-backend \
        "osrm-$step" /data/regions.osrm
      ;;
  esac
done

echo ">> done — start routing with: docker compose up -d osrm"
