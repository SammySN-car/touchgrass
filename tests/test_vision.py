import pytest
from app import vision


def test_load_impl_returns_scorer_with_score(monkeypatch):
    class FakeScorer:
        def score(self, quest_text, images):
            return [0.5] * len(images)

    monkeypatch.setattr(vision, "_load_scorer_impl", lambda: FakeScorer(), raising=False)
    vision._SCORER_CACHE.clear()
    scorer = vision.get_scorer()
    assert scorer.score("q", [b"a", b"b"]) == [0.5, 0.5]
    vision._SCORER_CACHE.clear()


def test_get_scorer_caches_singleton(monkeypatch):
    calls = {"n": 0}

    class FakeScorer:
        pass

    def fake_load():
        calls["n"] += 1
        return FakeScorer()

    monkeypatch.setattr(vision, "_load_scorer_impl", fake_load, raising=False)
    vision._SCORER_CACHE.clear()
    a = vision.get_scorer()
    b = vision.get_scorer()
    assert a is b
    assert calls["n"] == 1
    vision._SCORER_CACHE.clear()
