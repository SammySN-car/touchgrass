# SYSTEM://GRASS v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a local-first outdoor quest game (daily quest -> photo evidence -> rules verdict + Gemma narration -> XP/rank/penalty) as a mobile web app served from a laptop, with a DEMO VIDEO and DEV post before the Hacktoberfest Week 1 deadline (2026-10-11 23:59 PDT = 2026-10-12 12:29 IST).

**Architecture:** Python FastAPI backend + SQLite single-player state. Quests come from a hand-written template pool; Gemma (Ollama, local) supplies one flavor line and the verdict narration as JSON; rules tier is authoritative for PASS/FAIL; canned fallback banks keep the game alive when Ollama is down (also serves as demo mode via GRASS_MOCK=1). Frontend is a 4-screen mobile-first vanilla JS app with no build tooling, served by FastAPI on the LAN.

**Tech Stack:** Python 3.13, FastAPI 0.128, uvicorn 0.40, Pillow 11, pytest 9, requests, python-multipart, SQLite, Ollama (`gemma3:4b` primary, `llama3.2` fallback).

**Spec:** `docs/design.md` (frozen v1 design). This plan argues from that spec.

## Global Constraints

- No cloud, no accounts, no API keys, no push notifications. LAN only.
- Model: env `GRASS_MODEL` default `gemma3:4b`; automatic fallback chain `[model, llama3.2, qwen2.5:7b]`.
- `GRASS_MOCK=1` forces canned narration (tests + demo resilience). Tests NEVER require a live Ollama.
- Rules tier decides PASS/FAIL. The model may ONLY narrate; a model verdict contradicting rules is discarded.
- Photos: JPEG only, client-compressed target <=200KB, server re-compresses max side 1280px.
- Ranks F..S thresholds 0/100/250/500/850/1300/2000; FAIL_XP_PENALTY=30; streak bonus 5/day cap 25; perfect +20.
- No Solo Leveling IP anywhere in code, copy, README, or post. Disclaimer line stays in README.
- Commit after every task. Exact-path `git add` only.
- Deadline: target DEMO VIDEO by 10:30 IST, DEV post submitted by 11:30 IST.

## Review Focus (failure modes tests must pin)

1. **Ollama unreachable or times out mid-verdict** -> submit still returns a canned PASS/FAIL verdict; game continues. Owned by Task 5 (judge) and Task 7 (service).
2. **Model narration contradicts the rules verdict** (says PENALTY on passing evidence) -> rules verdict wins; narration swapped to canned. Owned by Task 5.
3. **Same-day re-open double-issues a quest or double-applies state** -> quest issuance idempotent per day; evidence after verdict rejected politely. Owned by Task 4 + Task 7.
4. **Missed-yesterday demotion mis-fires** (user passed yesterday -> no demotion; user has no quest record yesterday -> no demotion; quest issued yesterday with no verdict -> demote). Owned by Task 7.
5. **Hostile uploads** (non-image bytes, absurd file size, captured_at far in the past) -> 4xx with system-voice error; never a 500. Owned by Task 6 + Task 8.

---

### Task 1: State machine (ALREADY IMPLEMENTED - verify only)

**Files:**
- Exists: `app/state.py`
- Exists: `tests/test_state.py`

**Interfaces (later tasks rely on these exact names):**
- `PlayerState(xp:int, rank_index:int, streak:int, last_pass_day:date|None)` frozen dataclass; `.rank -> str`
- `apply_pass(state, base_xp:int, day:date, perfect:bool=False) -> tuple[PlayerState, list[Event]]`
- `apply_fail(state) -> tuple[PlayerState, list[Event]]`
- `apply_ignore(state) -> tuple[PlayerState, list[Event]]`
- `Event(kind:str, detail:str)` kinds: XP_GAINED, XP_LOST, RANK_UP, RANK_DOWN, STREAK_BROKEN
- `RANKS = ("F","E","D","C","B","A","S")`, `THRESHOLDS = (0,100,250,500,850,1300,2000)`

- [x] Step 1: Implemented in this session (14 tests, red->green history preserved).
- [ ] Step 2: Verify green.

Run: `python -m pytest tests/test_state.py -q`
Expected: `14 passed`

- [ ] Step 3: Commit if uncommitted.

