"""Which review issue to fix first. A pure function, like weights.py: no database, no model.

    priority = kind_weight × importance × (1 + (1 − mastery))

A wrong fact about an important point you haven't mastered comes first. An issue linked to no
knowledge point counts as importance 3; a point never quizzed (or no point) counts as mastery 0.5.
"""

from __future__ import annotations

from dataclasses import dataclass

UNLINKED_IMPORTANCE = 3
UNKNOWN_MASTERY = 0.5


@dataclass(frozen=True)
class IssueWeights:
    factual_error: float = 3.0
    unclear: float = 2.0
    missing: float = 1.0

    def __post_init__(self) -> None:
        for name in ("factual_error", "unclear", "missing"):
            if getattr(self, name) <= 0:
                raise ValueError(f"issue weight {name} must be positive")

    def of(self, kind: str) -> float:
        return getattr(self, kind)


def priority(
    kind: str,
    importance: int | None = None,
    mastery: float | None = None,
    weights: IssueWeights = IssueWeights(),
) -> float:
    """`importance` None = no linked point; `mastery` None = never quizzed (or no point)."""
    i = UNLINKED_IMPORTANCE if importance is None else importance
    m = UNKNOWN_MASTERY if mastery is None else min(1.0, max(0.0, mastery))
    return weights.of(kind) * i * (1 + (1 - m))
