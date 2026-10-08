# Week 1 Research Digest

Compiled 2026-10-09 via three parallel research agents (landscape/judging - engagement
mechanics - scope/feasibility). Source URLs inline. Status: input to final scope lock.

## 1. The competitive field (surprise: it is crowded)

Already submitted this week (tag #hf26challenge, Touch Grass):

- AI Outdoor Quest (Oct 6) - quest generator, cloud partner stack advertised, 1 commit,
  no photo/evidence, no PWA
- WildDex (Oct 8) - IRL Pokedex PWA on Render+MongoDB+Gemini(closed AI), XP/streaks,
  screen-photo guardrail, NO offline path, no tests, no penalties
- TouchGrass photo-judge (llama.cpp + Gemma 3 Vision) - server multiplayer judging,
  MockAiService fallback, always-online
- Touch Grass Not Glass (Elev) - Gemma structured output; KEY FINDING: describe before
  judge in the JSON schema (observation first, verdict second)
- Aminul's Touch Grass - on-device WebLLM (Qwen 1.5B) + CLIP ViT-B/32 WASM, offline SW
- Campus Quest, TerraPulse, Random Walk, Touchgrass.local, TouchGrass API - all
  "local LLM writes an outdoor plan" clones

WHAT IS STILL OPEN (our lane):
1. Consequences instead of encouragement: ranks F->S, demotion, penalties, regression
   token, Inner Demon, multi-persona roasts. No submission has a punitive System.
2. True offline-first evidence pipeline (photo -> IndexedDB queue -> reconnect ->
   batch judge). WildDex streams online-only; generator clones have no photos.
3. Zero accounts, zero cloud, zero keys. Rivals depend on Gemini/OpenRouter/cloud.
4. Test + mock-mode engineering credibility (rival repos: 0-1 commits, no tests).

## 2. What wins DEV/Hacktoberfest challenges (prior winners)

- Title = surprising claim + specific number, never a feature list
  (e.g. Keepalive: "I checked 328,186 tax returns")
- ONE idea deeply executed + a finding judges did not expect
- Live demo, zero friction, no signup; numbered 30-second try-it steps
- Radical honesty sections: "What is real and what is not", disclaiming a category
  you cannot prove working (still won overall)
- Partner tech must be load-bearing: "remove X and the app can no longer do Y"
- Reactions are only a tie-break: winning posts had 11-32 reactions - quality >> volume;
  publish early, reply to every comment
- AI-disclosure line: "AI assistance (Claude) used; design/analysis/verification mine"
- Judges praise: care taken, documentation, real working thing, graceful degradation,
  mock/fallback modes for demos

## 3. DO MORE (ranked by fun impact / build hours)

| # | Add-it | Fun | Hrs | Notes + sources |
|---|--------|-----|-----|-----------------|
| 1 | Regression token as PRE-EARNED equipped item (auto-equips weekly, visible on quest board) | 8 | 3 | Duolingo freezes: +0.38% DAU; weekend mercy +4% D14, -5% streak loss |
| 2 | Warm-guilt copy for any notification, inline actions, timing ~23.5h after last quest | 7 | 3 | ElliQ: guilt 3.5% but "berating" churn; warm reframe ~5%. iOS push is install-only + silent-push revocation - in-app + Android channels only |
| 3 | Inner Demon rage bar with goal-gradient framing: show "Days Survived: 12" not "5 days to demotion" | 9 | 4 | goal-gradient: progress-made beats progress-remaining; Habitica boss rage pattern |
| 4 | Constellation persona prompt pack: 5-8 hand-authored persona cards, name-priming, 3-example roast banks, markdown prompt sections, frequency penalty 0.3, 3-persona A/B on chosen model | 9 | 5 | Qwen2.5 documented LOW persona sensitivity (arxiv 2509.08484); test empirically |
| 5 | Quest generation: templates + slot-fill, Ollama JSON format constraint, 3-5 few-shot exemplars (+21.5% on 3B), retry-until-valid T=0.75 ~5s budget, randomized phrase fills | 9 | 5 | naive prompting = ~0% valid JSON on small models; SLOT: constrained decoding 99.5% schema acc |
| 6 | Generous-referee verdict: written reason + automatic re-shoot offer, never silent fail | 8 | 5 | Kleios pattern; proven by judges' comments on rival MockAiService |
| 7 | Offline evidence queue spine: IndexedDB + raw Blobs, client downscale ~200KB, persist(), QuotaExceeded handling, delete-after-sync | 10 | 6 | localStorage 5MB cap is a trap; Background Sync is Chromium-only |
| 8 | Demo hardening: deterministic mock judge mode, cached responses, demo script first, video | 9 | 4 | HackerEarth: judges decide in 30s; have cached LLM responses ready |
| 9 | Capture-in-app (no gallery upload) + EXIF freshness vs quest-issue time + cheap flatness/moire heuristic | 6 | 3 | BeReal: fresh-capture + no-roll; moiré = screen detection literature (USENIX sec21) |
| 10 | iOS/Android install coach overlay (Share-arrow tutorial after first quest) | 7 | 2 | no beforeinstallprompt on iOS; unlocks storage exemption + push |

Ethical-design line to state in the write-up: penalties apply only to EARNED status
(demoting an earned rank works; losing an unearned one does not - sagepub g4h study),
recovery always possible (regression token), never a "you failed" notification
(Silverman/Lerner streak research), cite Adrian Hon streak critique + ACM goal-drift
study as deliberate design conscience.

## 4. DO LESS / NEVER-IN-7-DAYS (cut list)

- Auth/accounts/sessions/email (the 11-flow iceberg) - local device profile only
- Multiplayer, leaderboards vs strangers, real-time, payments
- Native wrapper/App Store; iOS push as core loop; Background Sync API
- VLM judging as a HARD gate: qwen2.5vl:3b needs ~5GB runtime (does not fit 4GB);
  gemma3:4b vision ~3.3GB = only chance, tight; LLaVA-style negation failure >40%
- On-device CLIP tier-1 verdict: deprioritize (fun 4 / ~12h = 0.33; 350MB first-load;
  no solid evidence CLIP catches screens reliably; Aminul already ships it = less unique).
  Revisit post-challenge behind a flag
- Complex service worker (app-shell precache only, ~30 lines)
- localStorage for photos; cloud API keys as functional dependencies
- Fine-tuning, vector DBs, embeddings infra; long context (keep ctx <= 2-4k, KV cache VRAM)
- Perfect iOS camera UX (WebKit 282327 still open): ship <input type=file capture> fallback
- Over-testing: unit tests for Python judge + queue state machine only

## 5. AI judge - feasibility on RTX 2050 4GB (ranked)

1. Rules + metadata verdict (EXIF freshness, size/quality, queue integrity):
   0 VRAM, <100ms - CORE, instant, demo-proof
2. Text-only Ollama roast over structured data (gemma3:4b or qwen3-4b Q4):
   measured qwen3-4b = 17.2 tok/s, TTFT 1.5s => ~200-token roast 7-12s at sync
   (batched). NEED: OLLAMA_KEEP_ALIVE=-1, template fallback if model down - CORE AI
3. Caption-then-judge with gemma3:4b vision at sync (describe-before-judge schema):
   3.3-3.5GB VRAM tight; 8GB card measured 3.0s label / 8.3s caption => expect 10-30s+
   on 4GB; Ollama vision headroom bugs (#17099, #10161 OOM) - OPTIONAL bonus multiplier,
   never gates the loop; downscale <=1280px, retry, metadata fallback
4. On-device CLIP PWA verdict - post-challenge stretch (see cut list)

Verdict flow: instant rules verdict -> evidence queued -> at sync, text roast always,
VLM bonus if it works -> written reason + re-shoot offer -> penalties computed by CODE
never by the model.

## 6. Platform constraints (Android primary - our phone)

- Android Chrome PWA: camera reliable, push OK (channels), install prompt native
- iOS PWA: camera regression-prone in standalone (bug 282327), file-input fallback
  mandatory; push only installed + immediate-present or permission revoked; no install
  prompt; JS cookies capped 7d even installed (we have no auth - moot); storage exempt
  for installed PWAs; 7-day wipe for non-installed
- Queue: IndexedDB + Blob, navigator.storage.persist(), estimate() pre-check
- Sync: hand-rolled on online/visibilitychange events (no Background Sync)

## 7. Write-up plan (winning formula)

- Title: claim + number ("I made a local AI punish me for 7 days straight" style)
- Structure: provocative claim title -> personal lede -> What I Built (hard numbers)
  -> 30-second demo steps (no signup) -> repo -> How I Built It (real bugs, Ollama
  VRAM fights, persona A/B findings) -> "What is real and what is not" -> Prize
  Categories (Best Use of Gemma: gemma3:4b is the narrator+judge, load-bearing)
- Answer kate lindsay's "touch grass is a psyop - do what? for how long?" critique in
  one line: the System supplies exactly the missing "what/how long"
- Own the honesty: what the 4B model got wrong; punishment-backfire research cited as
  why regression token exists; AI assistance disclosed
- Local-first as the headline: "their apps die without wifi; ours dies without the
  outdoors" - photos/locations never leave the device
- Embed optional DevRelay session (rules-endorsed); publish early (Oct 10 morning),
  reply to every comment (tie-break runway)

## 8. Key sources (selection)

- Winner anatomy: dev.to/zkasuran (Keepalive), dev.to/devteam Generosity/Dog Days/Passion
  winner announcements
- Streak/penalty science: blog.duolingo.com streak posts; lerner.udel.edu; sagepub
  g4h.2021.0130 (earned-status loss aversion); sciencedirect S1071581918305135
  (Habitica inappropriate-punishment); uxdesign.cc streak exit-points
- Notifications: ElliQ case (eytanweinstein.com); pushwoosh; PMC10337295 (3.5x open lift)
- AI GM: arxiv 2502.19519 (ChatRPG narrator/archivist split); arxiv 2509.08484 (Qwen
  persona insensitivity); CALYPSO arxiv 2308.07540 (prompt hygiene); ACL 2026
  findings-acl.492 (few-shot +21.5% on 3B); EMNLP SLOT 2025.emnlp-industry.32
  (constrained decoding 99.5%)
- Anti-cheat: BeReal study sagepub 01634437231209420; USENIX sec21fall-cheng (moire);
  Kleios generous-referee pattern
- Feasibility: llm-bench.io rtx-2050 (qwen3-4b 17.2 tok/s); PhotoPrism VLM comparison;
  ollama issues 17099/10161/14312; transformers.js CLIP WASM benchmarks
- Scope failure: MLH how-to-win; lablab guide; otf-kit auth article; web.dev service
  worker guidance; TapPWA iOS limits 2026