# SYSTEM://GRASS — v1 Implementation Reference & Build Plan

> **Purpose:** Complete build reference for the Touch Grass challenge entry (Hacktoberfest 2026 Week 1, "Touch Grass"). Everything needed to implement v1 with zero prior context: architecture, data flow, module contracts, design rationale, and a task-by-task TDD build order.
>
> **Positioning:** Local-first outdoor quest game. Phone browser is a thin terminal; a laptop runs FastAPI + SQLite + Ollama (Gemma). No cloud, no accounts, no keys. Demo = video; submission window ends **2026-10-11 23:59 PDT (= 2026-10-12 12:29 IST)**.
>
> **Status (locked):** Design frozen in `docs/design.md`. State machine (`app/state.py`) already implemented — 14 tests green. This document is the execution reference from that point forward.

## Contents

1. [What we are building](#1-what-we-are-building)
2. [Architecture (system diagram)](#2-architecture-system-diagram)
3. [Flow charts — quest lifecycle & evidence pipeline](#3-flow-charts--quest-lifecycle--evidence-pipeline)
4. [Tech stack & why](#4-tech-stack--why)
5. [Module map & file responsibilities](#5-module-map--file-responsibilities)
6. [Data model (SQLite schema)](#6-data-model-sqlite-schema)
7. [API contract](#7-api-contract)
8. [Judging model — rules tier vs model tier](#8-judging-model--rules-tier-vs-model-tier)
9. [State machine contract (already shipped)](#9-state-machine-contract-already-shipped)
10. [Frontend screens](#10-frontend-screens)
11. [Design decisions (locked vs open)](#11-design-decisions-locked-vs-open)
12. [Build tasks (TDD order)](#12-build-tasks-tdd-order)
13. [Global constraints](#13-global-constraints)
14. [Review focus — failure modes that must be pinned](#14-review-focus--failure-modes-that-must-be-pinned)
15. [Gotchas (host-specific)](#15-gotchas-host-specific)
16. [Milestones & deadline plan](#16-milestones--deadline-plan)

---

## 1. What we are building

A single-player, daily outdoor quest game with a **harsh-but-funny "System" persona**. Each day the System issues one outdoor quest ("SKY CHECK: photograph the sky from outside"). The player submits photos; a deterministic **rules tier** decides PASS/FAIL; a **local Gemma model** narrates the verdict in the System's voice. XP, ranks (F→S), streaks, and penalties persist in SQLite.

Design pillars (user-locked):

- **Phone-as-terminal.** No app install. The phone's browser hits the laptop over LAN.
- **Local brain.** Quest flavor + verdict narration come from Ollama running Gemma on the laptop. If the model is down, canned banks keep the game alive — this doubles as the demo-resilience story and the `GRASS_MOCK=1` test mode.
- **Rules are law.** The model never decides PASS/FAIL. It may only narrate a decision that code already made. If it disagrees, its line is discarded.
- **Fun first, nudge second.** Personal daily game; the outdoor nudge is a side effect, not a health product. (Health framing deliberately dropped from design.)

What v1 is **not** (hard cuts, listed for scope honesty in the DEV post): Inner Demon, shop, regression token, raids, duels, persona pack, on-device CLIP, VLM/image judging, audio quests, push notifications, auth, cloud keys.

---

## 2. Architecture (system diagram)

```text
┌────────────────────────── PHONE (any browser, same Wi-Fi) ──────────────────────────┐
│  static/index.html  +  app.js  +  style.css                                          │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐              │
│  │ Quest Window│──▶│Evidence      │──▶│ Verdict      │──▶│ Log       │  (4 screens │
│  │ rank/XP bar │   │Capture(camera)│   │ PASS/PENALTY │   │ timeline  │   toggled)  │
│  └─────────────┘   └──────┬───────┘   └──────────────┘   └───────────┘              │
└───────────────────────────┼──────────────────────────────────────────────────────────┘
                            │  fetch() JSON + multipart (LAN, http)
                            ▼
┌─────────────────────────── LAPTOP (FastAPI, uvicorn :8000) ─────────────────────────┐
│  app/main.py            routes: /api/state /api/evidence /api/log /api/health       │
│       │                                                                             │
│       ▼                                                                             │
│  app/service.py         orchestration: issue-quest, submit, ignore-check            │
│       │            ┌────────────────────┬───────────────────┐                       │
│       ▼            ▼                    ▼                   ▼                       │
│  app/state.py   app/quests.py      app/judge.py        app/images.py               │
│  (XP/rank/      (14 templates,     (rules tier +       (Pillow JPEG                 │
│   streak pure    anti-repeat,       narration)          ≤200KB /                   │
│   functions)     flavor)                                1280px)                     │
│       │            │                    │                                           │
│       ▼            ▼                    ▼                                           │
│  grass.db (SQLite: player/quests/evidence/verdicts/template_usage)                 │
│                            evidence/ (compressed JPEGs on disk)                    │
│                                                                                     │
│                            app/ollama_client.py                                    │
│                                    │  (only if not GRASS_MOCK)                     │
└────────────────────────────────────┼────────────────────────────────────────────────┘
                                     ▼
                     ┌──────── Ollama (localhost:11434) ────────┐
                     │  gemma3:4b  (primary — VRAM ~4GB)        │
                     │  llama3.2   (fallback)                   │
                     │  qwen2.5:7b (fallback)                   │
                     │  JSON-only replies: {flavor}/{verdict,line}│
                     └──────────────────────────────────────────┘
```

Key properties the diagram encodes:

- **One trust boundary:** the laptop is the whole game; the phone is a display + camera. No auth because there is nothing to guard beyond one player's own fun on a private LAN.
- **One model boundary:** `app/ollama_client.py` is the *only* module that talks to Ollama. Everything else calls it or the canned banks.
- **Everything else is deterministic.** State machine, rules tier, template pool, image compression — all pure or I/O-predictable, all unit-testable without a model.

---

## 3. Flow charts — quest lifecycle & evidence pipeline

### 3a. Daily quest lifecycle (state + quest issuance)

```text
        ┌──────────────┐
        │  new day     │  player opens app (GET /api/state)
        └──────┬───────┘
               ▼
   ┌───────────────────────┐     yes    ┌────────────────────────┐
   │ quest exists for      │──────────▶ │ return it (idempotent)│──┐
   │ today?                │            └────────────────────────┘  │
   └───────┬───────────────┘                                        │
           │ no                                                     │
           ▼                                                        │
   ┌───────────────────────┐     yes    ┌────────────────────────┐  │
   │ quest exists for      │  no verdict│ apply_ignore(state)    │  │
   │ YESTERDAY, and        │──────────▶ │ demote −1 rank step,   │  │
   │ never got a verdict?  │            │ reset streak, record   │  │
   └───────┬───────────────┘            │ synthetic verdict row  │  │
           │ no / already judged        └───────────┬────────────┘  │
           ▼                                        │               │
   ┌───────────────────────┐                        │               │
   │ pick_template(conn)   │◀───────────────────────┘               │
   │ (least-used bias,     │                                        │
   │  14-tuple pool)       │                                        │
   └───────┬───────────────┘                                        │
           ▼                                                        │
   ┌───────────────────────┐  fails / mock  ┌────────────────────┐  │
   │ gemma_flavor(title,   │───────────────▶│ CANNED_FLAVOR line │  │
   │ objective)            │                └────────┬───────────┘  │
   └───────┬───────────────┘                         │              │
           │ model OK                                │              │
           ▼                                         ▼              │
   ┌───────────────────────────────────────────────────────────┐   │
   │ INSERT quests (day UNIQUE) + upsert template_usage        │   │
   └───────────────────────────────┬───────────────────────────┘   │
                                   └───────────────────────────────┘
```

Notes:
- **Same-day re-open never re-issues** (Review Focus #3). `day` carries a UNIQUE constraint; the lookup short-circuits.
- **Ignore only fires when the record proves it** (Review Focus #4): a quest row for yesterday *and* no verdict for it. A fresh DB, or a judged quest, never demotes.

### 3b. Evidence submission → verdict pipeline

```text
 phone: N photos + captured_at ISO
        POST /api/evidence  (multipart)
                │
                ▼
   ┌────────────────────────────┐  already   ┌─────────────────────┐
   │ quest already has a verdict│───────────▶│ HTTP 409            │
   └────────────┬───────────────┘            │ "already adjudicated"│
                │ no                         └─────────────────────┘
                ▼
   ┌────────────────────────────┐  bad bytes ┌─────────────────────┐
   │ images.compress_jpeg each  │───────────▶│ HTTP 422 (system    │
   │ (≤1280px, ≤200KB, RGB)     │            │  voice: "indecipherable│
   └────────────┬───────────────┘            │  evidence")         │
                │ ok                         └─────────────────────┘
                ▼
   ┌────────────────────────────┐
   │ save evidence/NNN.jpg      │
   │ INSERT evidence rows       │
   └────────────┬───────────────┘
                ▼
   ┌────────────────────────────┐
   │ RULES TIER (authoritative) │  judge.rules_verdict()
   │  count  ≥ target?          │  → {passed, reason}
   │  captured_at ≥ issued_at?  │     insufficient | stale |
   │  now ≤ deadline?           │     deadline | sincere | PASS
   │  size ≥ 3000 bytes each?   │
   └────────────┬───────────────┘
                ▼
   ┌────────────────────────────┐   mock / down / contradicts
   │ MODEL TIER (narration only)│────────────────────────────┐
   │  narrate(quest, meta, pass)│                            │
   │  expects {"verdict","line"}│                            ▼
   └────────────┬───────────────┘                 ┌──────────────────────┐
                │ agrees                          │ CANNED_PASS /        │
                ▼                                 │ CANNED_PENALTY bank  │
   ┌────────────────────────────┐                 │ model_used="canned"  │
   │ model line + model_used    │                 └──────────┬───────────┘
   └────────────┬───────────────┘                            │
                └──────────────────┬─────────────────────────┘
                                   ▼
   ┌──────────────────────────────────────────────────────────┐
   │ state.apply_pass(base_xp, day, perfect)  or apply_fail() │
   │  +40–80, streak+1 (bonus +5/day cap +25, perfect +20)    │
   │  or −30 XP, streak reset (XP floor 0)                    │
   └────────────┬─────────────────────────────────────────────┘
                ▼
   ┌──────────────────────────────────────────────────────────┐
   │ save_state, INSERT verdicts (events_json, model_used)    │
   │ return verdict payload → Verdict screen                  │
   └──────────────────────────────────────────────────────────┘
```

Two invariants this pipeline must never violate:

1. **Binary outcome comes only from the rules tier.** The model's `"verdict"` field is *checked against* the rules result, never *merged into* it. Contradiction ⇒ canned bank (Review Focus #2).
2. **A quest is adjudicated at most once.** The 409 guard lives in the service, not the UI.

---

## 4. Tech stack & why

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.13 | Already on the machine; one language for rules, state, server, and tests. |
| API | FastAPI 0.40-era + uvicorn | Tiny surface (4 routes); serves JSON *and* the static frontend from one process; multipart upload is a one-liner. |
| Storage | SQLite (`grass.db`) | Zero-ops, single file, WAL-unneeded at this scale; schema stays dump-able for the DEV post. |
| Model runtime | Ollama (local) | `gemma3:4b` fits the RTX 2050 4GB; llama3.2/qwen2.5 as fallbacks; `keep_alive:-1` avoids reload stalls between quest and verdict calls. |
| Model I/O | `format:"json"` chat | Deterministic parse target; we still wrap in try/except because JSON mode is a hint, not a guarantee. |
| Images | Pillow 11 | Bounded re-encode (≤1280px, ≤200KB) so the rules tier sees honest file sizes and the DB stays small. |
| Frontend | Vanilla HTML/CSS/JS | No build step under a 10-hour clock; phone only needs a browser; four screens is under the complexity threshold for a framework. |
| Tests | pytest 9 + httpx TestClient | The state machine, rules tier, quest engine, and API are all testable with `GRASS_MOCK=1` — no live Ollama in CI. |
| Demo | Video (allowed by rules) | No deploy target needed; LAN demo + screen recording is sufficient and honest. |

**Deliberately rejected for v1:** anything requiring a second process, a build toolchain, image-classification models (VRAM + honesty), or push infra.

---

## 5. Module map & file responsibilities

| File | Responsibility | Talks to |
|---|---|---|
| `app/state.py` | Pure XP/rank/streak transitions. No I/O, no time calls except injected `date`. | nothing (already shipped) |
| `app/db.py` | Connection factory, schema DDL, `init_db`. Env override `GRASS_DB` for tests. | filesystem (SQLite) |
| `app/quests.py` | 14-tuple template pool, least-used picking, daily issuance (idempotent), quest-side flavor line. | db, ollama_client |
| `app/ollama_client.py` | The only Ollama touchpoint. `chat_json`, `reachable`, `mock_mode`, model fallback chain. | Ollama HTTP |
| `app/judge.py` | `rules_verdict` (authoritative) + `narrate` (subordinate) + canned banks. | ollama_client |
| `app/images.py` | `compress_jpeg` — Pillow normalize to bounded JPEG or `ValueError`. | Pillow |
| `app/service.py` | Orchestration: load/save state, get-or-issue-today (incl. ignore check), submit_evidence (the pipeline in §3b), get_log. | all of the above |
| `app/main.py` | FastAPI app, 4 routes, startup (`init_db`, `evidence/` mkdir), static mount. | service |
| `static/index.html`, `style.css`, `app.js` | 4 screens, system-window aesthetic, fetch plumbing. | HTTP API |
| `tests/*` | One test file per module; API-level flows in `test_api.py`; never touches live Ollama. | pytest |

**Dependency direction (never inverted):** `main → service → {state, quests, judge, images, db} → ollama_client → Ollama`. `state` imports nothing from the project.

---

## 6. Data model (SQLite schema)

```sql
player      (id=1 PK, xp INT, rank_index INT, streak INT, last_pass_day TEXT)
quests      (id PK, day TEXT UNIQUE, template_id, title, objective,
             target_count INT, deadline_hour INT, xp_reward INT,
             difficulty TEXT, issued_at TEXT, flavor TEXT)
evidence    (id PK, quest_id FK, path TEXT, captured_at TEXT, uploaded_at TEXT)
verdicts    (id PK, quest_id FK, passed INT, xp_delta INT,
             rank_before TEXT, rank_after TEXT, streak_after INT,
             line TEXT, flavor TEXT, model_used TEXT,
             events_json TEXT, created_at TEXT)
template_usage (template_id PK, times_used INT)
```

Reading notes:

- `day` / dates are ISO strings (`YYYY-MM-DD`, or full ISO for timestamps) — string comparison is chronological, which keeps `ORDER BY` and "yesterday" logic trivial.
- `model_used` records `"gemma3:4b"` / `"llama3.2"` / … or `"canned"` — this is what makes the "what is real" honesty section in the DEV post cheap to write.
- `events_json` stores the `state.apply_*` event list so the Log screen can show rank-up lines without replaying transitions.

---

## 7. API contract

| Route | Method | Input | Output | Errors |
|---|---|---|---|---|
| `/api/state` | GET | — | `{player:{xp,rank,streak}, quest:{…}\|null, verdict:{…}\|null, server_time}` | — |
| `/api/evidence` | POST | multipart: `files[]`, `quest_id:int`, `captured_at:ISO` | verdict payload (see below) | 409 already adjudicated; 422 undecodable |
| `/api/log` | GET | — | `{entries:[verdict rows newest-first, cap 50]}` | — |
| `/api/health` | GET | — | `{ollama:bool, mock:bool, model:str}` | — |
| `/` , `/static/*` | GET | — | frontend assets | 404 |

Verdict payload shape (stable contract for `app.js`):

```json
{
  "passed": true,
  "line": "Evidence accepted. The System remains unimpressed.",
  "model_used": "gemma3:4b",
  "xp_delta": 45,
  "rank_before": "F", "rank_after": "F",
  "streak": 1,
  "events": [{"kind": "XP_GAINED", "detail": "+45 XP"}],
  "flavor": "Today's assignment reflects low but non-zero expectations."
}
```

`GET /api/state` issues today's quest as a side effect (first call of the day runs the §3a lifecycle). This keeps the frontend at "one fetch per screen change".

---

## 8. Judging model — rules tier vs model tier

The single most important design line in the project:

| Tier | Decides | May influence | Failure mode |
|---|---|---|---|
| **Rules** (`judge.rules_verdict`) | PASS/FAIL + reason string | nothing — it is final | deterministic, fully unit-tested |
| **Model** (`judge.narrate`) | nothing | the *wording* of the verdict line | down / slow / contradictory ⇒ canned bank |

Rules checks, in evaluation order (first failure wins, reason string is user-visible in the System voice):

1. `len(evidence) >= target_count` — "insufficient evidence"
2. every `captured_at >= quest.issued_at` — "stale capture" (blocks screenshot-reuse and gallery-time-travel)
3. `now.hour <= deadline_hour` — "deadline exceeded"
4. every file ≥ 3000 bytes — "evidence too small to be sincere" (blocks 1×1 pixel and empty uploads)

Model prompt contract: system = "You are THE SYSTEM: cold, bureaucratic outdoor quest authority. Dry humor. Never friendly, never obscene. Output JSON only." User = one-sentence request with expected shape `{"verdict":"PASS|PENALTY","line":string}` (≤30 words). Code compares `verdict` to the rules result; mismatch or parse failure ⇒ canned. This is Review Focus #2, and the contradiction test (`test_narrate_model_contradiction_is_discarded`) pins it.

Why text-only (no image understanding) in v1: 4GB VRAM budget shared with the narrative model, no reliable free VLM that fits the honesty bar under deadline, and the challenge rewards *open innovation demonstrated*, not model maximalism. The DEV post says this out loud.

---

## 9. State machine contract (already shipped)

`app/state.py` — pure functions, no I/O, injected `date`:

```python
PlayerState(xp, rank_index, streak, last_pass_day)   # frozen dataclass; .rank -> str
apply_pass(state, base_xp, day, perfect=False) -> (PlayerState, [Event])
apply_fail(state) -> (PlayerState, [Event])
apply_ignore(state) -> (PlayerState, [Event])
Event(kind, detail)  # XP_GAINED | XP_LOST | RANK_UP | RANK_DOWN | STREAK_BROKEN
RANKS = ("F","E","D","C","B","A","S")
THRESHOLDS = (0, 100, 250, 500, 850, 1300, 2000)
```

Semantics pinned by 14 tests (all green): pass = +base XP, streak+1, perfect bonus, streak bonus +5/day capped at +25, rank-up events on threshold cross; fail = −30 with floor at 0 and streak reset; ignore = one rank step down (XP untouched — rank never falls from XP loss alone), streak reset; recovery via a later pass; same-day double-pass is a no-op.

---

## 10. Frontend screens

Four screens, one page, JS-toggled (no router):

| Screen | Shown when | Shows |
|---|---|---|
| **Quest Window** | default | rank letter, XP bar toward next rank, streak, today's title/objective/target, flavor line, capture button |
| **Evidence Capture** | after "capture" | `<input type=file capture=environment multiple>`, submit, "JUDGING…" in-flight state |
| **Verdict Window** | after POST | PASS/PENALTY banner, narration line, XP delta, any RANK_UP events |
| **Log** | after verdict / nav | timeline of past verdicts (title, pass/fail, line, model_used) |

Aesthetic (locked): terminal black `#05060a`, amber `#ffb000` primary, green `#39ff14` accent, 1px bordered "windows", monospace. Poll `/api/state` on load and after actions only — no timers. On 409, show the existing verdict instead of an error (the game already judged you).

---

## 11. Design decisions (locked vs open)

**Locked (do not relitigate during build):**

- Rules tier authoritative; model narrates only (§8).
- `GRASS_MOCK=1` forces canned narration — required for tests, available for demos.
- Model chain: `GRASS_MODEL` (default `gemma3:4b`) → `llama3.2` → `qwen2.5:7b`; `keep_alive:-1`.
- Ranks/XP/streak constants exactly as §9.
- Roasts are a feature **not advertised** — README/DEV post pitch the loop and the local/offline story, not the meanness.
- No Solo Leveling IP anywhere; README keeps a disclaimer line only.
- Demo = video; no deploy.
- Privacy: nothing about the user's personal circumstances appears in repo, docs, or post.

**Open (decide only if forced by implementation):**

- Whether `/api/evidence` applies one `captured_at` to all files (current plan) or per-file (defer unless trivial).
- Exact canned-bank line wording (voice-checked at demo time).
- Video length and whether field footage or phone-screen-record only.

---

## 12. Build tasks (TDD order)

Each task: failing test → minimal implementation → green → commit (exact-path `git add`). Task 1 is verification-only.

### Task 1 — State machine (SHIPPED)
Verify: `python -m pytest tests/test_state.py -q` → `14 passed`. Commit only if uncommitted.

### Task 2 — Database layer
Create `app/db.py` (`connect`, `init_db`, `SCHEMA` §6, `DB_PATH` with `GRASS_DB` env override) + `tests/test_db.py` (tables created, player row seeded, Row factory works).

### Task 3 — Ollama client
Create `app/ollama_client.py` (`mock_mode`, `chat_json` with fallback chain and `RuntimeError` on total failure, `reachable`) + `tests/test_ollama.py` (mock env parsing, fallback order via monkeypatched `requests.post`, all-fail raises, reachable-false).

### Task 4 — Quest engine
Create `app/quests.py`: the 14-template pool (ids: living, sky, textures, lane, colors, ground, shadows, furthest, small, altered, night, doorstep, water, still — each with difficulty/xp/target_count/deadline_hour/title/objective), `CANNED_FLAVOR` bank, `pick_template` (least-used bias), `gemma_flavor` (any failure ⇒ canned), `issue_quest_for_day` (idempotent). Tests: same-day idempotent, first pass over 14 days hits all 14 ids distinct, mock flavor ∈ canned bank.

### Task 5 — Judge
Create `app/judge.py`: `rules_verdict` (§8 order), `CANNED_PASS`/`CANNED_PENALTY`, `narrate` with contradiction-discard. Tests: happy PASS, insufficient, stale, deadline, tiny file, mock canned, model contradiction discarded.

### Task 6 — Image compression
Create `app/images.py` (`compress_jpeg`) + tests (downscale ≤1280 and ≤200KB; garbage bytes ⇒ `ValueError`).

### Task 7 — Game service
Create `app/service.py`: `load_state`/`save_state`, `get_or_issue_today` (§3a incl. ignore check), `submit_evidence` (§3b, 409 via `ServiceError`), `get_log`. Tests: pass flow, fail flow, double-submit raises, yesterday-ignored demotes, yesterday-passed does not, no-yesterday-record does not.

### Task 8 — FastAPI app
Create `app/main.py` (routes §7, startup, static) + `tests/test_api.py` (httpx TestClient; `pip install httpx` in this task). Full suite must be green at task end.

### Task 9 — Frontend
Create `static/index.html`, `style.css`, `app.js` (§10). Manual E2E with `GRASS_MOCK=1` on desktop + phone.

### Task 10 — E2E, demo video, DEV post, submission
Full suite count recorded; one real-Ollama smoke (flavor + narrated verdict, note latency); demo video ≤3 min; `docs/demo-script.md`; DEV post draft (challenge template, `#hf26challenge`, claim+number title, "why open matters", "what is real / what is not", Gemma category mapping, AI-assistance disclosure); human submits by **11:30 IST**.

---

## 13. Global constraints

- No cloud, accounts, API keys, push notifications. LAN only.
- `GRASS_MOCK=1` ⇒ canned everywhere; tests never require live Ollama.
- Rules decide PASS/FAIL; model narration is subordinate (§8).
- Photos: JPEG, client ≤200KB target, server ≤1280px / ≤200KB.
- Ranks F..S at 0/100/250/500/850/1300/2000; fail −30 XP; streak bonus +5/day cap +25; perfect +20.
- No IP-lookalike naming; no personal-health framing anywhere.
- Commit after every task; exact-path adds; no force-push.
- Hard deadline: demo video by **~10:30 IST**, submission by **11:30 IST** (before 12:29 IST cut-off).

---

## 14. Review focus — failure modes that must be pinned

| # | Failure mode | Expected behavior | Pinned by |
|---|---|---|---|
| 1 | Ollama down or times out mid-verdict | Canned PASS/FAIL still returned; game continues | Task 3 fallback tests + Task 5 mock/narrate tests + Task 7 happy paths under `GRASS_MOCK` |
| 2 | Model contradicts rules verdict | Rules verdict wins; line swapped to canned | `test_narrate_model_contradiction_is_discarded` (Task 5) |
| 3 | Same-day re-open double-issues or re-adjudicates | Quest idempotent per day; second submit → 409 | `test_issue_is_idempotent_per_day` (Task 4) + double-submit test (Task 7) |
| 4 | Missed-yesterday demotion mis-fires | Demote only if yesterday's quest exists *and* was never judged | 3 demotion tests (Task 7) |
| 5 | Hostile uploads (garbage bytes, tiny files, gallery-old timestamps) | 4xx with System-voice message; never a 500 | Task 6 garbage test, Task 5 tiny/stale rules tests, Task 8 422 test |

---

## 15. Gotchas (host-specific)

- **PowerShell 5.1 host:** no `&&`; no inline comments in `.gitignore`; PS `.Replace(a,b,n)` 3-arg overload does **not** exist; a triple-backtick inside a double-quoted PS string breaks parsing; console cp1252 mangles unicode — write UTF-8 files via `[System.IO.File]::WriteAllText($p,$s,(New-Object System.Text.UTF8Encoding($false)))`.
- **bash tool = WSL** (`/mnt/c/...` paths); workdir is the Windows project dir.
- **VRAM:** `gemma3:4b` + desktop compositing on a 4GB RTX 2050 is tight — `keep_alive:-1` is set so we pay load cost once; if the pull is still running at build time, tests stay green because they are mock-mode.
- **Multipart on FastAPI** needs `python-multipart` (already installed, 0.0.22).
- **TestClient** needs `httpx` — install inside Task 8, not earlier.
- **SQLite + uvicorn reloader:** single-worker default; do not enable `--reload` while a demo DB is live.

---

## 16. Milestones & deadline plan

| Milestone | Contents | Gate |
|---|---|---|
| M1 (done) | Design freeze + state machine + plan reference | 14 state tests green |
| M2 | Tasks 2–6 (backend core) | per-task suites green |
| M3 | Tasks 7–8 (service + API) | **full suite green** |
| M4 | Task 9 (frontend) | manual E2E loop on phone |
| M5 | Task 10 (video, post, submit) | submission receipt before 11:30 IST |

---

*Self-review performed at write time: spec sections map to Tasks 1–10; §14 failure modes each have an owning task and named test; signatures in §5–§7 match across modules (`PlayerState`/`apply_*`, `rules_verdict`, `narrate`, `ServiceError`, `compress_jpeg`, `chat_json`); no TBD/placeholder bodies — Tasks 7–8 tests are specified by named assertion cases with the §3b pipeline as the contract.*