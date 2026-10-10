"""SQLite storage layer: connection factory, schema DDL, idempotent init.

Database file location: ``GRASS_DB`` env override (absolute or relative path)
wins when set; otherwise ``DB_PATH``, which defaults to repo-root
``grass.db``. The env var is read at import time (to seed ``DB_PATH``) and
re-checked when ``connect()`` is called, so tests can redirect storage via
``monkeypatch.setenv`` or ``monkeypatch.setattr(db, "DB_PATH", ...)``.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY,
    xp INTEGER NOT NULL DEFAULT 0,
    rank_index INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    last_pass_day TEXT
);
CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY,
    day TEXT UNIQUE,
    template_id TEXT,
    title TEXT,
    objective TEXT,
    target_count INTEGER,
    deadline_hour INTEGER,
    xp_reward INTEGER,
    difficulty TEXT,
    issued_at TEXT,
    flavor TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    quest_id INTEGER REFERENCES quests(id),
    path TEXT,
    captured_at TEXT,
    uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY,
    quest_id INTEGER REFERENCES quests(id),
    passed INTEGER,
    xp_delta INTEGER,
    rank_before TEXT,
    rank_after TEXT,
    streak_after INTEGER,
    line TEXT,
    flavor TEXT,
    model_used TEXT,
    events_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS template_usage (
    template_id TEXT PRIMARY KEY,
    times_used INTEGER
);
"""

DB_PATH: Path = Path(
    os.environ.get("GRASS_DB")
    or Path(__file__).resolve().parent.parent / "grass.db"
)


def _active_db_path() -> Path:
    env = os.environ.get("GRASS_DB")
    return Path(env) if env else Path(DB_PATH)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_active_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables (IF NOT EXISTS) and seed the single player row. Idempotent."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO player (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()
