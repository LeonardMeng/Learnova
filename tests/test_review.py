"""Gap review with fake model calls: which notes are picked, what happens to existing points on
re-review, the duplicate check, merging, and the report."""

from datetime import UTC, datetime

import pytest

from learnova.llm.factory import LLMCallError
from learnova.models import NoteContent, NoteSummary
from learnova.report import render_review, write_review_report
from learnova.services import knowledge
from learnova.services.review import (
    DuplicateCheck,
    DuplicateMatch,
    ExistingPoint,
    Issue,
    Missing,
    NewPoint,
    NoteReview,
    Status,
    notes_to_review,
    review_notes,
)
from learnova.sources.base import NoteSource
from learnova.storage.database import open_repositories
from learnova.sync import sync_workspace

T1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
REDIS = "11111111-1111-1111-1111-111111111111"
DB = "22222222-2222-2222-2222-222222222222"
INDEX = "33333333-3333-3333-3333-333333333333"
LONG = "Redis keeps data in memory and can persist it with RDB snapshots or an AOF log.\n"


class Source(NoteSource):
    def __init__(self, notes):  # source_id -> (title, edited, markdown)
        self.notes = notes

    def list_notes(self):
        return [
            NoteSummary(source_id=sid, title=t, path=("Interview", t), last_edited=e)
            for sid, (t, e, _) in self.notes.items()
        ]

    def fetch(self, note_id):
        return NoteContent(markdown=self.notes[note_id][2])

    def read_image(self, ref):
        raise AssertionError


class Script:
    """Fake reviewer: per note title, a function from the request to a NoteReview."""

    def __init__(self, **by_title):
        self.by_title = by_title
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.by_title[req.note.title](req)


class Deduper:
    """Fake duplicate check: `same` maps a new point's name to an existing point's name."""

    def __init__(self, **same):
        self.same = same
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        by_name = {c.point.name: ref for ref, c in req.candidates.items()}
        return DuplicateCheck(
            matches=[
                DuplicateMatch(new_ref=ref, match=by_name.get(self.same.get(p.name)))
                for ref, p in req.new_points.items()
            ]
        )


def review(existing=(), new=(), issues=(), missing=()):
    return NoteReview(
        existing=list(existing),
        new_points=[NewPoint(name=n, summary=f"About {n}.", importance=i) for n, i in new],
        issues=list(issues),
        missing=list(missing),
    )


@pytest.fixture
def repos(tmp_path):
    return open_repositories(f"sqlite:///{tmp_path / 'db.sqlite'}")


def workspace(repos, tmp_path, name, notes):
    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, name, REDIS, tmp_path / name)
    ws.path.mkdir()
    source = Source(notes)
    sync_workspace(ws, source, repos.notes, repos.images)
    return ws, source


def resync(repos, ws, source, sid, markdown, hour=11):
    title, _, _ = source.notes[sid]
    source.notes[sid] = (title, T1.replace(hour=hour), markdown)
    sync_workspace(ws, source, repos.notes, repos.images)


def by_status(result):
    return {o.point.name: o.status for o in result.outcomes}


