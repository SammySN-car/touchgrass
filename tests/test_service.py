import io
import os
from datetime import date, timedelta

import pytest
from PIL import Image

import app.db as db
import app.service as service
import app.state as state


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Isolate on a temp database (env + DB_PATH) and init schema."""
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("GRASS_DB", str(db_file))
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setenv("GRASS_MOCK", "1")  # no network in tests
    monkeypatch.setattr(service, "EVIDENCE_DIR", tmp_path / "evidence")
    db.init_db()
    connection = db.connect()
    yield connection
    connection.close()


def tiny_jpeg(size=(120, 120)) -> bytes:
    """High-entropy RGB image so the compressed JPEG clears the 3000-byte floor."""
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def insert_quest(conn, day, *, target_count=1, xp_reward=40, deadline_hour=23):
    """Deterministic quest row inserted directly, bypassing template picking."""
    conn.execute(
        "INSERT INTO quests (day, template_id, title, objective, target_count,"
        " deadline_hour, xp_reward, difficulty, issued_at, flavor)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            day,
            "sky",
            "SKY CHECK",
            "Photograph the sky from outside. Windows are not outside.",
            target_count,
            deadline_hour,
            xp_reward,
            "D",
            f"{day}T08:00:00",
            "A quest line.",
        ),
    )
    conn.commit()
    return conn.execute("SELECT id FROM quests WHERE day = ?", (day,)).fetchone()["id"]


def test_submit_pass_flow(conn):
    day = "2026-11-05"
    quest_id = insert_quest(conn, day)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is True
    assert set(payload) == {
        "passed", "line", "model_used", "xp_delta", "rank_before", "rank_after",
        "streak", "events", "flavor",
    }
    st = service.load_state(conn)
    assert st.xp > 0
    assert st.streak == 1
    assert payload["xp_delta"] == st.xp
    assert payload["rank_before"] == "F" and payload["rank_after"] == "F"
    assert payload["line"] and payload["flavor"] == "A quest line."
    row = conn.execute("SELECT * FROM verdicts WHERE quest_id = ?", (quest_id,)).fetchone()
    assert row is not None and row["passed"] == 1
    assert row["xp_delta"] == payload["xp_delta"]
    assert (service.EVIDENCE_DIR / f"{quest_id:04d}_00.jpg").is_file()
    ev = conn.execute("SELECT * FROM evidence WHERE quest_id = ?", (quest_id,)).fetchone()
    assert ev is not None
    assert ev["path"] == str(service.EVIDENCE_DIR / f"{quest_id:04d}_00.jpg")


def test_submit_fail_flow(conn):
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=3)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is False
    st = service.load_state(conn)
    assert st.xp == 0  # floor
    assert st.streak == 0
    assert payload["xp_delta"] == 0
    assert payload["streak"] == 0
    row = conn.execute("SELECT passed FROM verdicts WHERE quest_id = ?", (quest_id,)).fetchone()
    assert row["passed"] == 0


def test_double_submit_raises(conn):
    day = "2026-11-05"
    quest_id = insert_quest(conn, day)
    cap = f"{day}T09:00:00"
    service.submit_evidence(conn, quest_id, [tiny_jpeg()], [cap])
    with pytest.raises(service.ServiceError, match="already adjudicated"):
        service.submit_evidence(conn, quest_id, [tiny_jpeg()], [cap])


def test_submit_unknown_quest_raises(conn):
    with pytest.raises(service.ServiceError, match="quest not found"):
        service.submit_evidence(conn, 999, [tiny_jpeg()], ["2026-11-05T09:00:00"])


def test_yesterday_ignored_demotes(conn):
    today = date(2026, 11, 5)
    yesterday = (today - timedelta(days=1)).isoformat()
    yid = insert_quest(conn, yesterday)  # no verdict -> ignored
    conn.execute("UPDATE player SET rank_index = 2, streak = 3 WHERE id = 1")
    conn.commit()
    quest = service.get_or_issue_today(conn, today.isoformat())
    assert quest is not None and quest["day"] == today.isoformat()
    st = service.load_state(conn)
    assert st.rank_index == 1  # demoted one step from 2
    assert st.streak == 0
    row = conn.execute("SELECT * FROM verdicts WHERE quest_id = ?", (yid,)).fetchone()
    assert row is not None
    assert row["model_used"] == "rules"
    assert row["passed"] == 0
    assert row["xp_delta"] == 0
    assert row["rank_before"] == state.RANKS[2]
    assert row["rank_after"] == state.RANKS[1]
    assert row["line"] == "No evidence. The System noted this."
    today_n = conn.execute(
        "SELECT COUNT(*) AS n FROM quests WHERE day = ?", (today.isoformat(),)
    ).fetchone()["n"]
    assert today_n == 1


def test_yesterday_passed_no_demotion(conn):
    today = date(2026, 11, 5)
    yesterday = (today - timedelta(days=1)).isoformat()
    yid = insert_quest(conn, yesterday)
    conn.execute(
        "INSERT INTO verdicts (quest_id, passed, xp_delta, rank_before, rank_after,"
        " streak_after, line, flavor, model_used, events_json, created_at)"
        " VALUES (?, 1, 45, 'F', 'F', 1, 'ok', '', 'canned', '[]', ?)",
        (yid, f"{yesterday}T20:00:00"),
    )
    conn.execute("UPDATE player SET rank_index = 2, streak = 1 WHERE id = 1")
    conn.commit()
    quest = service.get_or_issue_today(conn, today.isoformat())
    assert quest is not None and quest["day"] == today.isoformat()
    st = service.load_state(conn)
    assert st.rank_index == 2  # no demotion
    assert st.streak == 1
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM verdicts WHERE quest_id = ?", (yid,)
    ).fetchone()["n"]
    assert n == 1  # no synthetic verdict added


def test_no_yesterday_record_no_demotion(conn):
    today = date(2026, 11, 5)
    conn.execute("UPDATE player SET rank_index = 2 WHERE id = 1")
    conn.commit()
    quest = service.get_or_issue_today(conn, today.isoformat())
    assert quest is not None and quest["day"] == today.isoformat()
    st = service.load_state(conn)
    assert st.rank_index == 2
    n = conn.execute("SELECT COUNT(*) AS n FROM verdicts").fetchone()["n"]
    assert n == 0


def test_get_or_issue_today_idempotent(conn):
    today = date(2026, 11, 5).isoformat()
    first = service.get_or_issue_today(conn, today)
    second = service.get_or_issue_today(conn, today)
    assert first["id"] == second["id"]
    n = conn.execute("SELECT COUNT(*) AS n FROM quests").fetchone()["n"]
    assert n == 1


def test_get_log_newest_first(conn):
    d1, d2 = "2026-11-04", "2026-11-05"
    q1 = insert_quest(conn, d1)
    service.submit_evidence(conn, q1, [tiny_jpeg()], [f"{d1}T09:00:00"])
    q2 = insert_quest(conn, d2)
    service.submit_evidence(conn, q2, [tiny_jpeg()], [f"{d2}T09:00:00"])
    entries = service.get_log(conn)
    assert len(entries) == 2
    assert entries[0]["quest_day"] == d2
    assert entries[1]["quest_day"] == d1
    assert set(entries[0]) == {
        "quest_day", "title", "passed", "xp_delta", "line", "model_used", "created_at",
    }
    assert entries[0]["title"] == "SKY CHECK"
    assert entries[0]["passed"] == 1


def test_gate_downgrades_rules_pass_to_fail(conn, monkeypatch):
    from app import vision

    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setenv("GRASS_VISION", "on")
    # Force rules PASS: valid fresh jpeg, target_count=1
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)

    monkeypatch.setattr(service.judge, "vision_enabled", lambda: True)
    monkeypatch.setattr(vision, "get_scorer", lambda: (lambda text, imgs: [0.01]))
    monkeypatch.setattr(service.judge, "narrate", lambda q, m, p: ("line", "fake-model"))

    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is False
    row = conn.execute("SELECT passed FROM verdicts WHERE quest_id = ?", (quest_id,)).fetchone()
    assert row["passed"] == 0


def test_gate_not_consulted_when_rules_fail(conn, monkeypatch):
    from app import vision

    monkeypatch.setenv("GRASS_VISION", "on")
    day = "2026-11-05"
    # target_count=3 but only 1 photo -> rules FAIL (insufficient evidence)
    quest_id = insert_quest(conn, day, target_count=3)

    called = {"n": 0}

    def spy(text, imgs):
        called["n"] += 1
        return [0.99]

    monkeypatch.setattr(service.judge, "vision_enabled", lambda: True)
    monkeypatch.setattr(vision, "get_scorer", lambda: spy)

    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is False
    assert called["n"] == 0  # gate never reached


def test_existing_pass_flow_unchanged_with_gate_default(conn):
    # GRASS_VISION defaults to on, but no scorer is loaded in tests -> gate inert.
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is True


def test_gate_survives_scorer_load_failure(conn, monkeypatch):
    from app import vision

    monkeypatch.setenv("GRASS_VISION", "on")
    monkeypatch.delenv("GRASS_MOCK", raising=False)
    monkeypatch.setattr(vision, "get_scorer", lambda: None)  # load failed
    day = "2026-11-05"
    quest_id = insert_quest(conn, day, target_count=1)
    payload = service.submit_evidence(conn, quest_id, [tiny_jpeg()], [f"{day}T09:00:00"])
    assert payload["passed"] is True  # degraded to v1 behavior
