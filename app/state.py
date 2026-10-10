"""Pure state machine: ranks, XP, streaks, penalties. No I/O."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

RANKS = ("F", "E", "D", "C", "B", "A", "S")
THRESHOLDS = (0, 100, 250, 500, 850, 1300, 2000)

FAIL_XP_PENALTY = 30
STREAK_BONUS_PER_DAY = 5
STREAK_BONUS_CAP = 25
PERFECT_BONUS = 20


def derived_rank_index(xp: int) -> int:
    """Highest rank index the XP total affords."""
    idx = 0
    for i, t in enumerate(THRESHOLDS):
        if xp >= t:
            idx = i
    return idx


@dataclass(frozen=True)
class PlayerState:
    xp: int = 0
    rank_index: int = 0
    streak: int = 0
    last_pass_day: date | None = None

    @property
    def rank(self) -> str:
        return RANKS[self.rank_index]


@dataclass(frozen=True)
class Event:
    kind: str  # RANK_UP | RANK_DOWN | STREAK_BROKEN | STREAK_CONTINUED | XP_GAINED | XP_LOST
    detail: str


def streak_bonus(streak: int) -> int:
    return min(streak * STREAK_BONUS_PER_DAY, STREAK_BONUS_CAP)


def apply_pass(state: PlayerState, base_xp: int, day: date, perfect: bool = False
               ) -> tuple[PlayerState, list[Event]]:
    """Quest passed. Streak continues if yesterday was a pass, else restarts."""
    if base_xp < 0:
        raise ValueError("base_xp must be >= 0")
    continued = state.last_pass_day is not None and (day - state.last_pass_day).days == 1
    new_streak = state.streak + 1 if continued else 1
    gain = base_xp + streak_bonus(new_streak) + (PERFECT_BONUS if perfect else 0)
    new_xp = state.xp + gain
    events = [Event("XP_GAINED", f"+{gain} XP")]
    new_rank_idx = max(state.rank_index, derived_rank_index(new_xp))
    if new_rank_idx > state.rank_index:
        events.append(Event("RANK_UP", f"{RANKS[state.rank_index]} -> {RANKS[new_rank_idx]}"))
    return replace(state, xp=new_xp, rank_index=new_rank_idx,
                   streak=new_streak, last_pass_day=day), events


def apply_fail(state: PlayerState) -> tuple[PlayerState, list[Event]]:
    """Evidence rejected: lose XP, streak dies. Rank may drop if XP crosses down
    only via explicit demotion (rank never falls from XP loss alone)."""
    new_xp = max(0, state.xp - FAIL_XP_PENALTY)
    events = [Event("XP_LOST", f"-{state.xp - new_xp} XP")]
    if state.streak > 0:
        events.append(Event("STREAK_BROKEN", f"streak {state.streak} -> 0"))
    return replace(state, xp=new_xp, streak=0, last_pass_day=None), events


def apply_ignore(state: PlayerState) -> tuple[PlayerState, list[Event]]:
    """Quest ignored entirely: one full rank demotion, streak dies."""
    new_idx = max(0, state.rank_index - 1)
    events: list[Event] = []
    if new_idx < state.rank_index:
        events.append(Event("RANK_DOWN", f"{RANKS[state.rank_index]} -> {RANKS[new_idx]}"))
    if state.streak > 0:
        events.append(Event("STREAK_BROKEN", f"streak {state.streak} -> 0"))
    return replace(state, rank_index=new_idx, streak=0, last_pass_day=None), events