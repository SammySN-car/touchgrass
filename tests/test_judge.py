from datetime import datetime

import app.judge as judge
import app.ollama_client as oc

META_OK = [
    {"captured_at": "2026-10-11T10:00:00", "size": 50000},
    {"captured_at": "2026-10-11T10:01:00", "size": 60000},
    {"captured_at": "2026-10-11T10:02:00", "size": 55000},
]
ISSUED_AT = "2026-10-11T09:00:00"
NOW = datetime(2026, 10, 11, 10, 30)


def test_rules_pass_happy_path():
    verdict = judge.rules_verdict(META_OK, 3, ISSUED_AT, 20, now=NOW)
    assert verdict["passed"] is True
    assert verdict["reason"] == "accepted"


def test_rules_fail_too_few():
    verdict = judge.rules_verdict(META_OK[:1], 3, ISSUED_AT, 20, now=NOW)
    assert verdict["passed"] is False
    assert "insufficient" in verdict["reason"]


def test_rules_fail_stale_capture():
    verdict = judge.rules_verdict(META_OK, 3, "2026-10-11T11:00:00", 20, now=NOW)
    assert verdict["passed"] is False
    assert "stale" in verdict["reason"]


def test_rules_fail_after_deadline():
    verdict = judge.rules_verdict(
        META_OK, 3, ISSUED_AT, 10, now=datetime(2026, 10, 11, 11, 0)
    )
    assert verdict["passed"] is False
    assert "deadline" in verdict["reason"]


def test_rules_fail_tiny_file():
    tiny = [{"captured_at": m["captured_at"], "size": 10} for m in META_OK]
    verdict = judge.rules_verdict(tiny, 3, ISSUED_AT, 20, now=NOW)
    assert verdict["passed"] is False
    assert "sincere" in verdict["reason"]


def test_narrate_mock_canned(monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    line, model_used = judge.narrate({"title": "Walk the block"}, META_OK, True)
    assert line in judge.CANNED_PASS
    assert model_used == "canned"


def test_narrate_model_contradiction_is_discarded(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setattr(
        judge, "chat_json", lambda *a, **k: {"verdict": "PENALTY", "line": "no"}
    )
    line, model_used = judge.narrate({"title": "Walk the block"}, META_OK, True)
    assert line in judge.CANNED_PASS
    assert model_used == "canned"


def test_narrate_exception_falls_back_to_canned(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(judge, "chat_json", boom)
    line, model_used = judge.narrate({"title": "Walk the block"}, META_OK, False)
    assert line in judge.CANNED_PENALTY
    assert model_used == "canned"


def test_narrate_model_agreement_uses_model_line(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setattr(
        judge,
        "chat_json",
        lambda *a, **k: {"verdict": "pass", "line": "  Outdoors logged.  "},
    )
    line, model_used = judge.narrate({"title": "Walk the block"}, META_OK, True)
    assert line == "Outdoors logged."
    assert model_used == oc.DEFAULT_MODEL


def _mk_scorer(scores):
    def scorer(quest_text, images):
        return list(scores)
    return scorer


def test_vision_gate_rejects_confident_low(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.05]))
    assert passed is False
    assert reason == "wrong photo"


def test_gate_passes_when_above_threshold(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.55]))
    assert passed is True
    assert reason == "accepted"


def test_gate_passes_when_ambiguous(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.30]))
    assert passed is True  # lenient: only confident-low rejects
    assert reason == "accepted"


def test_gate_uses_custom_threshold(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.setenv("GRASS_VISION_THRESHOLD", "0.60")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, _ = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.40]))
    assert passed is False  # 0.40 < 0.60 -> rejected under raised threshold


def test_gate_rejects_when_any_photo_low(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x", b"y"], _mk_scorer([0.80, 0.05]))
    assert passed is False
    assert reason == "wrong photo"
