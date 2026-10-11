# Vision Verification Tier (Tier 1.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-side CLIP vision gate that rejects confidently-wrong photos (e.g. a wall for a "pebble" quest) after Tier 1 rules pass and before Tier 2 narration, running on CPU so `gemma3:4b` keeps the GPU.

**Architecture:** New module `app/vision.py` owns CLIP loading + scoring. `judge.vision_gate()` is the pure decision function (injectable scorer for tests). `service.submit_evidence` calls the gate between `rules_verdict` and `narrate`. Any failure skips the gate and degrades to current v1 behavior. Rules stay authoritative; the gate only downgrades PASS->FAIL.

**Tech Stack:** Python 3.13, `transformers` CLIP (`openai/clip-vit-base-patch32`), Pillow (already present), pytest + monkeypatch (existing harness).

**Spec:** `docs/superpowers/specs/2026-10-11-vision-tier-design.md`

## Global Constraints

- Tier 1 (`judge.rules_verdict`) is authoritative and must never depend on any model. The new gate may only turn a rules-PASS into a FAIL; never a FAIL into a PASS; never touch narration.
- The gate runs on **CPU** (device `"cpu"`), never CUDA — `gemma3:4b` keeps the 4GB GPU.
- Lenient bias: a photo is rejected only when similarity is *confidently* LOW (below threshold). Ambiguous scores pass. Default threshold `0.20`, overridable via env `GRASS_VISION_THRESHOLD`.
- Env `GRASS_VISION` (`on`/`off`, default `on`) wholesale-disables the tier. Env `GRASS_MOCK=1` also skips it (consistent with existing mock mode).
- On ANY gate failure (import error, model load error, scoring error) the gate is skipped and behavior is identical to current v1. Log a warning; never raise out of the gate.
- Existing suite (61 tests) must stay green after every task.

## Review Focus

These are the failure modes the spec implies but that a naive implementation gets wrong. Each has a test pinned to the task that owns the code.

- **Gate rejects a legitimate photo (false positive).** The whole point of leniency: an ambiguous/mid-range score must PASS, not reject. Pinned in Task 3 (`test_gate_passes_when_above_threshold`, `test_gate_uses_custom_threshold`).
- **Gate runs when disabled or in mock mode.** `GRASS_VISION=off` or `GRASS_MOCK=1` must skip it entirely (no CLIP import/load). Pinned in Task 3 (`test_gate_disabled_by_env`, `test_gate_skipped_in_mock_mode`).
- **Gate raises and breaks submission.** A scorer that throws must be swallowed -> verdict proceeds on rules alone. Pinned in Task 3 (`test_gate_swallows_scorer_exception`).
- **Gate upgrades a FAIL to PASS** (violates the invariant). Rules-FAIL must short-circuit before the gate is ever consulted. Pinned in Task 4 (`test_gate_not_consulted_when_rules_fail`).
- **Model load latency / repeated reload.** CLIP must load once into a module-level singleton, not per submission. Pinned in Task 2 (`test_get_scorer_caches_singleton`).

---

### Task 1: Vision scoring module (CPU) with lazy singleton

**Files:**
- Create: `app/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `os.environ.get`, `PIL.Image`
- Produces: `app.vision.get_scorer() -> Callable[[str, list[bytes]], list[float]]` — a scorer taking (quest_text, jpeg_bytes_list) and returning a similarity float in [0,1] per image. Later tasks call `vision.get_scorer()` and treat it as the injectable seam.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.vision'` (or AttributeError for `_SCORER_CACHE`).

- [ ] **Step 3: Write minimal implementation**

Create `app/vision.py`:

```python
"""CLIP vision scoring: embeds quest text + photos, returns similarity.

Runs on CPU only so gemma3:4b keeps the GPU. Loaded once into a module-level
singleton; every failure path returns None so callers can skip the gate.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from PIL import Image
import io

log = logging.getLogger(__name__)

MODEL_ID = "openai/clip-vit-base-patch32"
_Scorer = Callable[[str, list[bytes]], list[float]]

_SCORER_CACHE: dict = {}


def _load_scorer_impl():
    """Load CLIP once. Returns an object with .score(quest_text, images)->list[float]."""
    from transformers import CLIPModel, CLIPProcessor

    model = CLIPModel.from_pretrained(MODEL_ID)
    processor = CLIPProcessor.from_pretrained(MODEL_ID)
    model.eval()

    class ClipScorer:
        def score(self, quest_text: str, images: list[bytes]) -> list[float]:
            import torch

            pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
            inputs = processor(
                text=[quest_text], images=pil, return_tensors="pt", padding=True
            )
            with torch.no_grad():
                out = model(**inputs)
            img = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
            txt = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
            sims = (img @ txt.T).squeeze(-1)
            return [float(s) for s in sims.tolist()]

    return ClipScorer()


def get_scorer():
    """Return the singleton scorer, or None if it cannot be loaded."""
    if "scorer" in _SCORER_CACHE:
        return _SCORER_CACHE["scorer"]
    try:
        scorer = _load_scorer_impl()
    except Exception as exc:  # noqa: BLE001 - any load failure skips the gate
        log.warning("vision scorer unavailable: %s", exc)
        return None
    _SCORER_CACHE["scorer"] = scorer
    return scorer
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vision.py -v`
Expected: PASS (note: `test_get_scorer_caches_singleton` must NOT trigger a real CLIP download — it monkeypatches `_load_scorer_impl`).

