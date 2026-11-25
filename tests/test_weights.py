"""The weighting formula and weighted drawing: plain values in, numbers out."""

import math
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from learnova.config import load_settings
from learnova.home import ensure_home
from learnova.scheduler.weights import PointState, WeightConfig, days_since, draw, weight

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def test_formula():
    # Reviewed just now: ln(1 + 0) = 0, so w = I × (1 − m).
    assert weight(PointState(5, 0.0, NOW), NOW) == pytest.approx(5.0)
    assert weight(PointState(4, 0.5, NOW), NOW) == pytest.approx(2.0)
    # e − 1 days ago: 1 + ln(e) = 2.
    assert weight(PointState(3, 0.0, ago(math.e - 1)), NOW) == pytest.approx(6.0)


def test_importance_scales_linearly():
    w1 = weight(PointState(1, 0.3, ago(10)), NOW)
    assert weight(PointState(5, 0.3, ago(10)), NOW) == pytest.approx(5 * w1)


def test_mastered_points_never_reach_zero():
    floor = weight(PointState(3, 1.0, NOW), NOW)
    assert floor == pytest.approx(3 * 0.1)
    assert weight(PointState(3, 0.95, NOW), NOW) == pytest.approx(floor)  # below the floor too
    assert weight(PointState(3, 1.0, NOW), NOW, WeightConfig(epsilon=0.2)) == pytest.approx(0.6)


def test_weight_grows_slowly_with_time():
    ws = [weight(PointState(3, 0.5, ago(d)), NOW) for d in (0, 1, 7, 30, 365)]
    assert ws == sorted(ws) and len(set(ws)) == 5
    assert ws[-1] / ws[0] < 8  # a year adds about ×6.9, not ×365


def test_never_quizzed_and_odd_inputs():
    config = WeightConfig(new_point_days=7)
    assert days_since(None, NOW, config) == 7
    assert weight(PointState(2), NOW, config) == pytest.approx(2 * (1 + math.log(8)))
    assert days_since(NOW + timedelta(hours=1), NOW, config) == 0  # clock skew
    assert days_since(ago(0.5), NOW, config) == pytest.approx(0.5)
    assert weight(PointState(2, -0.5, NOW), NOW) == weight(PointState(2, 0.0, NOW), NOW)
    assert weight(PointState(2, 1.7, NOW), NOW) == weight(PointState(2, 1.0, NOW), NOW)
    with pytest.raises(ValueError, match="importance"):
        weight(PointState(0), NOW)
    with pytest.raises(ValueError, match="epsilon"):
        WeightConfig(epsilon=0)


def test_draw_is_repeatable_distinct_and_bounded():
    items = list("abcde")
    weights = [1, 2, 3, 4, 5]
    first = draw(items, weights, 3, random.Random(42))
    assert first == draw(items, weights, 3, random.Random(42))
    assert len(first) == len(set(first)) == 3
    assert sorted(draw(items, weights, 10, random.Random(1))) == items  # k > n: all, once
    assert draw(items, weights, 0, random.Random(1)) == []
    assert draw([], [], 3, random.Random(1)) == []
    with pytest.raises(ValueError):
        draw(items, [1, 2, 3, 4, 0], 1, random.Random(1))
    with pytest.raises(ValueError):
        draw(items, [1, 2], 1, random.Random(1))


def test_draw_frequency_follows_weights():
    rng = random.Random(7)
    items = ["weak", "medium", "mastered"]
    weights = [6.0, 3.0, 1.0]
    counts = Counter(draw(items, weights, 1, rng)[0] for _ in range(20_000))
    for item, w in zip(items, weights, strict=True):
        assert counts[item] / 20_000 == pytest.approx(w / 10, abs=0.015)
    assert counts["mastered"] > 0  # still drawn now and then


def test_weights_section_in_config(learnova_home):
    ensure_home()
    assert load_settings().weights == WeightConfig()
    (learnova_home / "config.toml").write_text("[weights]\nepsilon = 0.25\nnew_point_days = 3\n")
    assert load_settings().weights == WeightConfig(epsilon=0.25, new_point_days=3)
