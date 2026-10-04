"""Scoring: turn POI profiles into similarity scores."""

from __future__ import annotations

import math

from .profile import CATEGORIES


def profile_vector(profile: dict[str, int]) -> list[float]:
    """log1p compresses large-count categories so shops don't drown out parks."""
    return [math.log1p(profile.get(cat, 0)) for cat in CATEGORIES]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def similarity_score(
    profile_a: dict, profile_b: dict, weights: dict | None = None
) -> float:
    """Return similarity in [0, 1]. 1 = identical mix of amenities.

    Optional per-category weights (typically 0–2) scale each category's
    contribution before the cosine comparison.
    """
    va = profile_vector(profile_a)
    vb = profile_vector(profile_b)
    if weights:
        w = [float(weights.get(cat, 1.0)) for cat in CATEGORIES]
        va = [x * y for x, y in zip(va, w)]
        vb = [x * y for x, y in zip(vb, w)]
    return cosine_similarity(va, vb)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