- [ ] **Step 5: Commit**

```bash
git add app/vision.py tests/test_vision.py
git commit -m "feat: CLIP vision scoring module with lazy CPU singleton"
```

---

### Task 2: Pure decision function `judge.vision_gate`

**Files:**
- Modify: `app/judge.py` (add `vision_gate` + `vision_enabled`)
- Test: `tests/test_judge.py` (append)

**Interfaces:**
- Consumes: `os.environ.get`, injected `scorer` callable with signature `(quest_text, images) -> list[float]`
- Produces: `judge.vision_enabled() -> bool`; `judge.vision_gate(quest: dict, image_bytes: list[bytes], scorer) -> tuple[bool, str]` returning `(passed, reason)` where `reason` is `"accepted"` or `"wrong photo"`. Task 5 calls this with the real scorer.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_judge.py`:

```python
import os  # (top of file already imports datetime; add env helpers here)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_judge.py -k vision_gate -v`
Expected: FAIL with `AttributeError: module 'app.judge' has no attribute 'vision_gate'`.

- [ ] **Step 3: Write minimal implementation**

Add to `app/judge.py` (after `_as_aware`, before `rules_verdict`):

```python
def vision_enabled() -> bool:
    """True unless GRASS_VISION=off or GRASS_MOCK=1 (mock skips the gate)."""
    if os.environ.get("GRASS_MOCK") == "1":
        return False
    return os.environ.get("GRASS_VISION", "on") != "off"


def _vision_threshold() -> float:
    raw = os.environ.get("GRASS_VISION_THRESHOLD", "0.20")
    try:
        return float(raw)
    except ValueError:
        return 0.20


def vision_gate(quest: dict, image_bytes: list[bytes], scorer) -> tuple[bool, str]:
    """Tier 1.5: reject only when similarity is confidently LOW.

    Pure decision function; `scorer(quest_text, images) -> list[float]` is
    injected so tests never load CLIP. Returns (passed, reason).
    """
    if not vision_enabled():
        return (True, "accepted")
    if not image_bytes or scorer is None:
        return (True, "accepted")
    text = quest.get("objective") or quest.get("title") or ""
    threshold = _vision_threshold()
    try:
        scores = scorer(text, image_bytes)
    except Exception as exc:  # noqa: BLE001 - gate must never raise
        log.warning("vision scoring failed, skipping gate: %s", exc)
        return (True, "accepted")
    if any(s < threshold for s in scores):
        return (False, "wrong photo")
    return (True, "accepted")
```

Also add `import os` and `import logging` + `log = logging.getLogger(__name__)` at the top of `app/judge.py` if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_judge.py -v`
Expected: PASS, and the pre-existing judge tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app/judge.py tests/test_judge.py
git commit -m "feat: vision_gate pure decision function with lenient threshold"
```

---

### Task 3: Gate enable/disable + failure-safety tests

**Files:**
- Modify: `tests/test_judge.py` (append) — no production change expected beyond Task 2
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `judge.vision_enabled`, `judge.vision_gate` (Task 2)
- Produces: verified behavior for `GRASS_VISION=off`, `GRASS_MOCK=1`, and scorer exceptions — the safety guarantees Task 5 relies on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_judge.py`:

```python
def test_gate_disabled_by_env(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "off")
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.01]))
    assert passed is True
    assert reason == "accepted"


def test_gate_skipped_in_mock_mode(monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    monkeypatch.setenv("GRASS_VISION", "on")
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], _mk_scorer([0.01]))
    assert passed is True
    assert reason == "accepted"


def test_gate_swallows_scorer_exception(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)

    def boom(quest_text, images):
        raise RuntimeError("cuda exploded")

    passed, reason = judge.vision_gate({"objective": "a pebble"}, [b"x"], boom)
    assert passed is True
    assert reason == "accepted"


def test_gate_passes_when_no_images(monkeypatch):
    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    passed, reason = judge.vision_gate({"objective": "a pebble"}, [], _mk_scorer([]))
    assert passed is True
    assert reason == "accepted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_judge.py -k "disabled or mock or swallows or no_images" -v`
