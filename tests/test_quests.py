import pytest

import app.db as db
import app.quests as quests


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Isolate on a temp database (env + DB_PATH) and init schema."""
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("GRASS_DB", str(db_file))
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setenv("GRASS_MOCK", "1")  # no network in tests
    db.init_db()
    connection = db.connect()
    yield connection
    connection.close()


def test_issue_is_idempotent_per_day(conn):
    first = quests.issue_quest_for_day(conn, "2026-11-01")
    second = quests.issue_quest_for_day(conn, "2026-11-01")
    assert first["id"] == second["id"]
    row_count = conn.execute("SELECT COUNT(*) AS n FROM quests").fetchone()["n"]
    assert row_count == 1


def test_anti_repeat_prefers_unused(conn, monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    used = []
    for day_num in range(1, 15):
        quest = quests.issue_quest_for_day(conn, f"2026-11-{day_num:02d}")
        used.append(quest["template_id"])
    assert len(set(used)) == 14
    assert set(used) == {tpl[0] for tpl in quests.TEMPLATES}


def test_canned_flavor_in_mock(conn, monkeypatch):
    monkeypatch.setenv("GRASS_MOCK", "1")
    quest = quests.issue_quest_for_day(conn, "2026-11-01")
    assert quest["flavor"] in quests.CANNED_FLAVOR
