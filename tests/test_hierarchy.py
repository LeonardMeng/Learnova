"""The knowledge hierarchy: the rules (loops, depth, shortcuts), topic mastery, merging topics,
parents chosen during review, topics left out of quizzes, `kp organize` and the kp commands."""

from datetime import UTC, datetime

import pytest
from test_review import LONG, REDIS, Deduper, Script, workspace
from typer.testing import CliRunner

from learnova.cli.main import app
from learnova.config import load_settings
from learnova.models import Mastery
from learnova.scheduler.weights import WeightConfig
from learnova.services import hierarchy, knowledge, quiz
from learnova.services import organize as organize_service
from learnova.services.hierarchy import HierarchyError
from learnova.services.organize import (
    OrganizeReply,
    ProposedMove,
    ProposedShortcut,
    ProposedTopic,
)
from learnova.services.review import NewPoint, NoteReview, notes_to_review, review_notes
from learnova.storage.database import open_repositories

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)


def make(repos, *names, importance=3):
    user = repos.users.get_or_create_local()
    return [repos.knowledge.create(user.id, n, f"About {n}.", importance) for n in names]


def graph(repos):
    return hierarchy.load(repos, repos.users.get_or_create_local().id)


def parent_name(repos, point):
    g = graph(repos)
    p = g.primary(point.id)
    return g.name(p) if p else None


# --- the rules -------------------------------------------------------------------------------


def test_move_link_unlink(repos):
    redis, memory, eviction, caching = make(repos, "Redis", "Memory", "Eviction", "Caching")
    hierarchy.move(repos, memory, redis)
    hierarchy.move(repos, eviction, memory)
    assert [graph(repos).name(p) for p in graph(repos).path(eviction.id)] == [
        "Redis", "Memory", "Eviction",
    ]  # fmt: skip

    hierarchy.add_shortcut(repos, eviction, caching)
    assert graph(repos).shortcuts(eviction.id) == [caching.id]
    assert graph(repos).is_topic(caching.id)  # a shortcut makes a topic too

    # Moving under the shortcut parent turns that shortcut into the primary link.
    hierarchy.move(repos, eviction, caching)
    assert parent_name(repos, eviction) == "Caching" and graph(repos).shortcuts(eviction.id) == []

    assert hierarchy.remove_link(repos, eviction, caching) is True  # it was the primary one
    assert parent_name(repos, eviction) is None
    hierarchy.move(repos, memory, None)
    assert parent_name(repos, memory) is None
    with pytest.raises(HierarchyError, match="isn't under"):
        hierarchy.remove_link(repos, memory, redis)


def test_loops_are_refused(repos):
    a, b, c = make(repos, "A", "B", "C")
    hierarchy.move(repos, b, a)
    hierarchy.move(repos, c, b)
    with pytest.raises(HierarchyError, match=r"'C' is already under 'A' \(A > B > C\)"):
        hierarchy.move(repos, a, c)  # C is under B under A
    with pytest.raises(HierarchyError, match=r"\(A > B > C\): that would make a loop"):
        hierarchy.add_shortcut(repos, a, c)
    with pytest.raises(HierarchyError, match="can't be under itself"):
        hierarchy.move(repos, a, a)
    assert parent_name(repos, a) is None  # nothing changed


def test_depth_is_at_most_four(repos):
    l1, l2, l3, l4, l5, l6 = make(repos, "L1", "L2", "L3", "L4", "L5", "L6")
    hierarchy.move(repos, l2, l1)
    hierarchy.move(repos, l3, l2)
    hierarchy.move(repos, l4, l3)  # 4 levels: fine
    with pytest.raises(HierarchyError, match=r"'L5' 5 levels deep \(L1 > L2 > L3 > L4 > L5\)"):
        hierarchy.move(repos, l5, l4)
    # Moving a subtree counts its depth too: L6 > L5 under L3 would put L5 at level 5.
    hierarchy.move(repos, l5, l6)
    with pytest.raises(HierarchyError, match="'L5' 5 levels deep"):
        hierarchy.move(repos, l6, l3)
    hierarchy.add_shortcut(repos, l5, l4)  # shortcuts don't count for depth


def test_at_most_two_shortcuts(repos):
    point, home, s1, s2, s3 = make(repos, "Point", "Home", "S1", "S2", "S3")
    hierarchy.move(repos, point, home)
    with pytest.raises(HierarchyError, match="already lives under 'Home'"):
        hierarchy.add_shortcut(repos, point, home)
    hierarchy.add_shortcut(repos, point, s1)
    with pytest.raises(HierarchyError, match="already also under 'S1'"):
        hierarchy.add_shortcut(repos, point, s1)
    hierarchy.add_shortcut(repos, point, s2)
    with pytest.raises(HierarchyError, match=r"already has 2 shortcuts \('S1', 'S2'\)"):
        hierarchy.add_shortcut(repos, point, s3)


# --- topics ----------------------------------------------------------------------------------