Expected: FAIL — these behaviors are not yet guaranteed (the env/mock paths may already pass by luck; the exception test should fail if Task 2's try/except is missing).

- [ ] **Step 3: Implement/confirm**

Task 2's `vision_enabled()` and the `try/except` in `vision_gate` already provide all four. If any test fails, ensure `vision_enabled()` checks `GRASS_MOCK` first and the scorer call is wrapped in `try/except Exception`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_judge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_judge.py
git commit -m "test: vision gate disable/mock/exception safety guarantees"
```

---

### Task 4: Wire the gate into `service.submit_evidence`

**Files:**
- Modify: `app/service.py` (between `rules_verdict` and `narrate`, ~line 124-128)
- Test: `tests/test_service.py` (append)

**Interfaces:**
- Consumes: `judge.vision_gate(quest, image_bytes, scorer)`, `vision.get_scorer()`, `judge.vision_enabled()`
- Produces: submission payloads where a confidently-wrong photo yields `passed=False`; rules-FAIL submissions never consult the gate.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service.py`:

```python
def test_gate_downgrades_rules_pass_to_fail(conn, monkeypatch):
    from app import vision

    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setenv("GRASS_VISION", "on")
    # Force rules PASS: valid fresh jpeg, target_count=1
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)

    monkeypatch.setattr(service.judge, "vision_enabled", lambda: True)
    monkeypatch.setattr(vision, "get_scorer", lambda: (lambda text, imgs: [0.01]))
    monkeypatch.setattr(service.judge, "narrate", lambda q, m, p: ("line", "fake-model"))

    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is False
    row = conn.execute("SELECT passed FROM verdicts WHERE quest_id = ?", (quest_id,)).fetchone()
    assert row["passed"] == 0


def test_gate_not_consulted_when_rules_fail(conn, monkeypatch):
    from app import vision

    monkeypatch.setenv("GRASS_VISION", "on")
    day = "2026-11-05"
    # target_count=3 but only 1 photo -> rules FAIL (insufficient evidence)
    quest_id = insert_quest(conn, day, target_count=3)

    called = {"n": 0}

    def spy(text, imgs):
        called["n"] += 1
        return [0.99]

    monkeypatch.setattr(vision, "get_scorer", lambda: spy)

    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is False
    assert called["n"] == 0  # gate never reached
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service.py -k "gate" -v`
Expected: FAIL — `test_gate_downgrades...` fails because the gate isn't wired (payload stays PASS).

- [ ] **Step 3: Write minimal implementation**

In `app/service.py`, after `passed = bool(verdict["passed"])` and before `judge.narrate(...)`, insert:

```python
        if passed and judge.vision_enabled():
            from app import vision
            scorer = vision.get_scorer()
            if scorer is not None:
                jpgs = [p.read_bytes() for p in sorted(EVIDENCE_DIR.glob(f"{quest_id:04d}_*.jpg"))]
                v_passed, v_reason = judge.vision_gate(quest, jpgs, scorer)
                if not v_passed:
                    passed = False
                    verdict = {"passed": False, "reason": v_reason}
```

Note: `image_bytes` are the just-written JPEGs read back from `EVIDENCE_DIR` (keeps the gate decoupled from the in-loop `jpg` variable and matches how evidence is persisted).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_service.py -v`
Expected: PASS, and the full suite: `python -m pytest -q` -> 61+ green.

- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_service.py
git commit -m "feat: wire vision gate between rules verdict and narration"
```

---

### Task 5: End-to-end + full suite regression

**Files:**
- Test: `tests/test_service.py` (append)
- Verify: whole suite

**Interfaces:**
- Consumes: everything above
- Produces:** confidence that existing pass/fail/double-submit flows are unchanged with the gate defaulted on-but-inert (no scorer loaded in tests).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service.py`:

```python
def test_existing_pass_flow_unchanged_with_gate_default(conn):
    # GRASS_VISION defaults to on, but no scorer is loaded in tests -> gate inert.
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is True


def test_gate_survives_scorer_load_failure(conn, monkeypatch):
    from app import vision

    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setattr(vision, "get_scorer", lambda: None)  # load failed
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is True  # degraded to v1 behavior
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_service.py -k "unchanged or load_failure" -v`
Expected: These should PASS already if wiring is correct; if either fails, the gate is raising or over-rejecting — fix Task 4 wiring (guard `scorer is None`).

- [ ] **Step 3: Run the entire suite**

Run: `python -m pytest -q`
Expected: All tests pass (61 existing + new vision/gate tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_service.py
git commit -m "test: end-to-end vision gate regression + scorer-load fallback"
```

---

## Self-Review notes (for executor)

- Spec coverage: Goal/Non-goals -> Task 4 wiring + Task 5 regression; Placement/Invariant -> Task 4 `if passed and ...` (only consulted after rules PASS); Model/runtime CPU -> Task 1 `device` not set to CUDA (CPU default); Scoring/lenient threshold -> Task 2 threshold logic + tests; Failure/fallback -> Task 3; Data flow -> Task 4; Env vars -> Tasks 2/3; Testing strategy -> all tasks.
- Type consistency: `vision_gate(quest, image_bytes, scorer) -> tuple[bool, str]` is used identically in Tasks 2, 4, 5. `get_scorer() -> scorer|None` consistent in Tasks 1, 4, 5.
- One real risk for the executor: Task 4 reads JPEGs back from disk (`EVIDENCE_DIR.glob`) rather than reusing the in-memory `jpg`. This is deliberate (decoupling + matches persisted evidence) but must use the same `{quest_id:04d}_*.jpg` pattern written in Task 4's Step 3.