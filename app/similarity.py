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


# Categories where a deficit (not a surplus) is the desirable direction.
_NEGATIVE_CATS = {"nuisance"}

# log1p difference that saturates the preference signal: a ~7.4x ratio.
_SAT_K = 2.0

# Max fraction of the base score a fully-expressed preference can add/remove.
_BONUS = 0.3


def similarity_score(
    profile_a: dict, profile_b: dict, weights: dict | None = None
) -> float:
    """Return similarity in [0, 1]. 1 = identical mix of amenities.

    The base score is cosine similarity of log-scaled profiles, dampened
    by the sqrt of the magnitude ratio so a sparse lookalike can't outrank
    a true peer.

    Optional per-category `weights` (1 = neutral) express *preference*, not
    importance: above 1 rewards candidates with more of that category than
    the source, below 1 rewards less. Each category contributes a bonus of
    (w-1) x saturated(log-surplus) / sum|w-1|, scaled by _BONUS — so slider
    positions tilt the ranking toward preferred areas rather than merely
    re-weighing the shape comparison.
    """
    va = profile_vector(profile_a)
    vb = profile_vector(profile_b)
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na == 0 or nb == 0:
        return 0.0
    cos = cosine_similarity(va, vb)
    base = cos * math.sqrt(min(na, nb) / max(na, nb))

    if weights:
        num = den = 0.0
        for i, cat in enumerate(CATEGORIES):
            d = float(weights.get(cat, 1.0)) - 1.0
            if not d:
                continue
            pol = -1.0 if cat in _NEGATIVE_CATS else 1.0
            surplus = pol * (vb[i] - va[i]) / _SAT_K
            num += d * max(-1.0, min(1.0, surplus))
            den += abs(d)
        if den:
            base *= 1.0 + _BONUS * (num / den)

    return min(1.0, max(0.0, base))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
