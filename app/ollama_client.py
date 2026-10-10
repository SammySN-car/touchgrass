"""Ollama HTTP client: the only module allowed to talk to Ollama."""
from __future__ import annotations

import json
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("GRASS_MODEL", "gemma3:4b")
CANDIDATE_MODELS = [DEFAULT_MODEL, "llama3.2", "qwen2.5:7b"]


def mock_mode() -> bool:
    return os.environ.get("GRASS_MOCK") == "1"


def _fallback_chain(model: str) -> list[str]:
    return [model] + [c for c in CANDIDATE_MODELS if c != model]


def chat_json(model: str, system: str, user: str, timeout: int = 90) -> dict:
    """POST /api/chat with format=json; on any failure try the next candidate
    model; if every candidate fails, raise RuntimeError listing each failure."""
    attempts: list[str] = []
    for m in _fallback_chain(model):
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
        except Exception as exc:
            attempts.append(f"{m}: {str(exc)[:120]}")
    raise RuntimeError(" | ".join(attempts))


def reachable() -> bool:
    try:
        requests.get(OLLAMA_URL + "/api/tags", timeout=2)
    except Exception:
        return False
    return True