def test_topic_mastery_matches_a_hand_calculation(repos):
    topic, sub = make(repos, "Topic", "Sub")
    a, b, c, d = (make(repos, n, importance=i)[0] for n, i in [("a", 5), ("b", 1), ("c", 3),
                                                                 ("d", 2)])  # fmt: skip
    for p in (a, b, c, sub):
        hierarchy.move(repos, p, topic)
    hierarchy.move(repos, d, sub)
    hierarchy.add_shortcut(repos, a, sub)  # a is reached twice from Topic: counted once

    def m(point, value, count=1):
        return point.id, Mastery(point_id=point.id, mastery=value, review_count=count,
                                 last_reviewed=T1)  # fmt: skip

    mastery = dict([m(a, 0.8), m(b, 0.2), m(c, 0.9, count=0), m(d, 0.5), m(sub, 1.0)])
    g = graph(repos)
    # Quizzed points under Topic: a (5 × 0.8), b (1 × 0.2), d (2 × 0.5). c was never quizzed and
    # Sub is a topic (its own old attempts don't count).
    result = hierarchy.topic_mastery(g, topic.id, mastery)
    assert result.mastery == pytest.approx((5 * 0.8 + 1 * 0.2 + 2 * 0.5) / (5 + 1 + 2))
    assert (result.quizzed, result.total) == (3, 4)
    assert result.label() == "65% · 3 of 4 quizzed"
    sub_result = hierarchy.topic_mastery(g, sub.id, mastery)
    assert sub_result.mastery == pytest.approx((2 * 0.5 + 5 * 0.8) / 7)  # d, and a by shortcut
    assert hierarchy.topic_mastery(g, sub.id, {}).label() == "not quizzed yet · 0 of 2 quizzed"

    rows = hierarchy.tree(g, mastery)
    assert [(r.point.name, r.level) for r in rows][:3] == [("Topic", 0), ("a", 1), ("c", 1)]
    assert [r.point.name for r in rows if r.also_under] == ["a"]


def test_merging_a_topic_keeps_its_children(repos):
    old, kept, root, x, y, z = make(repos, "Old topic", "Kept topic", "Root", "x", "y", "z")
    hierarchy.move(repos, x, old)
    hierarchy.move(repos, y, old)
    hierarchy.move(repos, old, root)
    hierarchy.move(repos, z, kept)
    knowledge.merge_points(repos, "Old topic", "Kept topic")
    g = graph(repos)
    assert sorted(g.name(c) for c in g.children(kept.id)) == ["x", "y", "z"]
    assert parent_name(repos, kept) == "Root"  # it had none: takes the merged point's

    # The kept point keeps its own primary parent; the merged one's becomes a shortcut.
    a, b, pa, pb = make(repos, "A", "B", "PA", "PB")
    hierarchy.move(repos, a, pa)
    hierarchy.move(repos, b, pb)
    knowledge.merge_points(repos, "A", "B")
    assert parent_name(repos, b) == "PB" and graph(repos).shortcuts(b.id) == [pa.id]

    # Merging a topic into its own child: the child takes its place.
    t, child, grandchild = make(repos, "T", "Child", "Grandchild")
    hierarchy.move(repos, child, t)
    hierarchy.move(repos, grandchild, t)
    knowledge.merge_points(repos, "T", "Child")
    assert parent_name(repos, grandchild) == "Child" and parent_name(repos, child) is None


def test_topics_are_not_quizzed(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    script = Script(Redis=lambda r: NoteReview(existing=[], new_points=[
        NewPoint(name="Persistence", summary="RDB and AOF.", importance=5),
        NewPoint(name="RDB", summary="Snapshots.", importance=4, parent="N1"),
    ], issues=[], missing=[]))  # fmt: skip
    review_notes(ws, repos, notes_to_review(repos, ws, True), script, Deduper())
    names = {i.point.name for i in quiz.candidates(repos, T1, WeightConfig())}
    assert names == {"RDB"}


# --- review ----------------------------------------------------------------------------------


def test_review_puts_new_points_under_the_chosen_parent(repos, tmp_path):
    (caching,) = make(repos, "Caching")
    (lru,) = make(repos, "LRU")
    hierarchy.move(repos, lru, caching)  # Caching is now a topic: offered as T1
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})

    def reply(req):
        assert {r: p.name for r, p in req.topics.items()} == {"T1": "Caching"}
        return NoteReview(existing=[], new_points=[
            NewPoint(name="Redis", summary="A store.", importance=5),
            NewPoint(name="Eviction", summary="Dropping keys.", importance=4, parent="T1"),
            NewPoint(name="Persistence", summary="RDB, AOF.", importance=4, parent="N1"),
            NewPoint(name="Pipelining", summary="Batches.", importance=2, parent="T9"),
            NewPoint(name="Loop", summary="x", importance=1, parent="N5"),
        ], issues=[], missing=[])  # fmt: skip

    run = review_notes(ws, repos, notes_to_review(repos, ws, True), Script(Redis=reply), Deduper())
    by_name = {
        p.name: p for p in repos.knowledge.list_for_user(repos.users.get_or_create_local().id)
    }
    assert parent_name(repos, by_name["Eviction"]) == "Caching"
    assert parent_name(repos, by_name["Persistence"]) == "Redis"
    assert parent_name(repos, by_name["Pipelining"]) is None
    assert run.reviewed[0].warnings == [
        "Unknown parent 'T9' for 'Pipelining'; left at the top.",
        "'Loop' not put under 'Loop': 'Loop' can't be under itself.",
    ]


