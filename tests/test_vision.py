from app import judge, vision


def test_model_id_is_canonical_hub_path():
    assert "/" in vision.MODEL_ID


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


def test_get_scorer_satisfies_gate_callable_contract(monkeypatch):
    """Seam regression: the production ClipScorer historically exposed only
    ``.score`` (no ``__call__``), so the gate's ``scorer(text, images)`` call
    raised TypeError, which judge's blanket except swallowed -> silent accept
    while CLIP still loaded. The fake below is shaped exactly like that scorer
    (only ``.score``, no ``__call__``); ``get_scorer()`` must still hand the
    gate something callable or the gate is inert."""
    class ScoreOnlyScorer:
        def score(self, quest_text, images):
            return [0.05] * len(images)

    monkeypatch.setattr(
        vision, "_load_scorer_impl", lambda: ScoreOnlyScorer(), raising=False
    )
    vision._SCORER_CACHE.clear()
    try:
        scorer = vision.get_scorer()
        passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], scorer)
        assert (passed, reason) == (False, "wrong photo")
        assert callable(scorer)
    finally:
        vision._SCORER_CACHE.clear()


def test_load_failure_is_memoized(monkeypatch):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("hub unreachable")

    monkeypatch.setattr(vision, "_load_scorer_impl", boom, raising=False)
    vision._SCORER_CACHE.clear()
    assert vision.get_scorer() is None
    assert vision.get_scorer() is None
    assert calls["n"] == 1  # second call hit the failure sentinel, not the loader
    vision._SCORER_CACHE.clear()
