"""Quest engine: template pool, anti-repeat picking, flavor, daily issuance.

All template rows and canned flavor lines are binding data (Task 4 brief)
and must not be edited without updating the spec. ``issue_quest_for_day``
is idempotent per ISO day: a second call for the same day returns the
stored row without re-picking a template or regenerating flavor.
"""
from __future__ import annotations

import random
from datetime import datetime

import app.db as db
import app.ollama_client as ollama_client
from app.ollama_client import chat_json, mock_mode

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

_SYSTEM_PROMPT = (
    "You are THE SYSTEM: cold, bureaucratic outdoor quest authority. "
    "Dry humor. Never friendly, never obscene. Output JSON only."
)


def _shape_weight(target_count: int) -> int:
    """Multi-photo 'collect N things' templates (target_count > 1) count double
    so they are issued ~2x less often than single-photo quests. Picking stays
    least-used on the weighted score: unused templates still score 0, so the
    14-day no-repeat rotation guarantee is unchanged."""
    return 2 if target_count > 1 else 1


def pick_template(conn) -> tuple:
    """Shape-weighted least-used bias: score each template as
    times_used * shape_weight (missing = 0 uses), find the minimum score,
    choose uniformly at random among tied templates."""
    rows = conn.execute(
        "SELECT template_id, times_used FROM template_usage"
    ).fetchall()
    usage = {row["template_id"]: row["times_used"] for row in rows}
    scores = {
        tpl[0]: usage.get(tpl[0], 0) * _shape_weight(tpl[3]) for tpl in TEMPLATES
    }
    min_score = min(scores.values())
    candidates = [tpl for tpl in TEMPLATES if scores[tpl[0]] == min_score]
    return random.choice(candidates)


def gemma_flavor(title: str, objective: str) -> tuple[str, str]:
    """Return (flavor line, model_used). Mock mode, any chat_json exception,
    or an unusable response falls back to a canned line with 'canned'."""
    if mock_mode():
        return random.choice(CANNED_FLAVOR), "canned"
    model_used = ollama_client.DEFAULT_MODEL
    user = (
        f"Quest: {title}. Objective: {objective}. "
        "Write one flavor line (<=20 words) in the System voice announcing "
        'this quest. Output JSON: {"flavor": string}'
    )
    try:
        result = chat_json(model_used, _SYSTEM_PROMPT, user)
        line = result.get("flavor") if isinstance(result, dict) else None
        if isinstance(line, str) and line.strip():
            return line, model_used
    except Exception:
        pass
    return random.choice(CANNED_FLAVOR), "canned"


def issue_quest_for_day(conn, day: str) -> dict | None:
    """Issue the quest for ISO ``day`` (YYYY-MM-DD). Idempotent: an existing
    row for that day is returned as-is (no new insert, no flavor regeneration)."""
    existing = conn.execute(
        "SELECT * FROM quests WHERE day = ?", (day,)
    ).fetchone()
    if existing is not None:
        return dict(existing)

    template_id, difficulty, xp, target_count, deadline_hour, title, objective = (
        pick_template(conn)
    )
    issued_at = datetime.now().isoformat(timespec="seconds")
    flavor, _model_used = gemma_flavor(title, objective)
    conn.execute(
        "INSERT INTO quests (day, template_id, title, objective, target_count,"
        " deadline_hour, xp_reward, difficulty, issued_at, flavor)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (day, template_id, title, objective, target_count, deadline_hour,
         xp, difficulty, issued_at, flavor),
    )
    conn.execute(
        "INSERT OR IGNORE INTO template_usage (template_id, times_used)"
        " VALUES (?, 0)",
        (template_id,),
    )
    conn.execute(
        "UPDATE template_usage SET times_used = times_used + 1"
        " WHERE template_id = ?",
        (template_id,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM quests WHERE day = ?", (day,)).fetchone()
    return dict(row) if row is not None else None
