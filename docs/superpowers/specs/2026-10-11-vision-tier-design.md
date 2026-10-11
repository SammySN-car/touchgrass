# Design Spec: Vision Verification Tier (Tier 1.5)

**Date:** 2026-10-11
**Status:** Approved (architecture), pre-implementation
**Supersedes:** the "Roadmap: on-device CLIP tier" note in design.md section 5, "Judge".

## 1. Goal

Add a server-side vision gate that catches *wrong* photos (e.g. a wall
submitted for a "photograph a pebble/leaf/crack" quest) before the verdict
is finalized. Today the judge reads only metadata (count, timestamp, size)
and cannot tell what is actually in the image.

Success = a rules-valid submission whose photos clearly do not match the
quest objective is rejected as "wrong photo"; everything else behaves
exactly as it does today.

## 2. Non-goals

- Does not change the phone client. All work is server-side.
- Does not make the model authoritative. Tier 1 rules stay the source of
  PASS/FAIL; this tier can only downgrade, never upgrade.
- Does not replace narration (Tier 2 / Gemma). Untouched.
- Does not require per-quest retraining. Must work for any quest text.

## 3. Architecture

### 3.1 Placement

The judge gains a third tier between rules and narration:

    Tier 1   rules_verdict()   deterministic PASS/FAIL   (authoritative)
    Tier 1.5 vision_gate()     CLIP similarity check      (gatekeeper, CPU)
    Tier 2   narrate()         Gemma line                 (narration only)

In `service.submit_evidence`, after the JPEGs are compressed/written and
after `rules_verdict` returns PASS, call `vision_gate(...)`. If the gate is
*confidently* wrong-photo, the verdict becomes FAIL with reason
"wrong photo". Otherwise proceed to narration unchanged.

### 3.2 Invariant (unchanged)

Tier 1 never depends on a model. Tier 1.5 may only turn a rules-PASS into a
FAIL; it may never turn a FAIL into a PASS and may never touch narration.
The core guarantee holds: a model cannot award or revoke XP by itself.

### 3.3 Model & runtime (Option B)

- Model: CLIP `clip-vit-base-patch32` via `transformers` (already installed).
- Runs on **CPU**, not the GPU. `gemma3:4b` keeps the 4GB GPU; the two
  models never contend for VRAM.
- Why CLIP: query-agnostic. It embeds the quest text and each photo into a
  shared space and scores similarity, so it generalizes to any quest with no
  retraining. Base size (~600MB) is enough for stone-vs-wall and keeps CPU
  latency to a few seconds.
- The quest objective text is embedded once per submission; each photo is
  embedded and compared against it.

### 3.4 Scoring & lenient threshold

- Each photo yields a similarity score (0-1) vs the quest objective.
- If any single photo scores below the rejection threshold *and* CLIP is
  confident, the whole submission is rejected ("wrong photo").
- **Lenient bias:** the rejection threshold is set LOW, so a photo is rejected
  only when its similarity is *confidently* low (clearly unrelated to the
  quest). Ambiguous/middle scores PASS. Doubt never costs a legitimate
  submission.
- Threshold is tunable via env `GRASS_VISION_THRESHOLD` (no code change).

### 3.5 Failure & fallback (must never break the game)

The vision tier is purely additive. Any failure degrades to current v1
behavior, never worse:

- Model missing / import error / CPU error / timeout -> gate skipped, verdict
  proceeds on rules alone. Logged, not fatal.
- `GRASS_MOCK=1` -> gate skipped (consistent with mock mode).
- `GRASS_VISION=on|off` env -> wholesale on/off switch for demo/debug.

Worst case of the new code == current shipped behavior.

## 4. Data flow

Phone captures photo -> uploads JPEG to laptop server ->
`submit_evidence` compresses + writes to `evidence/` ->
Tier 1 rules (count, freshness, size) -> if FAIL, reject (unchanged) ->
Tier 1.5 vision gate (CLIP, CPU; confident-mismatch -> reject; else pass) ->
Tier 2 narration (Gemma, GPU) -> verdict stored. Client request/response
unchanged; all server-side.

## 5. Env vars

| Var | Default | Meaning |
|-----|---------|---------|
| `GRASS_VISION` | `on` | `off` disables the tier entirely |
| `GRASS_VISION_THRESHOLD` | high (set in impl) | similarity cutoff for confident reject |
| `GRASS_MOCK` | unset | existing mock mode; also skips vision |

## 6. Testing strategy

- Unit (CLIP mocked): low score -> "wrong photo" reject; high/ambiguous ->
  pass. Threshold + lenient behavior asserted directly.
- Fallback: force model unavailable -> verdict matches current behavior
  (gate silently skipped).
- Integration: rules-valid photo with gate on -> downgrade only when
  confident, no change when unsure.
- Manual smoke: real stone vs wall images to sanity-check threshold; kept
  out of the automated suite (needs downloaded model).

## 7. Open items for implementation plan

- Exact default threshold value (calibrate against a few real images).
- Where the CLIP model is cached/downloaded on first run.
- Latency budget: confirm a few-second CPU pass is acceptable in
  `submit_evidence`'s request path (may need async or a warm model singleton).