```bash
git add app/state.py tests/test_state.py app/__init__.py tests/__init__.py
git commit -m "feat: state machine for ranks, xp, streaks, penalties"
```

---

### Task 2: Database layer

**Files:**
- Create: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `connect() -> sqlite3.Connection` (Row factory), `init_db() -> None`, `DB_PATH: Path`. Tables: `player(id=1,xp,rank_index,streak,last_pass_day)`, `quests(id,day UNIQUE,template_id,title,objective,target_count,deadline_hour,xp_reward,difficulty,issued_at,flavor)`, `evidence(id,quest_id,path,captured_at,uploaded_at)`, `verdicts(id,quest_id,passed,xp_delta,rank_before,rank_after,streak_after,line,flavor,model_used,events_json,created_at)`, `template_usage(template_id PK,times_used)`.

- [ ] **Step 1: Failing test**

```python
# tests/test_db.py
import sqlite3

from app import db


def test_init_db_creates_tables_and_player(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"player", "quests", "evidence", "verdicts",
                "template_usage"} <= tables
        row = conn.execute("SELECT * FROM player WHERE id=1").fetchone()
        assert row["xp"] == 0 and row["rank_index"] == 0


def test_connect_returns_row_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    conn = db.connect()
    row = conn.execute("SELECT 1 AS x").fetchone()
    assert row["x"] == 1
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement**

```python
# app/db.py
"""SQLite storage. Single-player, local-only."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "grass.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    xp INTEGER NOT NULL DEFAULT 0,
    rank_index INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    last_pass_day TEXT
);
CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL UNIQUE,
    template_id TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    deadline_hour INTEGER NOT NULL,
    xp_reward INTEGER NOT NULL,
    difficulty TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    flavor TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    quest_id INTEGER NOT NULL REFERENCES quests(id),
    path TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY,
    quest_id INTEGER NOT NULL REFERENCES quests(id),
    passed INTEGER NOT NULL,
    xp_delta INTEGER NOT NULL,
    rank_before TEXT NOT NULL,
    rank_after TEXT NOT NULL,
    streak_after INTEGER NOT NULL,
    line TEXT NOT NULL,
    flavor TEXT NOT NULL DEFAULT '',
    model_used TEXT NOT NULL,
    events_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS template_usage (
    template_id TEXT PRIMARY KEY,
    times_used INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO player (id) VALUES (1)")
```

- [ ] **Step 4: Verify PASS**

Run: `python -m pytest tests/test_db.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: sqlite schema and connection helpers"
```

---

### Task 3: Ollama JSON client

**Files:**
- Create: `app/ollama_client.py`
- Test: `tests/test_ollama.py`

**Interfaces:**
- Consumes: env `OLLAMA_URL` (default `http://127.0.0.1:11434`), `GRASS_MODEL` (default `gemma3:4b`), `GRASS_MOCK`.
- Produces: `mock_mode() -> bool`; `chat_json(model:str, system:str, user:str, timeout:int=90) -> dict` (tries model then fallbacks, raises RuntimeError if all fail); `reachable() -> bool`; `DEFAULT_MODEL: str`; `CANDIDATE_MODELS: list[str]`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_ollama.py
import pytest

from app import ollama_client


def test_mock_mode_env(monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    assert ollama_client.mock_mode() is True
    monkeypatch.delenv("GRASS_MOCK")
    assert ollama_client.mock_mode() is False


def test_chat_json_falls_back_to_next_model(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, ok, payload=None):
            self._ok, self._payload = ok, payload
        def raise_for_status(self):
            if not self._ok:
                raise RuntimeError("boom")
        def json(self):
            return {"message": {"content": self._payload}}

    def fake_post(url, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "gemma3:4b":
            return FakeResp(False)
        return FakeResp(True, '{"flavor": "ok"}')

    monkeypatch.setattr(ollama_client.requests, "post", fake_post)
    out = ollama_client.chat_json("gemma3:4b", "sys", "usr")
    assert out == {"flavor": "ok"}
    assert calls[0] == "gemma3:4b" and "llama3.2" in calls


def test_chat_json_all_fail_raises(monkeypatch):
    def fake_post(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ollama_client.requests, "post", fake_post)
    with pytest.raises(RuntimeError):
        ollama_client.chat_json("gemma3:4b", "s", "u")


def test_reachable_false_when_down(monkeypatch):
    def fake_get(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(ollama_client.requests, "get", fake_get)
    assert ollama_client.reachable() is False
```

- [ ] **Step 2: Verify FAIL**

Run: `python -m pytest tests/test_ollama.py -q`
Expected: FAIL import

- [ ] **Step 3: Implement**

```python
# app/ollama_client.py
"""Minimal Ollama JSON-chat client. GRASS_MOCK=1 never reaches the network."""
from __future__ import annotations

import json
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("GRASS_MODEL", "gemma3:4b")
CANDIDATE_MODELS = [DEFAULT_MODEL, "llama3.2", "qwen2.5:7b"]


def mock_mode() -> bool:
    return os.environ.get("GRASS_MOCK") == "1"


def chat_json(model: str, system: str, user: str, timeout: int = 90) -> dict:
    """One JSON object. Tries candidate models in order; raises if all fail."""
    errors = []
    chain = [model] + [c for c in CANDIDATE_MODELS if c != model]
    for m in chain:
        try:
            r = requests.post(
                OLLAMA_URL + "/api/chat",
                json={
                    "model": m,
                    "stream": False,
                    "keep_alive": -1,
                    "options": {"temperature": 0.75},
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=timeout,
            )
            r.raise_for_status()
            return json.loads(r.json()["message"]["content"])
        except Exception as e:  # noqa: BLE001
            errors.append(m + ": " + str(e)[:120])
    raise RuntimeError("all models failed: " + " | ".join(errors))


def reachable() -> bool:
    try:
        requests.get(OLLAMA_URL + "/api/tags", timeout=2)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Verify PASS** -> `python -m pytest tests/test_ollama.py -q` -> `4 passed`
- [ ] **Step 5: Commit**

```bash
git add app/ollama_client.py tests/test_ollama.py
git commit -m "feat: ollama json chat client with model fallback"
```

---

### Task 4: Quest engine (templates + issuance)

**Files:**
- Create: `app/quests.py`
- Test: `tests/test_quests.py`

**Interfaces:**
- Consumes: `db.connect`, `ollama_client.chat_json`, `ollama_client.mock_mode`.
- Produces: `TEMPLATES: list[tuple]` (7-tuples: id, difficulty, xp, target_count, deadline_hour, title, objective); `CANNED_FLAVOR: list[str]`; `pick_template(conn) -> tuple` (least-used bias); `gemma_flavor(title, objective) -> tuple[str,str]` (line, model_used; canned on any failure); `issue_quest_for_day(conn, day:str) -> dict|None` (idempotent per day).

- [ ] **Step 1: Failing tests**

```python
# tests/test_quests.py
from app import db, quests


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    return db.connect()


def test_issue_is_idempotent_per_day(tmp_path, monkeypatch):
    conn = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("GRASS_MOCK", "1")
    q1 = quests.issue_quest_for_day(conn, "2026-10-11")
    q2 = quests.issue_quest_for_day(conn, "2026-10-11")
    assert q1["id"] == q2["id"]
    n = conn.execute("SELECT COUNT(*) c FROM quests").fetchone()["c"]
    assert n == 1


def test_anti_repeat_prefers_unused(tmp_path, monkeypatch):
    conn = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("GRASS_MOCK", "1")
    seen = []
    for i in range(len(quests.TEMPLATES)):
        q = quests.issue_quest_for_day(conn, f"2026-11-{i+1:02d}")
        seen.append(q["template_id"])
    assert len(set(seen)) == len(quests.TEMPLATES)  # all 14 distinct first pass


def test_canned_flavor_in_mock(tmp_path, monkeypatch):
    conn = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("GRASS_MOCK", "1")
    q = quests.issue_quest_for_day(conn, "2026-10-11")
    assert q["flavor"] in quests.CANNED_FLAVOR
```

- [ ] **Step 2: Verify FAIL** -> `python -m pytest tests/test_quests.py -q` -> import error
- [ ] **Step 3: Implement** `app/quests.py` with the 14-template pool below (verbatim from this plan's design), `pick_template`, `gemma_flavor` (mock_mode -> canned; else chat_json with system "You are THE SYSTEM: cold, bureaucratic outdoor quest authority. Dry humor. Never friendly, never obscene. Output JSON only." and user asking for one sentence <=20 words as JSON {"flavor": string}; any exception -> canned), `issue_quest_for_day` (existing-day SELECT first; insert + template_usage upsert + commit).

Templates (verbatim):

```python
TEMPLATES = [
    ("living",   "D", 40, 3, 20, "LIVING THINGS",
     "Find 3 living things outdoors. Photograph each. Humans do not count."),
    ("sky",      "D", 40, 1, 18, "SKY CHECK",
     "Photograph the sky from outside. Windows are not outside."),
    ("textures", "D", 40, 2, 20, "TEXTURES",
     "Photograph 2 outdoor surfaces you have never touched. Touch them first."),
    ("lane",     "C", 55, 1, 19, "END OF THE LANE",
     "Walk to the end of your street. Photograph something you never noticed."),
    ("colors",   "C", 55, 3, 20, "COLOR HUNT",
     "Photograph 3 different colors found outdoors. No screens. No printed ink."),
    ("ground",   "C", 55, 1, 20, "GROUND LEVEL",
     "Photograph something at ground level, from outside. Crouch. The System waits."),
    ("shadows",  "C", 60, 2, 21, "SHADOWS",
     "Photograph 2 shadows. They only exist outside."),
    ("furthest", "B", 70, 1, 20, "FURTHEST POINT",
     "Go to the farthest point you can reach from your door. Photograph what you see."),
    ("small",    "B", 70, 3, 21, "SMALL WORLDS",
     "Photograph 3 small things: a pebble, a leaf, a crack. Detail is a skill."),
    ("altered",  "B", 60, 2, 20, "ALTERED BY TIME",
     "Photograph 2 things changed by weather or time: rust, cracks, wilt, bloom."),
    ("night",    "D", 45, 1, 22, "NIGHT AIR",
     "After dark, step outside. Photograph one thing lit by moon or streetlight."),
    ("doorstep", "D", 40, 1, 20, "DOORSTEP",
     "Stand outside your own door. Photograph your home from the outside."),
    ("water",    "C", 55, 1, 20, "WATER",
     "Find water that did not come from a tap. Photograph it."),
    ("still",    "B", 65, 1, 21, "STILLNESS",
     "Stand outside for 60 seconds without recording anything. Then photograph the spot."),
]

CANNED_FLAVOR = [
    "The System has observed your inactivity with growing interest.",
    "A quest has been allocated to you. Gratitude is optional; compliance is not.",
    "Today's assignment reflects the System's low but non-zero expectations.",
    "You have been selected for outdoor activity. This is not an honor.",
    "The System noticed you are indoors again. How predictable.",
    "Consider this quest a loan. Repayment is measured in evidence.",
    "Your rank reflects recent enthusiasm. Adjust accordingly.",
    "The System planned this around your habits. It has studied them.",
]
```

NOTE: template tuple order in TEMPLATES is `(id, difficulty, xp, target_count, deadline_hour, title, objective)`; `pick_template` bias: count usage from `template_usage`, choose among minimum-usage pool at random.
NOTE: `gemma_flavor` must catch ALL exceptions and fall back to canned (Task 5 Review Focus #1's quest-side twin).

- [ ] **Step 4: Verify PASS** -> `python -m pytest tests/test_quests.py -q` -> `3 passed`
- [ ] **Step 5: Commit**

```bash
git add app/quests.py tests/test_quests.py
git commit -m "feat: quest templates, anti-repeat issuance, flavor fallback"
```

---

### Task 5: Judge (rules authoritative + narration)

**Files:**
- Create: `app/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `ollama_client.chat_json/mock_mode`, canned banks (local).
- Produces:
  - `CANNED_PASS: list[str]`, `CANNED_PENALTY: list[str]`
  - `rules_verdict(evidence_meta:list[dict], target_count:int, issued_at:str, deadline_hour:int, now:datetime|None=None) -> dict` -> `{"passed": bool, "reason": str}` where meta dicts are `{"captured_at": isostr, "size": int}`; fails if `len(meta) < target_count` ("insufficient evidence"); fails if ANY captured_at < issued_at ("stale capture"); fails if now hour > deadline_hour ("deadline exceeded"); fails if any size < 3000 bytes ("evidence too small to be sincere").
  - `narrate(quest:dict, meta:list[dict], passed:bool) -> tuple[str,str]` -> `(line, model_used)`; prompt asks for JSON `{"verdict":"PASS|PENALTY","line":string}` (<=30 words, system voice); if mock_mode, model failure, OR model verdict != rules verdict -> canned bank line and model_used="canned" (Review Focus #2).

- [ ] **Step 1: Failing tests**

```python
# tests/test_judge.py
from datetime import datetime

from app import judge


META_OK = [
    {"captured_at": "2026-10-11T10:00:00", "size": 50_000},
    {"captured_at": "2026-10-11T10:01:00", "size": 60_000},
    {"captured_at": "2026-10-11T10:02:00", "size": 55_000},
]


def test_rules_pass_happy_path():
    out = judge.rules_verdict(META_OK, 3, "2026-11-01T09:00:00", 20,
                              now=datetime(2026, 11, 1, 10, 30))
    assert out["passed"] is True


def test_rules_fail_too_few():
    out = judge.rules_verdict(META_OK[:1], 3, "2026-11-01T09:00:00", 20,
                              now=datetime(2026, 11, 1, 10, 30))
    assert out["passed"] is False and "insufficient" in out["reason"]


def test_rules_fail_stale_capture():
    out = judge.rules_verdict(META_OK, 3, "2026-11-01T12:00:00", 20,
                              now=datetime(2026, 11, 1, 13, 0))
    assert out["passed"] is False and "stale" in out["reason"]


def test_rules_fail_after_deadline():
    out = judge.rules_verdict(META_OK, 3, "2026-11-01T09:00:00", 10,
                              now=datetime(2026, 11, 1, 11, 0))
    assert out["passed"] is False and "deadline" in out["reason"]


def test_rules_fail_tiny_file():
    meta = [dict(m, size=10) for m in META_OK]
    out = judge.rules_verdict(meta, 3, "2026-11-01T09:00:00", 20,
                              now=datetime(2026, 11, 1, 10, 0))
    assert out["passed"] is False and "sincere" in out["reason"]


def test_narrate_mock_canned(monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    line, used = judge.narrate({"title": "T"}, META_OK, True)
    assert used == "canned" and line in judge.CANNED_PASS


def test_narrate_model_contradiction_is_discarded(monkeypatch):
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setattr(judge, "chat_json",
                        lambda *a, **k: {"verdict": "PENALTY", "line": "no"})
    monkeypatch.setattr("app.ollama_client.mock_mode", lambda: False)
    line, used = judge.narrate({"title": "T"}, META_OK, True)
    assert used == "canned" and line in judge.CANNED_PASS
```

- [ ] **Step 2: Verify FAIL** -> import error
- [ ] **Step 3: Implement** `app/judge.py` (banks + rules_verdict + narrate; narrate imports chat_json and mock_mode from app.ollama_client; contradiction or exception -> canned).
- [ ] **Step 4: Verify PASS** -> `python -m pytest tests/test_judge.py -q` -> `7 passed`
- [ ] **Step 5: Commit**

```bash
git add app/judge.py tests/test_judge.py
git commit -m "feat: rules-authoritative judge with narrated verdicts"
```

---

### Task 6: Image compression helper

**Files:**
- Create: `app/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: Pillow.
- Produces: `compress_jpeg(data: bytes, max_side: int = 1280, target_kb: int = 200) -> bytes` (RGB convert, downscale, iterative quality 80->40 until <= target_kb); raises `ValueError` on undecodable bytes.

- [ ] **Step 1: Failing test**

```python
# tests/test_images.py
import io

import pytest
from PIL import Image

from app import images


def make_png(size=(2000, 1500), color=(10, 200, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_compress_downscales_and_shrinks():
    out = images.compress_jpeg(make_png())
    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 1280
    assert len(out) <= 200 * 1024


def test_compress_rejects_garbage():
    with pytest.raises(ValueError):
        images.compress_jpeg(b"not an image at all")
```

- [ ] **Step 2: Verify FAIL** -> import error
- [ ] **Step 3: Implement**

```python
# app/images.py
"""Server-side evidence normalization: JPEG, bounded size."""
from __future__ import annotations

import io

from PIL import Image


def compress_jpeg(data: bytes, max_side: int = 1280, target_kb: int = 200) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ValueError("undecodable image") from e
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    quality = 80
    while quality >= 40:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= target_kb * 1024:
            return buf.getvalue()
        quality -= 10
    return buf.getvalue()
```

- [ ] **Step 4: Verify PASS** -> `2 passed`
- [ ] **Step 5: Commit**

```bash
git add app/images.py tests/test_images.py
git commit -m "feat: evidence image compression helper"
```

---

### Task 7: Game service (glue + daily ignore check)

**Files:**
- Create: `app/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `db`, `state`, `quests`, `judge`, `images`.
- Produces (exact signatures):
  - `load_state(conn) -> state.PlayerState` (from player row; last_pass_day parsed from ISO date or None)
  - `save_state(conn, st) -> None`
  - `get_or_issue_today(conn, day:str) -> dict` (runs missed-yesterday check BEFORE issuing: if a quest exists for yesterday, no verdict exists for it, AND today has no quest yet -> apply_ignore + record synthetic verdict row line="No evidence. The System noted this.")
  - `submit_evidence(conn, quest_id:int, files:list[bytes], captured_ats:list[str]) -> dict` -> verdict payload `{"passed":bool,"line":str,"model_used":str,"xp_delta":int,"rank_before":str,"rank_after":str,"streak":int,"events":[{"kind","detail"}],"flavor":str}`; rejects (raises `ServiceError(msg)`) if quest already has a verdict; compresses each file to `evidence/NNN.jpg`; stores meta; rules_verdict; narrate; apply_pass/apply_fail; insert verdicts row.
  - `class ServiceError(Exception)`
- Produces (API layer uses): `get_log(conn) -> list[dict]` (verdicts joined with quest titles, newest first, cap 50).

- [ ] **Step 1: Failing tests** (tmp DB via monkeypatch; GRASS_MOCK=1; captured_ats after issued_at; size real compressed jpeg from Task 6 helper)

Cover: pass flow (3 photos -> xp increases, verdict row written); fail flow (1 photo -> -30 or 0 floor, streak reset); double-submit raises ServiceError; yesterday-ignored demotes; yesterday-passed does NOT demote; no-yesterday-record does NOT demote. (Write these as 6 tests in `tests/test_service.py` using helpers `setup()` like Task 4.)

- [ ] **Step 2: Verify FAIL** -> import error
- [ ] **Step 3: Implement** `app/service.py` per interfaces. Ignore-check: look up quest for `(today - 1 day)`; if found and no verdict for it -> apply_ignore, insert verdicts row (passed=0, xp_delta=0, model_used="rules").
- [ ] **Step 4: Verify PASS** -> `python -m pytest tests/test_service.py -q` -> `6 passed`
- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_service.py
git commit -m "feat: game service - submission, verdicts, missed-quest demotion"
```

---

### Task 8: FastAPI app + routes

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`
- Create runtime dirs at startup: `evidence/` (mkdir exist_ok).

**Interfaces:**
- Consumes: everything above.
- Produces routes:
  - `GET /api/state` -> `{"player":{xp,rank,streak},"quest":{...}|null,"verdict":{...}|null,"server_time":iso}` (issues today's quest on first call)
  - `POST /api/evidence` multipart: `files` (list), `quest_id` (int), `captured_at` (single ISO applied to all, v1) -> verdict payload; ServiceError -> 409; ValueError -> 422
  - `GET /api/log` -> `{"entries":[...]}`
  - `GET /api/health` -> `{"ollama": bool, "mock": bool, "model": str}`
  - `GET /` -> `static/index.html`; `/static/*` StaticFiles
- `init_db()` + `Path("evidence").mkdir(exist_ok=True)` in FastAPI startup event.

- [ ] **Step 1: Failing tests** (`httpx` must be installed: `pip install httpx` as part of this task). TestClient flows: health ok; state issues quest once; POST evidence with tiny valid JPEG (PIL-generated) returns verdict JSON with passed true when meta count matches; second POST same quest -> 409; log returns list.
- [ ] **Step 2: Verify FAIL**
- [ ] **Step 3: Implement** `app/main.py` (FastAPI, File uploads `list[UploadFile]`, monkeypatched db path in tests via env `GRASS_DB` -> if set, db.DB_PATH points there; set this env in tests).
  NOTE: add env override in `app/db.py`: `DB_PATH = Path(os.environ.get("GRASS_DB", default))` (small Modify step; update Task 2 test accordingly if needed).
- [ ] **Step 4: Verify PASS** -> `python -m pytest -q` -> ALL green (all tasks)
- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py app/db.py
git commit -m "feat: fastapi routes and static serving"
```

---

### Task 9: Frontend (4 screens, mobile-first)

**Files:**
- Create: `static/index.html`, `static/style.css`, `static/app.js`

**Interfaces:**
- Consumes: `/api/state`, `/api/evidence`, `/api/log`, `/api/health`.
- Produces: UI screens (single page, JS-toggled): Quest Window (rank/XP/streak bar + today's quest + flavor + capture button + status line); Evidence Capture (file input `capture="environment"` multiple + submit); Verdict Window (PASS/PENALTY banner + narration line + XP delta + rank-up events); Log (timeline of past verdicts). System-window aesthetic: black bg (#05060a), amber #ffb000 primary text, green #39ff14 accents, 1px bordered "windows", monospace. No frameworks. Poll `/api/state` on load + after actions only (no timers).

- [ ] Step 1: Implement the three files (vanilla JS; show "JUDGING..." state while POST in flight; on 409 show existing verdict).
- [ ] Step 2: Manual check: `uvicorn app.main:app --host 0.0.0.0 --port 8000` with `GRASS_MOCK=1`, open on desktop + phone on same Wi-Fi, run one full loop.
- [ ] Step 3: Commit.

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat: mobile-first system-window frontend"
```

---

### Task 10: E2E hardening, demo video, DEV post, submission

**Files:**
- Modify: `README.md` (screenshots section + exact run steps), `docs/design.md` (tick shipped list if any drift)
- Create: `docs/demo-script.md` (numbered on-screen steps used for the video)
- Create: DEV post draft (local file `docs/devpost-draft.md`; final submission via dev.to UI by the human)

**Steps:**
- [ ] 1. Run FULL suite: `python -m pytest -q` -> all green. Record count.
- [ ] 2. Real-Ollama smoke (no mock): start server, one quest flavor + one verdict narrated by gemma3:4b (or fallback); note latency in demo notes.
- [ ] 3. Record demo video (OBS or phone screen-record): boot server -> quest window -> outdoor capture (field shot) -> submit -> verdict -> rank/XP. Keep <=3 min.
- [ ] 4. Write `docs/demo-script.md` matching the video.
- [ ] 5. DEV post draft using challenge template + `#hf26challenge`; claim+number title candidates; sections: The System (what), Demo (video embed), Why open innovation matters, How I built it (real bugs: model contradiction handling, 4GB VRAM constraints, canned fallback), What is real and what is not (no pixel understanding v1; LAN-only demo), Prize categories (Best Use of Gemma), AI assistance disclosure line. NO roast-centric marketing.
- [ ] 6. Human: review post, upload video to DEV, submit before 12:29 IST. Push final commits.

```bash
git add -A docs README.md
git commit -m "docs: demo script, dev post draft, readme polish"
git push origin main
```

---

## Self-review notes (skill step, run at plan time)

1. Spec coverage: design.md sections 2-8 all mapped to Tasks 1-9; section 9 (submission) = Task 10. Cuts (roadmap) intentionally unimplemented.
2. Placeholders: Task 7/8 test bodies are described structurally but every assertion set is named; service/api implementers have exact signatures. Acceptable under sprint, but executor must write those tests explicitly per the named cases.
3. Type consistency: `PlayerState`/`Event` names consistent Tasks 1->7->8; `rules_verdict` kwargs consistent 5->7.
4. Review Focus: each of the 5 lines has an owning task + named tests (1: quests/ollama canned tests + service; 2: test_narrate_model_contradiction_is_discarded; 3: test_issue_is_idempotent_per_day + double-submit test; 4: three demotion tests; 5: rules tiny/stale + compress garbage + api 422).