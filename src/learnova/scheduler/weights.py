"""How likely each knowledge point is to be drawn for a quiz question.

Pure functions only: no database, no model, no clock. The caller passes in each point's state and
the current time, and (for drawing) a random-number generator, so results are easy to test and
the formula is easy to replace.

    w = I × max(1 − m, ε) × (1 + ln(1 + d))

I = importance (1–5), m = mastery (0–1), d = days since the last review, ε = floor (default 0.1).
Because of ε, mastered points still come up now and then; points not reviewed for a long time
slowly gain weight.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class WeightConfig:
    epsilon: float = 0.1  # floor for (1 − mastery); must be in (0, 1]
    new_point_days: float = 7.0  # d for a point that has never been quizzed

    def __post_init__(self) -> None:
        if not 0 < self.epsilon <= 1:
            raise ValueError(f"epsilon must be in (0, 1], got {self.epsilon}")
        if self.new_point_days < 0:
            raise ValueError(f"new_point_days must be >= 0, got {self.new_point_days}")


@dataclass(frozen=True)
class PointState:
    importance: int  # 1–5
    mastery: float = 0.0  # 0–1
    last_reviewed: datetime | None = None  # None = never quizzed


def days_since(last_reviewed: datetime | None, now: datetime, config: WeightConfig) -> float:
    """Fractional days, never negative (a clock that runs slightly behind counts as 0)."""
    if last_reviewed is None:
        return config.new_point_days
    return max(0.0, (now - last_reviewed).total_seconds() / SECONDS_PER_DAY)


def weight(state: PointState, now: datetime, config: WeightConfig = WeightConfig()) -> float:
    if not 1 <= state.importance <= 5:
        raise ValueError(f"importance must be 1–5, got {state.importance}")
    mastery = min(1.0, max(0.0, state.mastery))
    d = days_since(state.last_reviewed, now, config)
    return state.importance * max(1 - mastery, config.epsilon) * (1 + math.log1p(d))


def draw(items: Sequence[T], weights: Sequence[float], k: int, rng: random.Random) -> list[T]:
    """Pick up to k different items, each with probability proportional to its weight.

    Weighted sampling without replacement (Efraimidis–Spirakis): every item gets the key
    u^(1/w) for a uniform random u, and the k largest keys win. Pass a seeded random.Random to
    get the same picks every time.
    """
    if len(items) != len(weights):
        raise ValueError("items and weights must have the same length")
    if any(w <= 0 or math.isnan(w) for w in weights):
        raise ValueError("weights must be positive")
    if k <= 0 or not items:
        return []
    keys = [rng.random() ** (1 / w) for w in weights]
    order = sorted(range(len(items)), key=keys.__getitem__, reverse=True)
    return [items[i] for i in order[:k]]
