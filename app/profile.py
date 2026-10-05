"""Profile building: classify OSM elements into amenity categories."""

CATEGORIES = [
    "restaurants",
    "cafes",
    "nightlife",
    "shopping",
    "groceries",
    "lodging",
    "healthcare",
    "schools",
    "parks",
    "waterfront",
    "culture",
    "fitness",
    "transit",
    "cycling",
    "nuisance",
]

CATEGORY_LABELS = {
    "restaurants": "Restaurants",
    "cafes": "Cafes",
    "nightlife": "Bars & nightlife",
    "shopping": "Shopping",
    "groceries": "Groceries & markets",
    "lodging": "Hotels & tourism",
    "healthcare": "Healthcare",
    "schools": "Schools & childcare",
    "parks": "Parks & green space",
    "waterfront": "Waterfront",
    "culture": "Arts & culture",
    "fitness": "Sports & fitness",
    "transit": "Public transit",
    "cycling": "Cycling",
    "nuisance": "Industrial & nuisances",
}

_AMENITY_MAP = {
    "restaurants": {
        "restaurant", "fast_food", "food_court", "food_hall", "bbq", "biergarten",
    },
    "cafes": {"cafe", "coffee_shop", "tea", "ice_cream", "juice_bar"},
    "nightlife": {"bar", "pub", "nightclub", "wine_bar", "taproom", "lounge", "karaoke_box"},
    "groceries": {"marketplace", "food_hall_market"},
    "lodging": set(),  # handled via tourism tag
    "healthcare": {"hospital", "clinic", "doctors", "dentist", "pharmacy"},
    "schools": {"school", "kindergarten", "childcare", "college", "university", "driving_school"},
    "parks": set(),  # handled via leisure/natural tags
    "waterfront": {"marina", "ferry_terminal"},
    "culture": {
        "arts_centre", "library", "community_centre", "theatre", "cinema",
        "events_venue", "social_centre", "planetarium",
    },
    "fitness": {"fitness_centre", "gym", "dojo", "swimming_pool_area"},
    "transit": {"bus_station", "ferry_terminal"},  # ferry counts both ways
    "cycling": {"bicycle_parking", "bicycle_rental", "bicycle_repair_station"},
    "nuisance": {"waste_transfer_station", "waste_disposal"},
}

_SHOP_MAP = {
    "groceries": {
        "supermarket", "convenience", "greengrocer", "bakery", "butcher",
        "deli", "farm", "health_food", "seafood", "cheese", "wine", "alcohol",
        "frozen_food", "pastry", "spices",
    },
    "cycling": {"bicycle"},
    # everything else with shop=* counts as shopping
}

_LEISURE_MAP = {
    "parks": {
        "park", "garden", "playground", "dog_park", "nature_reserve", "common",
        "greenfield", "recreation_ground", "village_green", "miniature_golf",
    },
    "fitness": {
        "fitness_centre", "sports_centre", "stadium", "pitch", "track",
        "swimming_pool", "golf_course", "ice_rink", "bowling_alley", "climbing",
        "skatepark", "tennis", "water_park",
    },
    "culture": {"amusement_arcade"},
    "waterfront": {"marina"},
}

_TOURISM_CULTURE = {
    "museum", "gallery", "attraction", "theme_park", "zoo", "aquarium", "artwork",
}

_TOURISM_LODGING = {"hotel", "hostel", "guest_house", "motel"}

_NATURAL_WATER = {"water", "coastline", "beach", "bay"}

_LANDUSE_PARKS = {"recreation_ground", "village_green", "grass", "forest", "meadow"}

_LANDUSE_NUISANCE = {"industrial", "landfill", "quarry", "brownfield"}

_RAIL_TRANSIT = {"station", "tram_stop", "halt", "subway_entrance"}


def classify_element(tags: dict) -> set[str]:
    """Map an OSM element's tags to our categories."""
    cats = set()
    amenity = tags.get("amenity")
    if amenity:
        for cat, values in _AMENITY_MAP.items():
            if amenity in values:
                cats.add(cat)
    shop = tags.get("shop")
    if shop:
        matched = False
        for cat, values in _SHOP_MAP.items():
            if shop in values:
                cats.add(cat)
                matched = True
                break
        if not matched:
            cats.add("shopping")
    leisure = tags.get("leisure")
    if leisure:
        for cat, values in _LEISURE_MAP.items():
            if leisure in values:
                cats.add(cat)
    tourism = tags.get("tourism")
    if tourism:
        if tourism in _TOURISM_CULTURE:
            cats.add("culture")
        if tourism in _TOURISM_LODGING:
            cats.add("lodging")
    natural = tags.get("natural")
    if natural:
        if natural in _NATURAL_WATER:
            cats.add("waterfront")
        elif natural in ("wood", "scrub", "grassland"):
            cats.add("parks")
    waterway = tags.get("waterway")
    if waterway:
        cats.add("waterfront")
    landuse = tags.get("landuse")
    if landuse:
        if landuse in _LANDUSE_PARKS:
            cats.add("parks")
        if landuse in _LANDUSE_NUISANCE:
            cats.add("nuisance")
    highway = tags.get("highway")
    if highway == "bus_stop":
        cats.add("transit")
    if highway == "cycleway":
        cats.add("cycling")
    railway = tags.get("railway")
    if railway:
        if railway in _RAIL_TRANSIT:
            cats.add("transit")
        if railway == "rail":
            cats.add("nuisance")
    if tags.get("public_transport"):
        cats.add("transit")
    if tags.get("healthcare"):
        cats.add("healthcare")
    if (
        tags.get("aeroway") in ("aerodrome", "helipad")
        or tags.get("man_made") in ("works", "wastewater_plant")
        or tags.get("power") == "plant"
    ):
        cats.add("nuisance")
    return cats


def build_profile(poi_elements: list[dict]) -> dict[str, int]:
    """Count POIs per category for a set of Overpass elements."""
    counts = {c: 0 for c in CATEGORIES}
    for el in poi_elements:
        tags = el.get("tags") or {}
        for cat in classify_element(tags):
            counts[cat] += 1
    return counts
