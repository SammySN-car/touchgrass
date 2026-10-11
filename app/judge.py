"""Judge: rules tier decides PASS/FAIL; the model tier may only narrate."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import app.ollama_client
from app.ollama_client import chat_json, mock_mode

CANNED_PASS = [
    "Evidence accepted. The System remains unimpressed.",
    "Compliance recorded. Do not expect congratulations.",
    "PASS. The System notes you are capable of the bare minimum.",
    "Accepted. Outside exists; try not to look so surprised.",
    "The System has logged your brief adventure outdoors.",
]
CANNED_PENALTY = [
    "PENALTY. This evidence does not meet the System's standards.",
    "Rejected. The System expected better; it usually does.",
    "FAIL. Consider this a learning opportunity. The System already has.",
    "The System reviewed your submission and chose disappointment.",
    "Penalty applied. The outdoors remains unimpressed.",
]

SYSTEM_PROMPT = (
    "You are THE SYSTEM: cold, bureaucratic outdoor quest authority. "
    "Dry humor. Never friendly, never obscene. Output JSON only."
)



def _as_aware(ts: str) -> datetime:
    """Parse an ISO-8601 stamp into a timezone-aware datetime.

    Handles naive (assumed server-local), trailing-Z (UTC), and numeric
    offset forms so client (UTC) and server (local) stamps compare correctly.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        # Naive stamps come from the server's local clock.
        dt = dt.astimezone()
    return dt


def rules_verdict(
    evidence_meta: list[dict],
    target_count: int,
    issued_at: str,
    deadline_hour: int,
    now: datetime | None = None,
) -> dict:
    """Authoritative tier: first failure wins; the model never overrides this."""
    if now is None:
        now = datetime.now()
    if len(evidence_meta) < target_count:
        return {"passed": False, "reason": "insufficient evidence"}
    if any(_as_aware(meta["captured_at"]) < _as_aware(issued_at)
           for meta in evidence_meta):
        return {"passed": False, "reason": "stale capture"}
    if now.hour > deadline_hour:
        return {"passed": False, "reason": "deadline exceeded"}
    if any(meta["size"] < 3000 for meta in evidence_meta):
        return {"passed": False, "reason": "evidence too small to be sincere"}
    return {"passed": True, "reason": "accepted"}


def _canned(passed: bool) -> tuple[str, str]:
    return (random.choice(CANNED_PASS if passed else CANNED_PENALTY), "canned")


def narrate(quest: dict, evidence_meta: list[dict], passed: bool) -> tuple[str, str]:
    """Model tier: may only narrate an already-decided verdict. A model line
    that contradicts `passed` (or is unusable) is discarded for a canned bank
    line; the rules verdict itself is never changed here."""
    if mock_mode():
        return _canned(passed)
    expected = "PASS" if passed else "PENALTY"
    system = SYSTEM_PROMPT
    user = (
        f'Quest: {quest.get("title", "")}. Verdict is {expected}. '
        f"Evidence count: {len(evidence_meta)}. "
        "Write one harsh-but-funny verdict line (<=30 words) in the System voice. "
        'Output JSON: {"verdict": "PASS" or "PENALTY", "line": string}'
    )
    try:
        result = chat_json(app.ollama_client.DEFAULT_MODEL, system, user)
    except Exception:
        return _canned(passed)
    if not isinstance(result, dict):
        return _canned(passed)
    verdict = result.get("verdict")
    line = result.get("line")
    if (
        isinstance(verdict, str)
        and verdict.upper() == expected
        and isinstance(line, str)
        and line.strip()
    ):
        return (line.strip(), app.ollama_client.DEFAULT_MODEL)
    return _canned(passed)
