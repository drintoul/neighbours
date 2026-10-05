"""Tests for candidate dedup/spread/stratified sampling in _pick_candidates."""

from app.main import _pick_candidates

SRC_LAT, SRC_LON = 49.0, -122.0


def cand(name, lat, lon, place_type="suburb"):
    return {"name": name, "lat": lat, "lon": lon, "place_type": place_type}


def test_dedupes_names_case_insensitive():
    places = [
        cand("Newton", 49.05, -122.0),
        cand(" newton ", 49.07, -122.0),
        cand("Surrey Centre", 49.09, -122.0),
    ]
    picked = _pick_candidates(places, SRC_LAT, SRC_LON, 10)
    names = [p["name"] for p in picked]
    assert names.count("Newton") == 1


def test_drops_candidates_on_top_of_source():
    places = [
        cand("Here", SRC_LAT + 0.001, SRC_LON + 0.001),  # ~0.15 km away
        cand("Far", 49.1, -122.0),
    ]
    picked = _pick_candidates(places, SRC_LAT, SRC_LON, 10)
    assert [p["name"] for p in picked] == ["Far"]


def test_spatial_suppression():
    # B sits ~110 m from A — suppressed once A is picked.
    places = [
        cand("A", 49.05, -122.0),
        cand("B", 49.051, -122.0),
        cand("C", 49.1, -122.0),
    ]
    picked = _pick_candidates(places, SRC_LAT, SRC_LON, 10)
    names = [p["name"] for p in picked]
    assert "A" in names and "C" in names
    assert "B" not in names


def test_respects_max():
    places = [cand(f"p{i}", 49.0 + i * 0.02, -122.0) for i in range(1, 40)]
    picked = _pick_candidates(places, SRC_LAT, SRC_LON, 8)
    assert len(picked) <= 8


def test_far_candidates_survive_distance_stratification():
    # 20 near candidates (~2 km ring) + 20 far (~45 km): without banded
    # sampling the nearest-N alone would pick only the near ones.
    import math

    places = []
    for i in range(20):
        t = 2 * math.pi * i / 20
        places.append(
            cand(f"near{i}", 49.0 + 0.018 * math.cos(t), -122.0 + 0.027 * math.sin(t))
        )
    for i in range(20):
        t = 2 * math.pi * i / 20
        places.append(
            cand(f"far{i}", 49.0 + 0.36 * math.cos(t), -122.0 + 0.55 * math.sin(t))
        )
    picked = _pick_candidates(places, SRC_LAT, SRC_LON, 12)
    assert len(picked) == 12
    assert any(p["distance_km"] > 20 for p in picked)
    assert any(p["distance_km"] < 10 for p in picked)
