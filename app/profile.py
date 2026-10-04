"""Classify OSM elements into neighbourhood-characteristic categories."""

from __future__ import annotations

CATEGORIES = [
    "restaurants",
    "cafes",
    "nightlife",
    "shopping",
    "groceries",
    "parks",
    "waterfront",
    "culture",
    "fitness",
    "schools",
    "transit",
]

CATEGORY_LABELS = {
    "restaurants": "Restaurants",
    "cafes": "Cafés",
    "nightlife": "Bars & nightlife",
    "shopping": "Shopping",
    "groceries": "Groceries",
    "parks": "Parks & green space",
    "waterfront": "Waterfront",
    "culture": "Arts & culture",
    "fitness": "Sports & fitness",
    "schools": "Schools",
    "transit": "Public transit",
}

_AMENITY_MAP = {
    "restaurants": {
        "restaurant", "fast_food", "food_court", "bbq", "biergarten",
    },
    "cafes": {"cafe", "coffee_shop", "ice_cream", "tea"},
    "nightlife": {"bar", "pub", "nightclub", "stripclub"},
    "groceries": {"marketplace"},
    "culture": {
        "cinema", "theatre", "arts_centre", "library", "community_centre",
        "events_venue", "music_venue", "concert_hall", "planetarium",
    },
    "schools": {"school", "kindergarten", "college", "university", "childcare"},
    "transit": {"ferry_terminal"},
}

_SHOP_MAP = {
    "groceries": {
        "supermarket", "greengrocer", "bakery", "butcher", "deli",
        "convenience", "frozen_food", "seafood", "cheese", "farm",
        "health_food", "pastry", "alcohol", "beverages", "wine",
    },
}

_LEISURE_MAP = {
    "parks": {
        "park", "garden", "playground", "nature_reserve", "dog_park",
        "common", "recreation_ground", "picnic_table", "bandstand",
    },
    "fitness": {
        "fitness_centre", "sports_centre", "swimming_pool", "swimming_area",
        "pitch", "golf_course", "track", "climbing", "stadium", "ice_rink",
        "horse_riding", "bowling_alley", "skatepark",
    },
    "waterfront": {"marina", "slipway", "beach_resort"},
}

_TOURISM_CULTURE = {"museum", "gallery", "aquarium", "zoo", "theme_park", "artwork"}

_NATURAL_WATER = {"water", "coastline", "beach", "bay", "shoal", "strait"}
_NATURAL_GREEN = {"wood", "scrub", "grassland", "heath", "tree_row", "fell"}

_LANDUSE_GREEN = {"recreation_ground", "village_green", "grass", "forest", "meadow", "greenfield", "orchard"}

_RAILWAY_TRANSIT = {"station", "tram_stop", "halt", "subway_entrance"}


def classify_element(tags: dict) -> set[str]:
    """Return the set of categories an OSM element's tags belong to."""
    cats: set[str] = set()

    amenity = tags.get("amenity")
    if amenity:
        for cat, values in _AMENITY_MAP.items():
            if amenity in values:
                cats.add(cat)

    shop = tags.get("shop")
    if shop:
        if shop in _SHOP_MAP["groceries"]:
            cats.add("groceries")
        else:
            cats.add("shopping")

    leisure = tags.get("leisure")
    if leisure:
        for cat, values in _LEISURE_MAP.items():
            if leisure in values:
                cats.add(cat)

    if tags.get("tourism") in _TOURISM_CULTURE:
        cats.add("culture")

    natural = tags.get("natural")
    if natural in _NATURAL_WATER or "waterway" in tags:
        cats.add("waterfront")
    elif natural in _NATURAL_GREEN:
        cats.add("parks")

    if tags.get("landuse") in _LANDUSE_GREEN:
        cats.add("parks")

    if (
        "public_transport" in tags
        or tags.get("railway") in _RAILWAY_TRANSIT
        or tags.get("highway") == "bus_stop"
    ):
        cats.add("transit")

    return cats


def build_profile(elements: list[dict]) -> dict[str, int]:
    """Count elements per category. An element can count toward multiple categories."""
    profile = {cat: 0 for cat in CATEGORIES}
    for el in elements:
        for cat in classify_element(el.get("tags", {})):
            profile[cat] += 1
    return profile
