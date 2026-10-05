"""Tests for the persistent TTLCache."""

from app.osm import TTLCache


def test_get_set():
    c = TTLCache(ttl_seconds=60)
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}
    assert c.get("missing") is None


def test_expiry():
    c = TTLCache(ttl_seconds=0)
    c.set("k", "v")
    assert c.get("k") is None


def test_drop_where():
    c = TTLCache()
    c.set("empty", {"a": 0, "b": 0})
    c.set("full", {"a": 1, "b": 0})
    c.drop_where(lambda v: not any(v.values()))
    assert c.get("empty") is None
    assert c.get("full") == {"a": 1, "b": 0}


def test_persist_roundtrip(tmp_path):
    path = str(tmp_path / "cache.pkl")
    c1 = TTLCache(persist_path=path)
    c1.set("k", {"restaurants": 5})
    c1.save()
    c2 = TTLCache(persist_path=path)
    assert c2.get("k") == {"restaurants": 5}


def test_corrupt_cache_file_tolerated(tmp_path):
    path = tmp_path / "cache.pkl"
    path.write_bytes(b"not a pickle")
    c = TTLCache(persist_path=str(path))
    assert c.get("anything") is None
