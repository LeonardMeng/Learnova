from datetime import UTC, datetime, timedelta

import pytest

from learnova.scheduler.mastery import MasteryConfig, replay, round_score, update

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_round_score():
    assert round_score(3.46) == 3.5
    assert round_score(3.44) == 3.4
    assert round_score(7) == 5.0 and round_score(-1) == 0.0


def test_moving_average_on_score_over_five():
    assert update(0.0, 5.0) == pytest.approx(0.3)
    assert update(0.5, 2.5) == pytest.approx(0.5)  # 2.5/5 = 0.5: no change
    assert update(0.0, 5.0, MasteryConfig(alpha=1.0)) == pytest.approx(1.0)
    perfect = [replay([(at(i), 5.0) for i in range(n)]).mastery for n in (1, 3, 6)]
    assert perfect == pytest.approx([0.3, 1 - 0.7**3, 1 - 0.7**6])  # 0.30, 0.66, 0.88


def test_replay_uses_time_order_and_counts():
    attempts = [(at(2), 0.0), (at(0), 5.0), (at(1), 5.0)]
    state = replay(attempts)
    assert state.review_count == 3 and state.last_reviewed == at(2)
    # 5, 5, then 0: 0.3 -> 0.51 -> 0.357
    assert state.mastery == pytest.approx(0.357)
    assert replay([]).mastery == 0.0 and replay([]).last_reviewed is None


def test_alpha_is_validated():
    with pytest.raises(ValueError):
        MasteryConfig(alpha=0)
