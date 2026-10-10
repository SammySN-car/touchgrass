import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.db as db
import app.ollama_client as ollama_client
import app.service as service


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolate on a temp database (env + DB_PATH), mock Ollama, redirect
    evidence writes, then build the FastAPI test client."""
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("GRASS_DB", str(db_file))
    monkeypatch.setattr(db, "DB_PATH", db_file)
    monkeypatch.setenv("GRASS_MOCK", "1")  # no network in tests
    monkeypatch.setattr(service, "EVIDENCE_DIR", tmp_path / "evidence")
    db.init_db()
    from app.main import app

    return TestClient(app)


def tiny_jpeg(size=(120, 120)) -> bytes:
    """High-entropy RGB image so the compressed JPEG clears the 3000-byte floor."""
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def submit(client, quest_id, captured_at, blob=None):
    if blob is None:
        blob = tiny_jpeg()
    return client.post(
        "/api/evidence",
        data={"quest_id": str(quest_id), "captured_at": captured_at},
        files=[("files", ("photo.jpg", blob, "image/jpeg"))],
    )


def test_state_issues_quest_and_returns_player(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert body["player"]["xp"] == 0
    assert body["player"]["rank"] == "F"
    assert body["player"]["streak"] == 0
    quest = body["quest"]
    assert quest is not None
    assert quest["title"]
    assert quest["objective"]
    assert quest["target_count"] >= 1
    assert body["verdict"] is None
    assert body["server_time"]


def test_state_idempotent_same_day(client):
    first = client.get("/api/state").json()["quest"]["id"]
    second = client.get("/api/state").json()["quest"]["id"]
    assert first == second


def test_evidence_submit_happy_path(client):
    body = client.get("/api/state").json()
    quest = body["quest"]
    r = submit(client, quest["id"], quest["issued_at"])
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload["passed"], bool)
    assert payload["line"]
    assert isinstance(payload["xp_delta"], int)
    assert payload["rank_before"] == "F"
    assert payload["rank_after"] == "F"
    assert isinstance(payload["streak"], int)
    assert isinstance(payload["events"], list)
    assert "flavor" in payload


def test_evidence_double_submit_409(client):
    body = client.get("/api/state").json()
    quest = body["quest"]
    assert submit(client, quest["id"], quest["issued_at"]).status_code == 200
    r2 = submit(client, quest["id"], quest["issued_at"])
    assert r2.status_code == 409
    assert "already adjudicated" in r2.json()["detail"]


def test_evidence_undecodable_422(client):
    body = client.get("/api/state").json()
    quest = body["quest"]
    r = submit(client, quest["id"], quest["issued_at"], blob=b"garbage")
    assert r.status_code == 422


def test_log_endpoint(client):
    body = client.get("/api/state").json()
    quest = body["quest"]
    assert submit(client, quest["id"], quest["issued_at"]).status_code == 200
    r = client.get("/api/log")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) >= 1
    assert set(entries[0]) == {
        "quest_day", "title", "passed", "xp_delta", "line", "model_used", "created_at",
    }
    assert entries[0]["quest_day"] == quest["day"]


def test_health_mock(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {
        "ollama": False, "mock": True, "model": ollama_client.DEFAULT_MODEL,
    }
