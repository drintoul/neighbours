"""Tests for OSM tag → category classification and profile building."""

from app.profile import CATEGORIES, build_profile, classify_element


def test_restaurant():
    assert classify_element({"amenity": "restaurant"}) == {"restaurants"}


def test_cafe():
    assert classify_element({"amenity": "cafe"}) == {"cafes"}


def test_bar_is_nightlife():
    assert classify_element({"amenity": "bar"}) == {"nightlife"}


def test_supermarket_is_groceries_not_shopping():
    assert classify_element({"shop": "supermarket"}) == {"groceries"}


def test_non_grocery_shop_is_shopping():
    assert classify_element({"shop": "clothes"}) == {"shopping"}


def test_park_tags():
    assert classify_element({"leisure": "park"}) == {"parks"}
    assert classify_element({"natural": "wood"}) == {"parks"}
    assert classify_element({"landuse": "meadow"}) == {"parks"}


def test_waterfront():
    assert classify_element({"natural": "beach"}) == {"waterfront"}
    assert classify_element({"waterway": "river"}) == {"waterfront"}
    assert classify_element({"leisure": "marina"}) == {"waterfront"}


def test_culture():
    assert classify_element({"tourism": "museum"}) == {"culture"}
    assert classify_element({"amenity": "theatre"}) == {"culture"}


def test_transit():
    assert classify_element({"highway": "bus_stop"}) == {"transit"}
    assert classify_element({"railway": "station"}) == {"transit"}
    assert classify_element({"public_transport": "platform"}) == {"transit"}


def test_lodging():
    assert classify_element({"tourism": "hotel"}) == {"lodging"}
    assert classify_element({"tourism": "guest_house"}) == {"lodging"}


def test_healthcare():
    assert classify_element({"amenity": "pharmacy"}) == {"healthcare"}
    assert classify_element({"amenity": "hospital"}) == {"healthcare"}
    assert classify_element({"healthcare": "physiotherapist"}) == {"healthcare"}


def test_cycling():
    assert classify_element({"highway": "cycleway"}) == {"cycling"}
    assert classify_element({"amenity": "bicycle_parking"}) == {"cycling"}
    assert classify_element({"shop": "bicycle"}) == {"cycling"}


def test_nuisance():
    assert classify_element({"landuse": "industrial"}) == {"nuisance"}
    assert classify_element({"aeroway": "aerodrome"}) == {"nuisance"}
    assert classify_element({"power": "plant"}) == {"nuisance"}
    assert classify_element({"railway": "rail"}) == {"nuisance"}
    assert classify_element({"amenity": "waste_transfer_station"}) == {"nuisance"}


def test_multi_category_element_counts_once_per_category():
    cats = classify_element({"amenity": "cafe", "shop": "bakery"})
    assert cats == {"cafes", "groceries"}


def test_irrelevant_element():
    assert classify_element({"building": "yes"}) == set()
    assert classify_element({}) == set()


def test_build_profile_counts_and_all_categories_present():
    elements = [
        {"tags": {"amenity": "restaurant"}},
        {"tags": {"amenity": "restaurant"}},
        {"tags": {"leisure": "park"}},
        {"tags": {"amenity": "cafe", "shop": "bakery"}},
        {"tags": {}},
    ]
    p = build_profile(elements)
    assert set(p) == set(CATEGORIES)
    assert p["restaurants"] == 2
    assert p["parks"] == 1
    assert p["cafes"] == 1
    assert p["groceries"] == 1
    assert p["transit"] == 0
