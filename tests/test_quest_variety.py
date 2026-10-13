import pytest
from datetime import date, timedelta

import app.quests as quests


def test_multi_photo_templates_are_picked_less_often(conn):
    """Shape weighting: collect quests (target_count>1) must stay well below
    their pool share (6/14=43%) so a play week has ~2 not ~3-4."""
    day = date(2026, 12, 1)
    picks = []
    for _ in range(140):
        q = quests.issue_quest_for_day(conn, day.isoformat())
        picks.append(q["template_id"])
        day += timedelta(days=1)
    tpl_by_id = {t[0]: t for t in quests.TEMPLATES}
    multi = sum(1 for pid in picks if tpl_by_id[pid][3] > 1)
    # weighting target ~27%; unweighted baseline ~43%
    assert multi / len(picks) < 0.36, f"multi share {multi/len(picks):.2f} too high"
    # every template still rotates (no starvation)
    assert len(set(picks)) == 14


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Isolate on a temp database (env + DB_PATH) and init schema."""
    import app.db as db
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("GRASS_DB", str(db_file))
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setenv("GRASS_MOCK", "1")
    db.init_db()
    connection = db.connect()
    yield connection
    connection.close()