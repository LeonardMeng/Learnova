"""Mastery from quiz scores. Pure functions, like weights.py.

Mastery is never edited directly: it is always the result of replaying a point's attempts in time
order through `update`, a moving average on score / 5, where each question type moves it by a
different amount:

    m ← m + α × w_type × (s − m)      m starts at 0, s = score / 5, α = 0.3 by default

w_type is 1 for short answers and lower for question types that can be guessed (multiple choice
0.6, true/false 0.3 by default). So recent answers count most and one lucky answer doesn't make a
point mastered (one perfect short answer gives 0.30, three give 0.66, six give 0.88).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

MAX_SCORE = 5.0
SHORT_ANSWER = "short_answer"
DEFAULT_TYPE_WEIGHTS = {"short_answer": 1.0, "multiple_choice": 0.6, "true_false": 0.3}


@dataclass(frozen=True)
class MasteryConfig:
    alpha: float = 0.3
    # Question type (QuestionType value) -> how much one answer of that type moves mastery.
    type_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TYPE_WEIGHTS))

    def __post_init__(self) -> None:
        if not 0 < self.alpha <= 1:
            raise ValueError(f"alpha must be in (0, 1], got {self.alpha}")
        for name, w in self.type_weights.items():
            if not 0 <= w <= 1:
                raise ValueError(f"type weight for {name} must be in [0, 1], got {w}")

    def weight_of(self, question_type: str) -> float:
        return self.type_weights.get(question_type, 1.0)


@dataclass(frozen=True)
class MasteryState:
    mastery: float = 0.0
    review_count: int = 0
    last_reviewed: datetime | None = None


def round_score(score: float) -> float:
    """Clamp to 0–5 and keep one decimal place."""
    return round(min(MAX_SCORE, max(0.0, score)), 1)


def update(
    mastery: float,
    score: float,
    config: MasteryConfig = MasteryConfig(),
    question_type: str = SHORT_ANSWER,
) -> float:
    rate = config.alpha * config.weight_of(question_type)
    return mastery + rate * (round_score(score) / MAX_SCORE - mastery)


def replay(
    attempts: Iterable[tuple[datetime, float] | tuple[datetime, float, str]],
    config: MasteryConfig = MasteryConfig(),
) -> MasteryState:
    """(time, score[, question type]) in any order -> mastery after all of them, oldest first.
    Without a type, an attempt counts as a short answer."""
    state = MasteryState()
    for attempt in sorted(attempts, key=lambda a: a[0]):
        when, score = attempt[0], attempt[1]
        qtype = attempt[2] if len(attempt) > 2 else SHORT_ANSWER
        state = MasteryState(
            update(state.mastery, score, config, qtype), state.review_count + 1, when
        )
    return state
