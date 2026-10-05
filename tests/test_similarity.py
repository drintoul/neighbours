"""Tests for log-scaled cosine similarity and weighting."""

import math

import pytest

from app.profile import CATEGORIES
from app.similarity import haversine_km, similarity_score


def prof(**kw):
    return {c: kw.get(c, 0) for c in CATEGORIES}


def test_identical_profiles_score_one():
    p = prof(restaurants=10, parks=5, transit=3)
    assert similarity_score(p, p) == pytest.approx(1.0)


def test_orthogonal_profiles_score_zero():
    assert similarity_score(prof(restaurants=10), prof(parks=10)) == 0.0


def test_zero_profile_scores_zero():
    assert similarity_score(prof(), prof(restaurants=3)) == 0.0


def test_magnitude_is_dampened_by_log_scaling():
    # Same proportions, wildly different counts — log1p keeps the mix
    # dominant, so a small town centre still scores high vs a city core.
    # (Not exactly 1.0 — log scaling breaks strict proportionality.)
    small = prof(restaurants=5, parks=2)
    big = prof(restaurants=500, parks=200)
    assert similarity_score(small, big) > 0.95


def test_log_scaling_dampens_more_than_raw_counts():
    # Non-proportional profiles: log-scaled cosine must exceed raw cosine.
    a = prof(restaurants=5, parks=2)
    b = prof(restaurants=500, parks=50)
    log_score = similarity_score(a, b)

    def raw(p):
        return [float(p[c]) for c in CATEGORIES]

    va, vb = raw(a), raw(b)
    raw_score = sum(x * y for x, y in zip(va, vb)) / math.sqrt(
        sum(x * x for x in va) * sum(y * y for y in vb)
    )
    assert log_score > raw_score


def test_weight_boost_penalizes_candidates_lacking_the_category():
    src = prof(restaurants=10, waterfront=10)
    has = prof(restaurants=8, waterfront=10)
    lacks = prof(restaurants=8, waterfront=0)
    w = {"waterfront": 3}
    assert similarity_score(src, lacks, w) < similarity_score(src, lacks)
    assert similarity_score(src, has, w) > similarity_score(src, has)



def test_zero_weight_ignores_category():
    a = prof(restaurants=10, parks=1)
    b = prof(restaurants=10, parks=50)
    assert similarity_score(a, b, weights={"parks": 0}) == pytest.approx(1.0)


def test_haversine_sanity():
    assert haversine_km(0, 0, 0, 0) == 0.0
    # Vancouver downtown → Langley ≈ 40 km
    d = haversine_km(49.2827, -123.1207, 49.1044, -122.6604)
    assert 30 < d < 50
