"""HTTP layer: FastAPI routes over the game service.

Connection-per-request via dependency (no lifespan in v1): each request
opens its own SQLite connection and closes it when done. Deps and handlers
are all ``async def`` so the connection is created and used on the same
(event-loop) thread — sqlite3 objects are thread-bound and Python 3.13
removed the settable ``check_same_thread`` attribute, while db.connect()
cannot pass connect() kwargs. The server never decides `passed` — that is
the service/judge contract; errors map ServiceError -> 409, ValueError -> 422.
"""
from __future__ import annotations

from datetime import datetime

from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import db, ollama_client, service, state

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


app = FastAPI(title="SYSTEM://GRASS")


@app.on_event("startup")
def _startup() -> None:
    """Ensure schema exists (idempotent) so a deleted/first-run DB self-heals."""
    db.init_db()


def _row_to_dict(row) -> dict | None:
    """Plain JSON-ready dict copy of a sqlite3.Row, or None."""
    return dict(row) if row is not None else None


@app.get("/api/state")
async def read_state(conn=Depends(get_conn)):
    day_str = datetime.now().date().isoformat()
    quest = service.get_or_issue_today(conn, day_str)  # side effect: issue + demotion
    st = service.load_state(conn)  # after demotion so player reflects it
    verdict = None
    if quest is not None:
        row = conn.execute(
            "SELECT * FROM verdicts WHERE quest_id = ? ORDER BY id DESC LIMIT 1",
            (quest["id"],),
        ).fetchone()
        verdict = _row_to_dict(row)
    return {
        "player": {
            "xp": st.xp,
            "rank": state.RANKS[st.rank_index],
            "streak": st.streak,
        },
        "quest": quest,
        "verdict": verdict,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


@app.post("/api/evidence")
async def post_evidence(
    quest_id: int = Form(...),
    captured_at: str = Form(...),
    files: list[UploadFile] = File(...),
    conn=Depends(get_conn),
):
    blobs = [await f.read() for f in files]
    captured_ats = [captured_at] * len(blobs)  # v1: one client stamp per batch
    try:
        return service.submit_evidence(conn, quest_id, blobs, captured_ats)
    except service.ServiceError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/api/log")
async def read_log(conn=Depends(get_conn)):
    return {"entries": service.get_log(conn)}


@app.get("/api/health")
async def read_health():
    mock = ollama_client.mock_mode()
    ollama = ollama_client.reachable() if not mock else False
    return {"ollama": ollama, "mock": mock, "model": ollama_client.DEFAULT_MODEL}


@app.get("/")
async def root():
    """Serve the single-page frontend from the API origin (same-origin, no CORS)."""
    return RedirectResponse("/index.html")


# Mount static LAST so /api/* routes above take precedence.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Also expose index.html + assets at root-relative paths the page expects.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="frontend")