# --- organize --------------------------------------------------------------------------------


def organizer(req):
    ref = {p.name: r for r, p in {**req.points, **req.topics}.items()}
    return OrganizeReply(
        new_topics=[
            ProposedTopic(ref="X1", name="Persistence", summary="Saving data.", importance=4),
            ProposedTopic(ref="X2", name="Empty", summary="Nothing goes here.", importance=1),
        ],
        moves=[
            ProposedMove(point=ref["RDB"], parent="X1"),
            ProposedMove(point=ref["AOF"], parent="X1"),
            ProposedMove(point="X1", parent=ref["Redis"]),
            ProposedMove(point=ref["Redis"], parent=ref["RDB"]),  # a loop: refused
            ProposedMove(point="K99", parent=None),  # unknown: refused
        ],
        shortcuts=[ProposedShortcut(point=ref["AOF"], parent=ref["Logs"], action="add")],
    )


def test_organize_proposes_checks_and_applies(repos):
    make(repos, "Redis", "RDB", "AOF", "Logs")
    proposal = organize_service.propose(repos, organizer)
    text = organize_service.render(proposal, "m", T1)
    assert "4 point(s) sent · 8 change(s) proposed · 5 can be applied · 3 refused" in text
    assert "1. **Persistence** (importance 4): Saving data." in text
    assert (
        "~~**Empty** (importance 1): Nothing goes here.~~ — **refused:** no point was put" in text
    )
    assert "**RDB**: top level → under **Persistence**" in text
    assert "~~**Redis**: top level → under **RDB**~~ — **refused:** 'RDB' is already under" in text
    assert "**refused:** unknown label K99." in text
    assert "Add: **AOF** also under **Logs**" in text
    assert graph(repos).links == {}  # nothing changed yet

    organize_service.apply(repos, proposal)
    g = graph(repos)
    by_name = {p.name: p for p in g.points.values()}
    assert "Empty" not in by_name
    assert [g.name(p) for p in g.path(by_name["AOF"].id)] == ["Redis", "Persistence", "AOF"]
    assert g.shortcuts(by_name["AOF"].id) == [by_name["Logs"].id]


def test_organize_command_confirms_before_applying(repos, monkeypatch, learnova_home):
    from learnova.llm import factory

    make(repos, "Redis", "RDB", "AOF", "Logs")
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    monkeypatch.setattr(organize_service, "llm_organizer", lambda model: organizer)
    runner = CliRunner()

    r = runner.invoke(app, ["kp", "organize", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "5 change(s) can be applied, 3 refused" in r.output and "Dry run" in r.output
    assert "  6. Redis: top level → under RDB  (refused:" in r.output
    reports = sorted((learnova_home / "reports").iterdir())
    assert [p.name.endswith("-organize.md") for p in reports] == [True]

    r = runner.invoke(app, ["kp", "organize"], input="n\n")
    assert "Apply all of them?" in r.output and "Nothing changed." in r.output
    assert graph(repos).links == {}

    r = runner.invoke(app, ["kp", "organize"], input="y\n")
    assert r.exit_code == 0 and "Applied 5 change(s)." in r.output
    assert len(graph(repos).links) == 4
    assert len(list((learnova_home / "reports").iterdir())) == 3
    latest = next((learnova_home / "reports").glob("*-organize-3.md")).read_text()
    assert "**Applied**" in latest and "the 5 change(s) not marked refused" in latest

    r = runner.invoke(app, ["kp", "list"])
    assert r.exit_code == 0, r.output
    assert "  Persistence" in r.output and "topic · not quizzed yet · 0 of 2" in r.output
    assert "↗ also under Logs" in r.output


def test_kp_move_link_unlink_commands(repos):
    make(repos, "Redis", "Eviction", "Caching", "Memory")
    runner = CliRunner()
    r = runner.invoke(app, ["kp", "move", "Eviction", "--under", "Redis"])
    assert r.exit_code == 0 and "'Eviction' is now under 'Redis'." in r.output
    r = runner.invoke(app, ["kp", "link", "Eviction", "--also-under", "Caching"])
    assert "'Eviction' is now also under 'Caching'." in r.output
    r = runner.invoke(app, ["kp", "move", "Redis", "--under", "Eviction"])
    assert r.exit_code != 0 and "that would make a loop" in r.output
    r = runner.invoke(app, ["kp", "unlink", "Eviction", "--from", "Redis"])
    assert "no longer under 'Redis'. It is now at the top level." in r.output
    r = runner.invoke(app, ["kp", "move", "Eviction"])
    assert r.exit_code != 0 and "--under" in r.output
    r = runner.invoke(app, ["kp", "list", "--flat"])
    assert r.exit_code == 0 and "topic ·" not in r.output