def test_first_review_then_nothing_to_do(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    reviewer = Script(Redis=lambda req: review(new=[("RDB vs AOF", 5), ("Eviction", 3)]))
    deduper = Deduper()
    run = review_notes(ws, repos, notes_to_review(repos, ws, False), reviewer, deduper)

    assert by_status(run.reviewed[0]) == {"RDB vs AOF": Status.NEW, "Eviction": Status.NEW}
    assert reviewer.requests[0].existing == {}
    assert "Redis keeps data in memory" in reviewer.requests[0].markdown  # cache text, no header
    assert len(deduper.requests) == 1
    note = repos.notes.list(ws.id)[0]
    assert {p.name for p in repos.knowledge.list_for_note(note.id)} == {"RDB vs AOF", "Eviction"}
    assert notes_to_review(repos, ws, False) == []
    assert len(notes_to_review(repos, ws, True)) == 1


def test_rereview_keeps_updates_removes_and_adds(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    first = Script(Redis=lambda req: review(new=[("RDB", 5), ("AOF", 4), ("Eviction", 3)]))
    review_notes(ws, repos, notes_to_review(repos, ws, False), first, Deduper())
    note = repos.notes.list(ws.id)[0]
    before = {p.name: p for p in repos.knowledge.list_for_note(note.id)}

    resync(repos, ws, source, REDIS, LONG + "Also: Redis Cluster shards keys.\n")
    assert [n.title for n in notes_to_review(repos, ws, False)] == ["Redis"]

    def second(req):
        refs = {p.name: ref for ref, p in req.existing.items()}
        assert set(refs) == {"RDB", "AOF", "Eviction"}
        return review(
            existing=[
                ExistingPoint(ref=refs["RDB"], status="kept"),
                ExistingPoint(
                    ref=refs["AOF"], status="updated", name="AOF log", summary="Append-only."
                ),
                ExistingPoint(ref=refs["Eviction"], status="removed"),
            ],
            new=[("Cluster sharding", 4)],
        )

    deduper = Deduper()
    run = review_notes(ws, repos, notes_to_review(repos, ws, False), Script(Redis=second), deduper)
    assert by_status(run.reviewed[0]) == {
        "RDB": Status.KEPT,
        "AOF log": Status.UPDATED,
        "Eviction": Status.REMOVED,
        "Cluster sharding": Status.NEW,
    }
    after = {p.name: p for p in repos.knowledge.list_for_note(note.id)}
    assert set(after) == {"RDB", "AOF log", "Cluster sharding"}
    assert after["RDB"].id == before["RDB"].id
    assert after["AOF log"].id == before["AOF"].id
    assert after["AOF log"].importance == before["AOF"].importance  # never overwritten
    assert repos.knowledge.get(before["Eviction"].id) is not None  # unlinked, not deleted
    # Only the genuinely new point went to the duplicate check.
    assert [p.name for p in deduper.requests[0].new_points.values()] == ["Cluster sharding"]


def test_unmentioned_points_are_kept_and_no_dedupe_without_new_points(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    review_notes(
        ws,
        repos,
        notes_to_review(repos, ws, False),
        Script(Redis=lambda r: review(new=[("RDB", 5), ("AOF", 4)])),
        Deduper(),
    )
    resync(repos, ws, source, REDIS, LONG + "More detail.\n")

    def only_one(req):
        ref = next(iter(req.existing))
        return review(
            existing=[
                ExistingPoint(ref=ref, status="kept"),
                ExistingPoint(ref="P99", status="removed"),
            ]
        )

    deduper = Deduper()
    run = review_notes(
        ws, repos, notes_to_review(repos, ws, False), Script(Redis=only_one), deduper
    )
    result = run.reviewed[0]
    assert set(by_status(result).values()) == {Status.KEPT}
    assert any(o.note == "not mentioned by the model" for o in result.outcomes)
    assert result.warnings == ["1 existing point(s) not mentioned; kept."]
    assert deduper.requests == []  # no new points, no duplicate check


def test_new_point_that_is_one_of_the_notes_own_points_is_not_created(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    review_notes(
        ws,
        repos,
        notes_to_review(repos, ws, False),
        Script(Redis=lambda r: review(new=[("RDB snapshots", 5)])),
        Deduper(),
    )
    resync(repos, ws, source, REDIS, LONG + "Edited.\n")

    def forgetful(req):  # says "removed" and re-proposes it under another name
        ref = next(iter(req.existing))
        return review(existing=[ExistingPoint(ref=ref, status="removed")], new=[("RDB", 5)])

    run = review_notes(
        ws,
        repos,
        notes_to_review(repos, ws, False),
        Script(Redis=forgetful),
        Deduper(RDB="RDB snapshots"),
    )
    assert by_status(run.reviewed[0]) == {"RDB snapshots": Status.KEPT}
    note = repos.notes.list(ws.id)[0]
    assert [p.name for p in repos.knowledge.list_for_note(note.id)] == ["RDB snapshots"]
    user = repos.users.get_or_create_local()
    assert len(repos.knowledge.list_for_user(user.id)) == 1


def test_duplicates_across_workspaces_then_merge(repos, tmp_path):
    ws_a, _ = workspace(repos, tmp_path, "ml", {REDIS: ("Optimisation", T1, LONG)})
    review_notes(
        ws_a,
        repos,
        notes_to_review(repos, ws_a, False),
        Script(Optimisation=lambda r: review(new=[("Gradient descent", 5)])),
        Deduper(),
    )

    ws_b, _ = workspace(repos, tmp_path, "dl", {DB: ("Training", T1, LONG)})
    deduper = Deduper(GD="Gradient descent")
    run = review_notes(
        ws_b,
        repos,
        notes_to_review(repos, ws_b, False),
        Script(Training=lambda r: review(new=[("GD", 4)])),
        deduper,
    )
    outcome = run.reviewed[0].outcomes[0]
    assert (
        outcome.status == Status.NEW and outcome.possible_duplicate.point.name == "Gradient descent"
    )
    assert outcome.possible_duplicate.sources[0].workspace_name == "ml"

    user = repos.users.get_or_create_local()
    report = render_review(run, "fake:model", repos.knowledge.list_for_user(user.id), T1)
    assert '`learnova kp merge "GD" --into "Gradient descent"`' in report
    assert "(ml: Interview / Optimisation)" in report

    result = knowledge.merge_points(repos, "GD", "gradient DESCENT")
    assert result.into.name == "Gradient descent" and result.source_count == 2
    assert [p.name for p in repos.knowledge.list_for_user(user.id)] == ["Gradient descent"]
    note_b = repos.notes.list(ws_b.id)[0]
    assert [p.name for p in repos.knowledge.list_for_note(note_b.id)] == ["Gradient descent"]


def test_same_point_twice_in_one_run_is_flagged(repos, tmp_path):
    notes = {REDIS: ("Redis", T1, LONG), DB: ("Database", T1, LONG)}
    ws, _ = workspace(repos, tmp_path, "w", notes)
    reviewer = Script(
        Redis=lambda r: review(new=[("Caching", 4)]),
        Database=lambda r: review(new=[("Caching", 4)]),
    )
    run = review_notes(
        ws, repos, notes_to_review(repos, ws, False), reviewer, Deduper(Caching="Caching")
    )
    second = run.reviewed[1].outcomes[0]
    assert second.possible_duplicate is not None
    assert second.possible_duplicate.point.id == run.reviewed[0].outcomes[0].point.id


def test_failed_and_skipped_notes(repos, tmp_path):
    notes = {REDIS: ("Redis", T1, LONG), INDEX: ("Index", T1, "→ Sub-page: Redis\n")}
    ws, _ = workspace(repos, tmp_path, "w", notes)

    def boom(req):
        raise LLMCallError("RateLimitError: slow down")

    run = review_notes(ws, repos, notes_to_review(repos, ws, False), Script(Redis=boom), Deduper())
    assert [n.title for n in run.skipped] == ["Index"]
    assert [(n.title, e) for n, e in run.failed] == [("Redis", "RateLimitError: slow down")]
    assert [n.title for n in notes_to_review(repos, ws, False)] == ["Redis"]  # retried next time


def test_report_file_and_sections(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    reviewer = Script(
        Redis=lambda r: review(
            new=[("RDB", 5)],
            issues=[
                Issue(
                    kind="factual_error",
                    quote="Redis is single-threaded for I/O",
                    problem="Since 6.0 Redis can use I/O threads.",
                ),
                Issue(kind="unclear", quote="AOF is safer", problem="Safer than what, and why?"),
            ],
            missing=[Missing(topic="Replication", why="A Redis note should cover replicas.")],
        )
    )
    run = review_notes(ws, repos, notes_to_review(repos, ws, False), reviewer, Deduper())
    user = repos.users.get_or_create_local()
    points = repos.knowledge.list_for_user(user.id)
    first = write_review_report(run, "fake:model", points)
    second = write_review_report(run, "fake:model", points)
    assert first.parent == ws.path / "reports" and first.name.endswith("-review.md")
    assert second.name.endswith("-review-2.md")
    text = first.read_text()
    assert "- Factual errors: 1 · unclear explanations: 1 · missing topics: 1" in text
    assert "### Factual errors\n\n- > Redis is single-threaded for I/O" in text
    assert "### Missing topics\n\n- **Replication**" in text
    assert "| new | **RDB**: About RDB. | 5 |" in text


def test_find_point_by_ambiguous_name(repos):
    user = repos.users.get_or_create_local()
    a = repos.knowledge.create(user.id, "Caching", "one", 3)
    repos.knowledge.create(user.id, "caching", "two", 3)
    with pytest.raises(knowledge.KnowledgeError, match="2 points match"):
        knowledge.find_point(repos, "Caching")
    assert knowledge.find_point(repos, str(a.id)[:8]).id == a.id
    with pytest.raises(knowledge.KnowledgeError, match="same knowledge point"):
        knowledge.merge_points(repos, str(a.id), str(a.id)[:8])


def test_merge_with_overlapping_notes(repos, tmp_path):
    ws, _ = workspace(
        repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG), DB: ("Database", T1, LONG)}
    )
    user = repos.users.get_or_create_local()
    n1, n2 = sorted(repos.notes.list(ws.id), key=lambda n: n.title)
    keep = repos.knowledge.create(user.id, "Keep", "k", 4)
    dup = repos.knowledge.create(user.id, "Dup", "d", 2)
    for p, n in ((keep, n1), (dup, n1), (dup, n2)):
        repos.knowledge.link(p.id, n.id)
    repos.knowledge.merge(dup.id, keep.id)
    assert repos.knowledge.get(dup.id) is None
    assert sorted(s.note_id for s in repos.knowledge.sources(user.id)) == sorted([n1.id, n2.id])


def test_review_and_merge_commands(tmp_path, monkeypatch, learnova_home):
    from typer.testing import CliRunner

    from learnova.cli import workspace as workspace_cli
    from learnova.cli.main import app
    from learnova.llm import factory
    from learnova.services import review as review_service
    from learnova.sources import notion

    source = Source({REDIS: ("Redis", T1, LONG), DB: ("Database", T1, LONG)})
    from collections import Counter

    source.block_types, source.skipped_databases = Counter(), []
    monkeypatch.setattr(workspace_cli, "check_notion_page", lambda page_id: "Interview")
    monkeypatch.setattr(notion, "make_client", lambda: None)
    monkeypatch.setattr(notion, "NotionSource", lambda client, page: source)
    monkeypatch.setattr(factory, "main_model", lambda settings: object())
    reviewer = Script(
        Database=lambda r: review(new=[("Caching", 4)]),  # reviewed first (path order)
        Redis=lambda r: review(new=[("Cache", 3)]),
    )
    monkeypatch.setattr(review_service, "llm_reviewer", lambda model: reviewer)
    monkeypatch.setattr(review_service, "llm_deduper", lambda model: Deduper(Cache="Caching"))

    runner = CliRunner()
    ws_dir = tmp_path / "ws"
    runner.invoke(app, ["workspace", "new", "w", "--notion", REDIS, "--path", str(ws_dir)])
    monkeypatch.chdir(ws_dir)
    assert runner.invoke(app, ["sync"]).exit_code == 0

    r = runner.invoke(app, ["review"])
    assert r.exit_code == 0, r.output
    assert "Interview / Database: reviewed" in r.output
    assert "2 reviewed · 2 new point(s) · 0 issue(s) · 0 missing topic(s) · 1 possible" in r.output
    report = next((ws_dir / "reports").iterdir()).read_text()
    assert '`learnova kp merge "Cache" --into "Caching"`' in report

    r = runner.invoke(app, ["review"])
    assert "Nothing to review" in r.output

    r = runner.invoke(app, ["kp", "merge", "Cache", "--into", "Caching"])
    assert r.exit_code == 0, r.output
    assert "Merged 'Cache' into 'Caching'. It now comes from 2 note(s)." in r.output
    r = runner.invoke(app, ["kp", "merge", "Cache", "--into", "Caching"])
    assert r.exit_code == 1 and "No knowledge point named" in r.output
    print(report)


def test_kp_list_and_importance(repos, tmp_path, monkeypatch, learnova_home):
    from typer.testing import CliRunner

    from learnova.cli.main import app
    from learnova.config import load_settings

    repos = open_repositories(load_settings().database_url)  # the CLI's database
    ws_a, _ = workspace(repos, tmp_path, "ml", {REDIS: ("Optimisation", T1, LONG)})
    ws_b, _ = workspace(repos, tmp_path, "db", {DB: ("Redis", T1, LONG)})
    review_notes(
        ws_a,
        repos,
        notes_to_review(repos, ws_a, False),
        Script(Optimisation=lambda r: review(new=[("Gradient descent", 3)])),
        Deduper(),
    )
    review_notes(
        ws_b,
        repos,
        notes_to_review(repos, ws_b, False),
        Script(Redis=lambda r: review(new=[("RDB", 5)])),
        Deduper(),
    )

    names = [i.point.name for i in knowledge.list_points(repos)]
    assert names == ["RDB", "Gradient descent"]  # most important first
    assert [i.point.name for i in knowledge.list_points(repos, "ml")] == ["Gradient descent"]

    runner = CliRunner()
    r = runner.invoke(app, ["kp", "list"])
    assert r.exit_code == 0, r.output
    assert "ml: Interview / Optimisation" in r.output and "2 point(s)." in r.output

    r = runner.invoke(app, ["kp", "importance", "gradient descent", "5"])
    assert r.exit_code == 0 and "Importance of 'Gradient descent' is now 5." in r.output
    r = runner.invoke(app, ["kp", "importance", "RDB", "9"])
    assert r.exit_code == 2  # rejected by the argument check

    # A re-review never overwrites importance you set.
    note = repos.notes.list(ws_a.id)[0]
    repos.notes.mark_reviewed(note.id, "stale")

    def keep(req):
        return review(
            existing=[
                ExistingPoint(
                    ref="P1", status="updated", name="Gradient descent", summary="Changed summary."
                )
            ]
        )

    review_notes(
        ws_a, repos, notes_to_review(repos, ws_a, False), Script(Optimisation=keep), Deduper()
    )
    point = knowledge.find_point(repos, "Gradient descent")
    assert (point.importance, point.summary) == (5, "Changed summary.")

    r = runner.invoke(app, ["kp", "list", "--workspace", "nope"])
    assert "No knowledge points from workspace 'nope'" in r.output
    print(runner.invoke(app, ["kp", "list"]).output)
