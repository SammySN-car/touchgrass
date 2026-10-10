# SYSTEM://GRASS - Design (v1 freeze)

Hacktoberfest 2026 Week 1 "Touch Grass" entry. Frozen 2026-10-11 01:45 IST.
Working title; rename allowed. Isekai-system-window inspired. Original lore;
not affiliated with any rights holder.

## 1. Fantasy

A harsh-but-funny AI System issues daily outdoor micro-quests, judges photo
evidence, narrates verdicts, and punishes ignoring it with demotion. The phone
is a dumb terminal; the brain runs at home on a laptop (local Ollama, Gemma).
Screen time = seconds; the System does the thinking.

## 2. Daily loop

1. Open app -> SYSTEM WINDOW shows today's quest (generated at first open).
2. Go do it outdoors. Phone stays in pocket.
3. Snap 1-3 evidence photos in-app, submit over LAN.
4. Rules verdict (instant) + System narration via Gemma (~10s).
5. PASS: +XP, streak, possible RANK UP. FAIL/IGNORE: -XP, streak reset,
   possible demotion. Tomorrow: a new quest. The System remembers.

## 3. Quest system

- One quest per day. Template pool (12-15 hand-written) + Gemma slot-fill.
- Generation: Ollama structured-output (JSON schema), 3-5 few-shot exemplars,
  retry-until-valid, temperature ~0.75. Canned fallback if model unavailable.
- Template provides structure; the model only personalizes flavor text.
- Quest fields: id, day, title (system-window text), objective,
  target_count, deadline (local hour), xp_reward, penalty, difficulty (D/C/B).
- Content: outdoor micro-missions, e.g. "Find 3 living things. Photograph
  each." / "Walk to the end of your lane; photograph something you never
  noticed."
- Anti-repeat: track template usage.

## 4. Evidence

- Photos only (audio = post-challenge). 1-3 per quest.
- Captured in-app (file input / camera), compressed client-side (~200KB).
- Uploaded to local server over LAN. No cloud, no accounts, nothing leaves
  the laptop.
- Freshness rule: capture timestamp must be >= quest issue timestamp.

## 5. Judge (two tiers)

- Tier 1 rules (instant, deterministic): photo count, freshness, size sanity
  -> skeleton PASS/FAIL. Never depends on the model.
- Tier 2 narration (Ollama Gemma, text-only): quest text + evidence metadata
  -> JSON {verdict, line, flavor}. Persona prompt + few-shot bank.
- Fallback: canned narration bank (also serves as judge-proof demo mode).
- v1 does NOT understand pixels (4GB VRAM constraint) - owned honestly in
  the write-up. Roadmap: on-device CLIP tier.

## 6. Ranks, XP, penalties

- Ranks: F(0) E(100) D(250) C(500) B(850) A(1300) S(2000).
- Pass: +40..80 XP by difficulty; streak +5/day capped +25; perfect +20.
- Fail: -30 XP + streak reset. No evidence by deadline: -1 rank step.
- Recovery always possible by playing. No death spiral.
- Post-challenge roadmap: regression token, Inner Demon, shop, gate raids,
  duels, persona pack, on-device verdicts.

## 7. Persona

One persona in v1: THE SYSTEM - cold, bureaucratic, dry, disappointed but
attentive. Narration is a feature, not the marketed identity. Rank-up moments
get full-width system-window drama.

## 8. Stack

- Python 3.13 + FastAPI + SQLite; Ollama client (gemma3:4b primary,
  canned fallback).
- State machine (ranks/XP/streak/penalty): pure Python module, pytest covered.
- Frontend: mobile-first vanilla JS/CSS, 4 screens (Quest, Evidence, Verdict,
  Log). No build tooling. Served by FastAPI on the laptop; phone uses
  http://<laptop-ip>:8000 on the same Wi-Fi.

## 9. Submission

- Demo = video (rules allow video or deploy). Repo + tests included.
- DEV post: template + #hf26challenge, claim+number title, why-open-matters
  section, Gemma category mapping, honesty section, AI-assistance disclosure.