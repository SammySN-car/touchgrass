from datetime import date, timedelta

from app.state import (PlayerState, apply_fail, apply_ignore, apply_pass,
                       derived_rank_index, streak_bonus)


def test_derived_rank_boundaries():
    assert derived_rank_index(0) == 0
    assert derived_rank_index(99) == 0
    assert derived_rank_index(100) == 1
    assert derived_rank_index(1999) == 5
    assert derived_rank_index(2000) == 6
    assert derived_rank_index(99999) == 6


def test_pass_first_quest():
    s, ev = apply_pass(PlayerState(), 50, date(2026, 10, 11))
    assert s.xp == 55  # 50 + streak 1*5
    assert s.streak == 1
    assert s.rank == "F"
    assert any(e.kind == "XP_GAINED" for e in ev)


def test_streak_continues_next_day():
    s, _ = apply_pass(PlayerState(), 50, date(2026, 10, 11))
    s, ev = apply_pass(s, 50, date(2026, 10, 12))
    assert s.streak == 2
    assert s.xp == 50 + 5 + (50 + 10)


def test_streak_resets_if_day_skipped():
    s, _ = apply_pass(PlayerState(), 50, date(2026, 10, 11))
    s, _ = apply_pass(s, 50, date(2026, 10, 13))  # gap of 2 days
    assert s.streak == 1


def test_streak_bonus_caps_at_25():
    assert streak_bonus(5) == 25
    assert streak_bonus(99) == 25


def test_rank_up_event_and_ceiling():
    s = PlayerState(xp=95, rank_index=0)
    s, ev = apply_pass(s, 100, date(2026, 10, 11))
    assert s.rank == "E"
    assert any(e.kind == "RANK_UP" and "F -> E" in e.detail for e in ev)
    s, ev = apply_pass(s, 100, date(2026, 10, 12))
    assert s.rank == "D"  # 95+105=200 (E); 200+110=310 -> D
    assert any(e.kind == "RANK_UP" and "E -> D" in e.detail for e in ev)


def test_rank_ceiling_is_s():
    s = PlayerState(xp=19_000, rank_index=6)
    s, ev = apply_pass(s, 500, date(2026, 10, 11))
    assert s.rank == "S"
    assert not any(e.kind == "RANK_UP" for e in ev)


def test_fail_loses_xp_and_streak():
    s = PlayerState(xp=10, rank_index=0, streak=3, last_pass_day=date(2026, 10, 10))
    s, ev = apply_fail(s)
    assert s.xp == 0
    assert s.streak == 0
    assert any(e.kind == "STREAK_BROKEN" for e in ev)


def test_fail_never_goes_negative():
    s, _ = apply_fail(PlayerState(xp=5))
    assert s.xp == 0


def test_ignore_demotes_one_step():
    s = PlayerState(xp=300, rank_index=2, streak=4)
    s, ev = apply_ignore(s)
    assert s.rank == "E"
    assert s.xp == 300  # xp untouched; the scar is the rank
    assert any(e.kind == "RANK_DOWN" for e in ev)


def test_ignore_at_f_stays_f():
    s, ev = apply_ignore(PlayerState(rank_index=0, streak=2))
    assert s.rank == "F"
    assert not any(e.kind == "RANK_DOWN" for e in ev)
    assert s.streak == 0


def test_demote_then_recover_via_pass():
    s = PlayerState(xp=300, rank_index=2, streak=0)
    s, _ = apply_ignore(s)
    assert s.rank == "E"
    s, ev = apply_pass(s, 20, date(2026, 10, 11))
    # 325 xp affords D; rank climbs back toward derived
    assert s.rank == "D"
    assert any(e.kind == "RANK_UP" for e in ev)


def test_same_day_double_pass_does_not_stack_streak():
    d = date(2026, 10, 11)
    s, _ = apply_pass(PlayerState(), 50, d)
    s, _ = apply_pass(s, 10, d)
    assert s.streak == 1


def test_negative_base_xp_rejected():
    import pytest
    with pytest.raises(ValueError):
        apply_pass(PlayerState(), -1, date(2026, 10, 11))