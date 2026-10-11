"""Game service: orchestrates daily quests, evidence, judging, and player state.

The only layer that writes player state. Buffered (late) submits share the
exact same code path as in-session ones — the server cannot tell them apart.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from app import db, state, quests, judge, images

log = logging.getLogger(__name__)

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"


class ServiceError(Exception):
    """Domain rejection the API maps to HTTP (currently 409)."""


def load_state(conn) -> state.PlayerState:
    row = conn.execute("SELECT * FROM player WHERE id = 1").fetchone()
    return state.PlayerState(
        xp=row["xp"],
        rank_index=row["rank_index"],
        streak=row["streak"],
        last_pass_day=(
            date.fromisoformat(row["last_pass_day"]) if row["last_pass_day"] else None
        ),
    )


def save_state(conn, st: state.PlayerState) -> None:
    conn.execute(
        "UPDATE player SET xp = ?, rank_index = ?, streak = ?, last_pass_day = ?"
        " WHERE id = 1",
        (
            st.xp,
            st.rank_index,
            st.streak,
            st.last_pass_day.isoformat() if st.last_pass_day else None,
        ),
    )


def _events_json(events: list[state.Event]) -> str:
    return json.dumps([{"kind": e.kind, "detail": e.detail} for e in events])


def get_or_issue_today(conn, day: str) -> dict:
    """Issue the quest for ``day`` (idempotent), demoting for an unjudged
    yesterday first — but only when today's quest does not yet exist."""
    today_exists = conn.execute(
        "SELECT id FROM quests WHERE day = ?", (day,)
    ).fetchone()
    if today_exists is None:
        yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        yrow = conn.execute(
            "SELECT id FROM quests WHERE day = ?", (yesterday,)
        ).fetchone()
        if yrow is not None:
            judged = conn.execute(
                "SELECT id FROM verdicts WHERE quest_id = ?", (yrow["id"],)
            ).fetchone()
            if judged is None:
                old = load_state(conn)
                st2, events = state.apply_ignore(old)
                save_state(conn, st2)
                conn.execute(
                    "INSERT INTO verdicts (quest_id, passed, xp_delta, rank_before,"
                    " rank_after, streak_after, line, flavor, model_used, events_json,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        yrow["id"],
                        0,
                        0,
                        state.RANKS[old.rank_index],
                        state.RANKS[st2.rank_index],
                        st2.streak,
                        "No evidence. The System noted this.",
                        "",
                        "rules",
                        _events_json(events),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
    return quests.issue_quest_for_day(conn, day)


def submit_evidence(
    conn, quest_id: int, files: list[bytes], captured_ats: list[str]
) -> dict:
    """Adjudicate a submission atomically: one commit at the end, rollback on
    any error. ``ValueError`` from ``images.compress_jpeg`` propagates (the
    API maps it to 422); an already-judged quest raises ``ServiceError`` (409)."""
    try:
        row = conn.execute("SELECT * FROM quests WHERE id = ?", (quest_id,)).fetchone()
        if row is None:
            raise ServiceError("quest not found")
        quest = dict(row)
        already = conn.execute(
            "SELECT id FROM verdicts WHERE quest_id = ?", (quest_id,)
        ).fetchone()
        if already is not None:
            raise ServiceError("already adjudicated")
        EVIDENCE_DIR.mkdir(exist_ok=True)
        meta: list[dict] = []
        for i, (blob, cap) in enumerate(zip(files, captured_ats)):
            jpg = images.compress_jpeg(blob)
            path = EVIDENCE_DIR / f"{quest_id:04d}_{i:02d}.jpg"
            path.write_bytes(jpg)
            conn.execute(
                "INSERT INTO evidence (quest_id, path, captured_at, uploaded_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    quest_id,
                    str(path),
                    cap,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            meta.append({"captured_at": cap, "size": len(jpg)})
        verdict = judge.rules_verdict(
            meta, quest["target_count"], quest["issued_at"], quest["deadline_hour"]
        )
        passed = bool(verdict["passed"])
        if passed and judge.vision_enabled():
            from app import vision
            scorer = vision.get_scorer()
            if scorer is not None:
                jpgs = [p.read_bytes() for p in sorted(EVIDENCE_DIR.glob(f"{quest_id:04d}_*.jpg"))]
                v_passed, v_reason = judge.vision_gate(quest, jpgs, scorer)
                if not v_passed:
                    passed = False
                    log.warning(
                        "vision gate downgraded quest %s to FAIL: %s",
                        quest_id,
                        v_reason,
                    )
        line, model_used = judge.narrate(quest, meta, passed)
        st = load_state(conn)
        if passed:
            st2, events = state.apply_pass(
                st, quest["xp_reward"], date.fromisoformat(quest["day"])
            )
        else:
            st2, events = state.apply_fail(st)
        xp_delta = st2.xp - st.xp
        save_state(conn, st2)
        conn.execute(
            "INSERT INTO verdicts (quest_id, passed, xp_delta, rank_before,"
            " rank_after, streak_after, line, flavor, model_used, events_json,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quest_id,
                int(passed),
                xp_delta,
                state.RANKS[st.rank_index],
                state.RANKS[st2.rank_index],
                st2.streak,
                line,
                quest.get("flavor", ""),
                model_used,
                _events_json(events),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "passed": passed,
        "line": line,
        "model_used": model_used,
        "xp_delta": xp_delta,
        "rank_before": state.RANKS[st.rank_index],
        "rank_after": state.RANKS[st2.rank_index],
        "streak": st2.streak,
        "events": [{"kind": e.kind, "detail": e.detail} for e in events],
        "flavor": quest.get("flavor", ""),
    }


def get_log(conn) -> list[dict]:
    """Newest-first verdict history, capped at 50 entries."""
    rows = conn.execute(
        "SELECT q.day AS quest_day, q.title, v.passed, v.xp_delta, v.line,"
        " v.model_used, v.created_at"
        " FROM verdicts v JOIN quests q ON q.id = v.quest_id"
        " ORDER BY v.id DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]
