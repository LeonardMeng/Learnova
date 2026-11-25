"""Mastery from quiz scores. Pure functions, like weights.py.

Mastery is never edited directly: it is always the result of replaying a point's attempts in time
order through `update`, a moving average on score / 5:

    m ← m + α × (s − m)      m starts at 0, s = score / 5, α = 0.3 by default

So recent answers count most and one lucky answer doesn't make a point mastered (one perfect
answer gives 0.30, three give 0.66, six give 0.88).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

MAX_SCORE = 5.0


@dataclass(frozen=True)
class MasteryConfig:
    alpha: float = 0.3

    def __post_init__(self) -> None:
        if not 0 < self.alpha <= 1:
            raise ValueError(f"alpha must be in (0, 1], got {self.alpha}")


@dataclass(frozen=True)
class MasteryState:
    mastery: float = 0.0
    review_count: int = 0
    last_reviewed: datetime | None = None


def round_score(score: float) -> float:
    """Clamp to 0–5 and keep one decimal place."""
    return round(min(MAX_SCORE, max(0.0, score)), 1)


def update(mastery: float, score: float, config: MasteryConfig = MasteryConfig()) -> float:
    return mastery + config.alpha * (round_score(score) / MAX_SCORE - mastery)


def replay(
    attempts: Iterable[tuple[datetime, float]], config: MasteryConfig = MasteryConfig()
) -> MasteryState:
    """(time, score) pairs in any order -> mastery after all of them, oldest first."""
    state = MasteryState()
    for when, score in sorted(attempts, key=lambda a: a[0]):
        state = MasteryState(update(state.mastery, score, config), state.review_count + 1, when)
    return state
