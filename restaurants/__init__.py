"""Nearby Eats — restaurant finder, separate service sharing the OSM plumbing.

Geocode an address or hotel name (Nominatim) → fetch food-service POIs
around it (Overpass) → filter to those within a real drive-time budget
(OSRM) → group the survivors by cuisine.
"""
