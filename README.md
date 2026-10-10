# SYSTEM://GRASS

A **local-first outdoor quest game**. An AI "System" assigns you one outdoor
micro-quest per day, judges your photo evidence, narrates the verdict, and
tracks your rank from F to S - with real penalties for ignoring it.

Built for the **Hacktoberfest 2026 Open-Source AI Challenge: Week 1
("Touch Grass")**. Submission post tagged `#hf26challenge`.

## What makes it different

- **100% local.** Quest generation and judgment run on your own machine via
  Ollama (Gemma). No cloud, no accounts, no API keys. Your photos never leave
  your laptop.
- **Phone = terminal.** A mobile web app on your LAN captures evidence; the
  brain stays home. The screen is the shortest part of the experience.
- **Consequences, not confetti.** Ranks, XP, streaks, demotions. The System
  is harsh but recovery is always one quest away.
- **System-window aesthetic.** Isekai-inspired interface. Original names and
  lore - not affiliated with any rights holder.

## Docs

- [docs/design.md](docs/design.md) - frozen v1 design
- [docs/research.md](docs/research.md) - competitive/technical research
- [docs/concept.md](docs/concept.md) - original concept notes
- [docs/challenge-week1.md](docs/challenge-week1.md) - challenge brief

## Run (dev)

```
ollama pull gemma3:4b
pip install fastapi uvicorn pillow
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<your-laptop-ip>:8000` on your phone (same Wi-Fi).

## License

MIT - see [LICENSE](LICENSE).