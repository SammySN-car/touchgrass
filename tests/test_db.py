import importlib
import sqlite3
from pathlib import Path

import pytest

import app.db as db


@pytest.fixture
def grass_db(tmp_path, monkeypatch):
    """Isolate the module on a temp database via the GRASS_DB env override."""
    db_file = tmp_path / "grass.db"
    monkeypatch.setenv("GRASS_DB", str(db_file))
    return db_file


def test_grass_db_env_override_used_at_call_time(grass_db):
    conn = db.connect()
    conn.close()
    assert grass_db.exists()


def test_db_path_default_is_repo_root_grass_db(monkeypatch):
    monkeypatch.delenv("GRASS_DB", raising=False)
    importlib.reload(db)
    assert db.DB_PATH == Path(db.__file__).resolve().parent.parent / "grass.db"


def test_connect_sets_row_factory(grass_db):
    conn = db.connect()
    try:
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_init_db_creates_all_tables(grass_db):
    db.init_db()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = {row["name"] for row in rows}
    assert {"player", "quests", "evidence", "verdicts", "template_usage"} <= names


def test_player_row_seeded_with_defaults(grass_db):
    db.init_db()
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM player").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["xp"] == 0
    assert row["rank_index"] == 0
    assert row["streak"] == 0
    assert row["last_pass_day"] is None


def test_row_factory_access_by_column_name(grass_db):
    db.init_db()
    conn = db.connect()
    try:
        row = conn.execute("SELECT xp, streak FROM player WHERE id = 1").fetchone()
        assert row["xp"] == 0
        assert list(row.keys()) == ["xp", "streak"]
    finally:
        conn.close()


def test_init_db_is_idempotent(grass_db):
    db.init_db()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO quests (day, template_id, title)"
            " VALUES ('2026-10-11', 't1', 'Walk')"
        )
        conn.commit()
    finally:
        conn.close()
    db.init_db()  # second run must not raise, duplicate, or wipe data
    conn = db.connect()
    try:
        player_count = conn.execute("SELECT COUNT(*) AS n FROM player").fetchone()["n"]
        quest_count = conn.execute("SELECT COUNT(*) AS n FROM quests").fetchone()["n"]
    finally:
        conn.close()
    assert player_count == 1
    assert quest_count == 1
