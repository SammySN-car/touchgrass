# SYSTEM://GRASS — v1 Implementation Reference & Build Plan

> **Purpose:** Complete build reference for the Touch Grass challenge entry (Hacktoberfest 2026 Week 1, "Touch Grass"). Everything needed to implement v1 with zero prior context: architecture, data flow, module contracts, design rationale, and a task-by-task TDD build order.
>
> **Positioning:** Local-first outdoor quest game with **offline-first evidence capture**. Phone browser is a thin terminal with a pending buffer; a laptop runs FastAPI + SQLite + Ollama (Gemma). No cloud, no accounts, no keys. Gameplay needs **no internet at all** — the laptop link is only required twice: fetching the quest and submitting evidence (buffers make the second one deferrable). Demo = video; submission window ends **2026-10-11 23:59 PDT (= 2026-10-12 12:29 IST)**.
>
> **Status (locked):** Design frozen in `docs/design.md`. State machine (`app/state.py`) already implemented — 14 tests green. Offline buffer design locked (§11). This document is the execution reference from that point forward.

## Contents

1. [What we are building](#1-what-we-are-building)
2. [Architecture (system diagram)](#2-architecture-system-diagram)
3. [Flow charts — quest lifecycle, buffer & evidence pipeline](#3-flow-charts--quest-lifecycle-buffer--evidence-pipeline)
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

A single-player, daily outdoor quest game with a **harsh-but-funny "System" persona**. Each day the System issues one outdoor quest ("SKY CHECK: photograph the sky from outside"). The player submits photos; a deterministic **rules tier** decides PASS/FAIL; a local **Gemma model** narrates the verdict in the System's voice. XP, ranks (F→S), streaks, and penalties persist in SQLite.

Design pillars (user-locked):

- **Phone-as-terminal.** No app install. The phone's browser hits the laptop over LAN.
- **Offline-first capture.** The player walks away from the laptop to complete the quest. The phone camera needs no connectivity; photos land in an **IndexedDB pending buffer** with the moment they were taken. Submission happens when the phone is back in laptop range (auto on reconnect, or one manual tap). Gameplay itself never needs the internet — Ollama, FastAPI, and SQLite all live on the laptop.
- **Local brain.** Quest flavor + verdict narration come from Ollama running Gemma on the laptop. If the model is down, canned banks keep the game alive — this doubles as the demo-resilience story and the `GRASS_MOCK=1` test mode.
- **Rules are law.** The model never decides PASS/FAIL. It may only narrate a decision that code already made. If it disagrees, its line is discarded.
- **Fun first, nudge second.** Personal daily game; the outdoor nudge is a side effect, not a health product. (Health framing deliberately dropped from design.)

What v1 is **not** (hard cuts, listed for scope honesty in the DEV post): Inner Demon, shop, regression token, raids, duels, persona pack, on-device CLIP, VLM/image judging, audio quests, push notifications, auth, cloud keys, GPS/geofence location checks, background upload daemon.

---

## 2. Architecture (system diagram)

```text
┌──────────────────────── PHONE (browser, may be OFFLINE while outside) ────────────────┐
│                                                                                       │
│   camera / file picker                                                                │
│        │                                                                              │
│        ▼                                                                              │
│   ┌────────────────────────────┐      reconnect / online event / manual tap           │
│   │  PENDING BUFFER            │──────────────────────────────┐                       │
│   │  IndexedDB store "pending" │                              │                       │
│   │  {quest_id, blob,          │                              ▼                       │
│   │   captured_at} per photo   │                      POST /api/evidence              │
│   └────────────────────────────┘                      (multipart, may be late)        │
│                                                                                       │
│   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐              │
│   │ Quest Window│──▶│Evidence      │──▶│ Verdict      │──▶│ Log       │  (4 screens │
│   │ rank/XP bar │   │Capture+PENDING│  │ PASS/PENALTY │   │ timeline  │   toggled)  │
│   └─────────────┘   └──────────────┘   └──────────────┘   └───────────┘              │
└───────────────────────────┼──────────────────────────────────────────────────────────┘
                            │  fetch() JSON + multipart (LAN, http; only when in range)
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

- **Two contacts, one game loop.** The phone needs the laptop exactly twice per day — `GET /api/state` (quest) and `POST /api/evidence` (verdict). Everything between them (walking, shooting, buffering) happens with zero connectivity.
- **The buffer is the bridge.** IndexedDB lives on the phone; photos + their capture timestamps survive screen lock, app reload, and hours outside. A late submit is a first-class path, not an error.
- **One trust boundary:** the laptop is the whole game brain; the phone is a display + camera + queue. No auth — one player, private LAN.
- **One model boundary:** `app/ollama_client.py` is the *only* module that talks to Ollama. Everything else calls it or the canned banks.
- **Everything else is deterministic.** State machine, rules tier, template pool, image compression — all pure or I/O-predictable, all unit-testable without a model.
---

## 3. Flow charts — quest lifecycle, buffer & evidence pipeline

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
   │ return quest (+ existing verdict if already judged)       │   │
   └───────────────────────────────┬───────────────────────────┘   │
                                   │                               │
   frontend: if IndexedDB has pending rows for THIS quest_id       │
             → show "SUBMIT PENDING (N)" (§3b) ────────────────────┘
```

Notes:
- **Same-day re-open never re-issues** (Review Focus #3). `day` carries a UNIQUE constraint; the lookup short-circuits.
- **Ignore only fires when the record proves it** (Review Focus #4): a quest row for yesterday *and* no verdict for it. A fresh DB, or a judged quest, never demotes.
- **Quest fetch and buffer are decoupled.** The server has no idea a buffer exists; the frontend just refuses to auto-flush rows whose `quest_id` doesn't match the currently returned quest.

### 3b. Offline buffer lifecycle (capture → pending → flush)

```text
   shutter press / file pick (OUTSIDE, no laptop contact)
                │
                ▼
   ┌────────────────────────────┐
   │ captured_at = Date.now()   │  wall-clock at the moment of capture;
   │ (client stamp, §11)        │  this is what the staleness rule reads
   └────────────┬───────────────┘
                ▼
   ┌────────────────────────────┐
   │ IndexedDB "pending" store  │  one row per photo:
   │ put({quest_id, blob,       │  {quest_id, blob, captured_at}
   │      captured_at})         │  survives screen-lock / reload / hours
   └────────────┬───────────────┘
                ▼
   ┌────────────────────────────┐
   │ UI: "N PENDING — submit    │
   │ when back in range"        │  (badge on Evidence screen; no error,
   └────────────┬───────────────┘   this is the happy offline path)
                │
     user walks back into laptop range
     (or taps SUBMIT PENDING manually)
                │
                ▼
   ┌────────────────────────────┐
   │ window "online" event OR   │
   │ app load: flushPending()   │  reads rows WHERE quest_id == current
   └────────────┬───────────────┘
                ▼
   ┌────────────────────────────┐
   │ group rows by stored       │  POST multipart
   │ quest_id; files +          │  (files, quest_id,
   │ quest_id + captured_at     │   captured_at of newest row)
   └────────────┬───────────────┘
        ┌───────┴────────┬─────────────────┐
        ▼                ▼                 ▼
   ┌─────────┐     ┌───────────┐     ┌──────────────┐
   │ 200 OK  │     │ HTTP 409  │     │ network fail │
   │ delete  │     │ delete    │     │ KEEP rows    │
   │ all rows│     │ rows +    │     │ show "still  │
   │ show    │     │ show the  │     │  pending"    │
   │ verdict │     │ existing  │     └──────────────┘
   └─────────┘     │ verdict   │
                   └───────────┘
```

Design consequences worth stating out loud:

- **A late submit is legal.** The rules tier already validates `captured_at >= issued_at` (photo taken after quest issued) and `now <= deadline_hour` (submitted before deadline). Buffering changes *when* the POST happens, never *what* the rules accept.
- **A missed day stays missed.** If the player never comes back in range before the deadline, tomorrow's `get_or_issue_today` applies the ignore demotion (§3a). The buffer does not rescue no-shows — by design.
- **Buffered photos for an already-adjudicated quest get a clean 409**, which the frontend turns into "the System already judged this" and clears the rows. No zombie resubmits.

### 3c. Evidence submission → verdict pipeline (server side)

```text
 POST /api/evidence  (multipart: N photos + quest_id + captured_at)
                │
                ▼
   ┌────────────────────────────┐  already   ┌─────────────────────┐
   │ quest already has a verdict│───────────▶│ HTTP 409            │
   └────────────┬───────────────┘            │ "already adjudicated"│
                │ no                         └─────────────────────┘
                ▼
   ┌────────────────────────────┐  bad bytes ┌─────────────────────┐
   │ images.compress_jpeg each  │───────────▶│ HTTP 422 (system    │
   │ (≤1280px, ≤200KB, RGB)     │            │  voice)             │
   └────────────┬───────────────┘            └─────────────────────┘
                │ ok
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
   │  narrate(quest, meta, pass)│                            ▼
   │  expects {"verdict","line"}│                 ┌──────────────────────┐
   └────────────┬───────────────┘                 │ CANNED_PASS /        │
                │ agrees                          │ CANNED_PENALTY bank  │
                ▼                                 │ model_used="canned"  │
   ┌────────────────────────────┐                 └──────────┬───────────┘
   │ model line + model_used    │                            │
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
2. **A quest is adjudicated at most once.** The 409 guard lives in the service, not the UI — the buffer's flush path relies on it (Review Focus #3, #7).
---

## 4. Tech stack & why

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.13 | Already on the machine; one language for rules, state, server, and tests. |
| API | FastAPI + uvicorn | Tiny surface (4 routes); serves JSON *and* the static frontend from one process; multipart upload is a one-liner. |
| Storage | SQLite (`grass.db`) | Zero-ops, single file; schema stays dump-able for the DEV post. |
| Model runtime | Ollama (local) | `gemma3:4b` fits the RTX 2050 4GB; llama3.2/qwen2.5 as fallbacks; `keep_alive:-1` avoids reload stalls between quest and verdict calls. |
| Model I/O | `format:"json"` chat | Deterministic parse target; still wrapped in try/except because JSON mode is a hint, not a guarantee. |
| Images | Pillow 11 | Bounded re-encode (≤1280px, ≤200KB) so the rules tier sees honest file sizes and the DB stays small. |
| Frontend | Vanilla HTML/CSS/JS | No build step under a 10-hour clock; phone only needs a browser; four screens is under the complexity threshold for a framework. |
| **Offline buffer** | **IndexedDB (native, no lib)** | Survives screen-lock, reload, and hours outside; simple enough API for a few photos; zero dependencies. In-memory `Map` mirror as read fallback. |
| Tests | pytest 9 + httpx TestClient | The state machine, rules tier, quest engine, and API are all testable with `GRASS_MOCK=1` — no live Ollama in CI. |
| Demo | Video (allowed by rules) | No deploy target needed; LAN demo + screen recording is sufficient and honest. |

**Deliberately rejected for v1:** anything requiring a second process, a build toolchain, image-classification models (VRAM + honesty), push infra, tunnels/relays (would drag in the cloud we cut), and GPS checks (privacy + the rules tier already judges by timestamp).

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
| `app/service.py` | Orchestration: load/save state, get-or-issue-today (incl. ignore check), submit_evidence (§3c), get_log. Treats a late (buffered) submit identically to an in-session one. | all of the above |
| `app/main.py` | FastAPI app, 4 routes, startup (`init_db`, `evidence/` mkdir), static mount. | service |
| `static/index.html`, `style.css`, `app.js` | 4 screens, system-window aesthetic, fetch plumbing, **IndexedDB pending buffer + flush-on-reconnect** (§3b). | HTTP API + IndexedDB |
| `tests/*` | One test file per module; API-level flows in `test_api.py`; never touches live Ollama. | pytest |

**Dependency direction (never inverted):** `main → service → {state, quests, judge, images, db} → ollama_client → Ollama`. `state` imports nothing from the project. The buffer is frontend-only — the server never learns it exists.

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
- `captured_at` on evidence rows is the **client buffer stamp** (§11). The staleness rule reads it; EXIF cross-check is a documented non-goal for v1.
- `model_used` records `"gemma3:4b"` / `"llama3.2"` / … or `"canned"` — this is what makes the "what is real" honesty section in the DEV post cheap to write.
- `events_json` stores the `state.apply_*` event list so the Log screen can show rank-up lines without replaying transitions.
- **Phone-side (not in this DB):** IndexedDB store `pending` = `{id, quest_id, blob, captured_at}` per photo. Cleared on successful flush or on 409.

---

## 7. API contract

| Route | Method | Input | Output | Errors |
|---|---|---|---|---|
| `/api/state` | GET | — | `{player:{xp,rank,streak}, quest:{…}\|null, verdict:{…}\|null, server_time}` | — |
| `/api/evidence` | POST | multipart: `files[]`, `quest_id:int`, `captured_at:ISO` | verdict payload (see below) | 409 already adjudicated; 422 undecodable |
| `/api/log` | GET | — | `{entries:[verdict rows newest-first, cap 50]}` | — |
| `/api/health` | GET | — | `{ollama:bool, mock:bool, model:str}` | — |
| `/` , `/static/*` | GET | — | frontend assets | 404 |

Contract notes:

- **Submission may be deferred.** The server does not care whether the POST arrives 2 seconds or 3 hours after the photos were taken. Freshness is judged by `captured_at` vs `issued_at`; lateness by `now` vs `deadline_hour`. Nothing else about the request changes.
- `GET /api/state` issues today's quest as a side effect (first call of the day runs the §3a lifecycle). This keeps the frontend at "one fetch per screen change".
- Frontend uses the same `quest_id` it was issued (stored alongside the buffer rows), never "whatever today's quest happens to be" — a crossed-wire submit is structurally impossible.

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

---

## 8. Judging model — rules tier vs model tier

The single most important design line in the project:

| Tier | Decides | May influence | Failure mode |
|---|---|---|---|
| **Rules** (`judge.rules_verdict`) | PASS/FAIL + reason string | nothing — it is final | deterministic, fully unit-tested |
| **Model** (`judge.narrate`) | nothing | the *wording* of the verdict line | down / slow / contradictory ⇒ canned bank |

Rules checks, in evaluation order (first failure wins, reason string is user-visible in the System voice):

1. `len(evidence) >= target_count` — "insufficient evidence"
2. every `captured_at >= quest.issued_at` — "stale capture" (blocks screenshot-reuse and gallery-time-travel; works unchanged on buffered photos because the buffer stamps at capture time)
3. `now.hour <= deadline_hour` — "deadline exceeded" (a buffered submit after the deadline fails here, which is correct)
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
| **Quest Window** | default | rank letter, XP bar toward next rank, streak, today's title/objective/target, flavor line, capture button; **"N PENDING" chip if buffer non-empty for this quest** |
| **Evidence Capture** | after "capture" | `<input type=file capture=environment multiple>`, live buffer list (thumbnails + count), submit / **SUBMIT PENDING**, "JUDGING…" in-flight state |
| **Verdict Window** | after POST | PASS/PENALTY banner, narration line, XP delta, any RANK_UP events |
| **Log** | after verdict / nav | timeline of past verdicts (title, pass/fail, line, model_used) |

Buffer behavior in the UI (the offline contract):

- Picking/shooting photos writes them to IndexedDB **immediately** — no network needed, no spinner.
- Badge text is factual, not alarming: "3 PENDING — submit when back in range".
- On `window` `online` event **and** on every app load: `flushPending()` runs silently; success jumps to the Verdict Window, 409 shows the existing verdict and clears rows, network failure just leaves the badge.
- 409 is never an error dialog — the game already judged you; show the verdict (Review Focus #7).

Aesthetic (locked): terminal black `#05060a`, amber `#ffb000` primary, green `#39ff14` accent, 1px bordered "windows", monospace. Poll `/api/state` on load and after actions only — no timers.

---

## 11. Design decisions (locked vs open)

**Locked (do not relitigate during build):**

- Rules tier authoritative; model narrates only (§8).
- **Offline buffer:** native IndexedDB store `pending`, one row per photo, `captured_at` stamped client-side at the moment the photo enters the buffer. Flush on `online` event + app load + manual tap. Rows deleted only on 200 or 409. Server is buffer-unaware.
- Buffer submits always reference the **stored** `quest_id` — never "today's current quest" implicitly.
- `GRASS_MOCK=1` forces canned narration — required for tests, available for demos.
- Model chain: `GRASS_MODEL` (default `gemma3:4b`) → `llama3.2` → `qwen2.5:7b`; `keep_alive:-1`.
- Ranks/XP/streak constants exactly as §9.
- Roasts are a feature **not advertised** — README/DEV post pitch the loop, the offline-first story, and the local/offline brain, not the meanness.
- No Solo Leveling IP anywhere; README keeps a disclaimer line only.
- Demo = video; no deploy.
- Privacy: nothing about the user's personal circumstances appears in repo, docs, or post. No GPS, no location tracking — timestamps only.

**Open (decide only if forced by implementation):**

- Whether `/api/evidence` applies one `captured_at` to all files (current plan: newest buffered row's stamp) or per-file list (defer unless trivial).
- EXIF-vs-client-stamp cross-check on the server (documented non-goal; promote only if a gallery-reuse cheat shows up live).
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
Create `app/service.py`: `load_state`/`save_state`, `get_or_issue_today` (§3a incl. ignore check), `submit_evidence` (§3c, 409 via `ServiceError`), `get_log`. Treat buffered-late submits identically to in-session ones (same code path — the server cannot tell the difference). Tests: pass flow, fail flow, double-submit raises, yesterday-ignored demotes, yesterday-passed does not, no-yesterday-record does not.

### Task 8 — FastAPI app
Create `app/main.py` (routes §7, startup, static) + `tests/test_api.py` (httpx TestClient; `pip install httpx` in this task). Full suite must be green at task end.

### Task 9 — Frontend + offline buffer
Create `static/index.html`, `style.css`, `app.js` (§10) **including the buffer module**:
- `savePending(files, questId)` — stamps `captured_at`, puts one IndexedDB row per photo.
- `getPending(questId)` / `clearPending(ids)` — read/cleanup.
- `flushPending()` — group by stored `quest_id`, multipart POST, handle 200/409/network (§3b).
- Wire `window.addEventListener('online', flushPending)` + call on load; PENDING chip and badge UI.
- 409 → show existing verdict, clear rows. Never an error dialog.

Manual E2E with `GRASS_MOCK=1` **including the offline drill**: load quest → airplane mode → shoot 3 photos → verify badge → leave airplane mode → verify auto-flush → verdict. Also reload-page-mid-buffer to confirm persistence.

### Task 10 — E2E, demo video, DEV post, submission
Full suite count recorded; one real-Ollama smoke (flavor + narrated verdict, note latency); demo video ≤3 min **showing the offline drill** (airplane mode on, photos taken, reconnect, verdict lands); `docs/demo-script.md`; DEV post draft (challenge template, `#hf26challenge`, claim+number title, "why open matters", "what is real / what is not", offline-first capture as a headline feature, Gemma category mapping, AI-assistance disclosure); human submits by **11:30 IST**.

---

## 13. Global constraints

- No cloud, accounts, API keys, push notifications. **Gameplay needs no internet** — only the laptop LAN link at quest-fetch and submit time.
- `GRASS_MOCK=1` ⇒ canned everywhere; tests never require live Ollama.
- Rules decide PASS/FAIL; model narration is subordinate (§8).
- Photos: JPEG, client ≤200KB target, server ≤1280px / ≤200KB. Buffered client-side in IndexedDB before any upload.
- Ranks F..S at 0/100/250/500/850/1300/2000; fail −30 XP; streak bonus +5/day cap +25; perfect +20.
- No IP-lookalike naming; no personal-health framing anywhere; no GPS.
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
| 6 | Buffered photos lost on reload / screen-lock / hours outside | IndexedDB rows survive; badge restores on next load | Task 9 manual drill (airplane-mode + reload mid-buffer) |
| 7 | Flush targets a quest that was already judged (e.g. ignored next-day, or player submitted from another path) | 409 → buffer cleared, existing verdict shown, no error dialog | Task 7 409 test + Task 9 manual 409 handling |
| 8 | Buffered submit arrives after the deadline hour | Rules tier returns "deadline exceeded" FAIL; −30 XP path runs normally | Task 5 deadline test (server) — buffered timing is just a late POST |

---

## 15. Gotchas (host-specific)

- **PowerShell 5.1 host:** no `&&`; no inline comments in `.gitignore`; PS `.Replace(a,b,n)` 3-arg overload does **not** exist; a triple-backtick inside a double-quoted PS string breaks parsing; console cp1252 mangles unicode — write UTF-8 files via `[System.IO.File]::WriteAllText($p,$s,(New-Object System.Text.UTF8Encoding($false)))`.
- **bash tool = WSL** (`/mnt/c/...` paths); workdir is the Windows project dir.
- **VRAM:** `gemma3:4b` + desktop compositing on a 4GB RTX 2050 is tight — `keep_alive:-1` is set so we pay load cost once; if the pull is still running at build time, tests stay green because they are mock-mode.
- **Multipart on FastAPI** needs `python-multipart` (already installed, 0.0.22).
- **TestClient** needs `httpx` — install inside Task 8, not earlier.
- **SQLite + uvicorn reloader:** single-worker default; do not enable `--reload` while a demo DB is live.
- **Windows firewall:** first `uvicorn --host 0.0.0.0` run may prompt; allow on Private networks or the phone times out.
- **Phone↔laptop reachability:** same Wi-Fi is easiest; if the router isolates clients (AP isolation), use the phone hotspot or laptop Mobile Hotspot — both create a private LAN **even with no internet**, which is enough for gameplay. The demo video is recorded on that private link.
- **iOS Safari IndexedDB eviction:** Safari may purge IndexedDB under storage pressure or after long backgrounding. Mitigation: mirror rows in an in-memory `Map` on first read and write-through; acceptable v1 risk, noted honestly in the DEV post if it bites.
- **Buffer stamp vs EXIF:** `captured_at` is the client wall-clock at buffer time (§11). Picking a gallery photo older than the quest will stamp it "now" and pass the staleness check — known v1 limitation, listed under "what is not" in the post; server-side EXIF check is the named follow-up.

---

## 16. Milestones & deadline plan

| Milestone | Contents | Gate |
|---|---|---|
| M1 (done) | Design freeze + state machine + plan reference | 14 state tests green |
| M2 | Tasks 2–6 (backend core) | per-task suites green |
| M3 | Tasks 7–8 (service + API) | **full suite green** |
| M4 | Task 9 (frontend + offline buffer) | manual E2E loop on phone **incl. airplane-mode drill** |
| M5 | Task 10 (video with offline moment, post, submit) | submission receipt before 11:30 IST |

---

*Self-review performed at write time: spec sections map to Tasks 1–10; §14 failure modes each have an owning task and named test or manual drill; signatures in §5–§7 match across modules (`PlayerState`/`apply_*`, `rules_verdict`, `narrate`, `ServiceError`, `compress_jpeg`, `chat_json`, buffer helpers); no TBD/placeholder bodies; buffer decisions (store name, stamping, flush triggers, 409 handling) are locked in §11 and mirrored in §2/§3/§10/§12.*