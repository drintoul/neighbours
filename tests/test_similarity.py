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


def test_magnitude_mismatch_penalizes_score():
    # Same proportions, wildly different counts — a tiny centre with a
    # fraction of the amenity mass should NOT look like a near-identical
    # match, but log scaling + sqrt dampening keeps the penalty moderate
    # rather than proportional to the raw-count gap.
    small = prof(restaurants=5, parks=2)
    big = prof(restaurants=500, parks=200)
    score = similarity_score(small, big)
    assert 0.2 < score < 0.8
    assert similarity_score(big, big) == pytest.approx(1.0)


def test_similar_magnitude_proportional_profiles_score_high():
    # Same proportions AND similar size — still a near-perfect match.
    a = prof(restaurants=50, parks=20)
    b = prof(restaurants=60, parks=24)
    assert similarity_score(a, b) > 0.95


def test_log_scaling_dampens_more_than_raw_counts():
    # Non-proportional profiles: log-scaled cosine must exceed raw cosine.
    a = prof(restaurants=5, parks=2)
    b = prof(restaurants=500, parks=50)
    log_score = similarity_score(a, b)

    def raw(p):
        return [float(p[c]) for c in CATEGORIES]

    va, vb = raw(a), raw(b)
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    raw_cos = sum(x * y for x, y in zip(va, vb)) / (na * nb)
    # Same size-mismatch penalty applied to raw counts — the raw-count
    # version is far harsher because counts aren't compressed.
    raw_score = raw_cos * math.sqrt(min(na, nb) / max(na, nb))
    assert log_score > raw_score


def test_more_preference_rewards_surplus_and_penalizes_deficit():
    src = prof(restaurants=10, waterfront=10)
    more = prof(restaurants=8, waterfront=14)   # above the source
    lacks = prof(restaurants=8, waterfront=0)   # below the source
    w = {"waterfront": 3}
    assert similarity_score(src, lacks, w) < similarity_score(src, lacks)
    assert similarity_score(src, more, w) > similarity_score(src, more)


def test_less_preference_rewards_deficit():
    # "Less" expresses preference for *less* of the category: the candidate
    # with far more parks than the source is penalized.
    a = prof(restaurants=10, parks=1)
    b = prof(restaurants=10, parks=50)
    assert similarity_score(a, b, weights={"parks": 0.25}) < similarity_score(a, b)


def test_neutral_weight_leaves_score_unchanged():
    a = prof(restaurants=10, parks=1)
    b = prof(restaurants=10, parks=50)
    assert similarity_score(a, b, weights={"parks": 1}) == pytest.approx(
        similarity_score(a, b)
    )


def test_nuisance_polarity_prefers_fewer():
    # "More" on a negative feature rewards candidates with FEWER nuisances.
    src = prof(restaurants=10, nuisance=10)
    clean = prof(restaurants=10, nuisance=2)
    dirty = prof(restaurants=10, nuisance=30)
    w = {"nuisance": 3}
    assert similarity_score(src, clean, w) > similarity_score(src, dirty, w)


def test_haversine_sanity():
    assert haversine_km(0, 0, 0, 0) == 0.0
    # Vancouver downtown → Langley ≈ 40 km
    d = haversine_km(49.2827, -123.1207, 49.1044, -122.6604)
    assert 30 < d < 50
