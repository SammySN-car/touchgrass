# Concept: The System (working title)

Hacktoberfest 2026 Week 1 "Touch Grass" entry - see `challenge-week1.md`.
Status: brainstorming. Architecture recommendation under review.

## The idea

An AI "System" (inspired by the isekai system-window trope; **no Solo Leveling
IP - original names, art, and lore only**) that assigns daily real-world outdoor
quests and ruthlessly punishes you for ignoring them.

Built for someone who does not go outside: motivation comes from penalty,
not inspiration.

### Core loop

1. The System posts a daily quest (find something alive, record three sounds,
   reach somewhere before sunset, photo evidence required).
2. Player steps out with phone as a dumb sensor - camera snaps, audio records,
   screen stays off otherwise.
3. Open AI judges the evidence: local LLM writes quest text, roasts failures,
   narrates rank-ups; an open vision/audio model verifies photos/sounds
   ("that is a photo of a screen - PENALTY").
4. Compliance raises rank (F-rank -> S-rank). Ghosting a quest demotes you,
   resets streaks, and the narrator respects you less.

### Why open-source AI is the core, not an accessory

No open models = no game. The quest writer, the judge, and the narrator are all
local open-weight models (Ollama + Piper TTS + open vision/audio models). Costs
nothing, works with no signal, photos/location never leave the machine.

## Decisions so far

- Tone: **harsh System** - real penalties, rank demotion, roast narration
- Phone: PWA (camera + quest board + audio) - no app-store, no mobile-dev
  rabbit hole; capture outside, AI brain at home
- AI placement: **hybrid** (recommended, pending approval) - quests/audio
  pre-generated to play offline; instant "evidence logged" ack; full AI tribunal
  on sync; on-device model as stretch goal
- Constraints: 7-day build, single-player MVP (friend duels = later), one
  entry, DEV write-up with #hf26challenge
- Open: final name, quest catalog, verdict model choice, phone OS/RAM check

## Non-goals

- No Pokémon or Solo Leveling assets/names
- No GPS map / location tracking v1 (privacy story: everything stays local)
- No app-store distribution