"""Review issues: priority, their lifecycle across reviews (new, still present, fixed, dismissed,
obsolete, hidden), links to knowledge points, `kp merge`, the report and `learnova issues`."""

import pytest
from test_ignore_sync import Source as IgnoringSource
from test_review import LONG, REDIS, T1, Deduper, Script, resync, workspace
from typer.testing import CliRunner

from learnova.config import load_settings
from learnova.home import ensure_home
from learnova.models import IssueKind, IssueStatus, ReviewIssue
from learnova.scheduler.priority import IssueWeights, priority
from learnova.services import issues as issues_service
from learnova.services import knowledge
from learnova.services.review import (
    Issue,
    IssueCheck,
    Missing,
    NewPoint,
    NoteReview,
    notes_to_review,
    review_notes,
)
from learnova.storage.database import open_repositories
from learnova.sync import sync_workspace

ERROR = Issue(
    kind="factual_error", quote="Redis stores data on disk only", problem="It's in memory."
)
UNCLEAR = Issue(kind="unclear", quote="AOF is safer", problem="Safer than what?")
MISSING = Missing(topic="Replication", why="A Redis note should cover replicas.")


def reply(issues=(), missing=(), status=(), new=(), existing=()):
    return NoteReview(
        existing=list(existing),
        new_points=[NewPoint(name=n, summary=f"About {n}.", importance=i) for n, i in new],
        issue_status=[IssueCheck(ref=r, status=s) for r, s in status],
        issues=list(issues),
        missing=list(missing),
    )


@pytest.fixture
def repos(learnova_home):
    return open_repositories(load_settings().database_url)


def run_review(repos, ws, script, everything=True):
    return review_notes(ws, repos, notes_to_review(repos, ws, everything), script, Deduper())


def open_issues(repos, ws):
    user = repos.users.get_or_create_local()
    return repos.issues.list(user.id, ws.id, [IssueStatus.OPEN])


# --- priority --------------------------------------------------------------------------------


def test_priority_formula_and_defaults(learnova_home):
    assert priority("factual_error", 5, 0.0) == pytest.approx(3 * 5 * 2)
    assert priority("unclear", 4, 1.0) == pytest.approx(2 * 4 * 1)
    assert priority("missing") == pytest.approx(1 * 3 * 1.5)  # unlinked, never quizzed
    assert priority("factual_error", 2, None) == pytest.approx(3 * 2 * 1.5)
    assert priority("unclear", 3, 7.0) == priority("unclear", 3, 1.0)  # mastery clamped
    assert priority("missing", 3, 0.5, IssueWeights(missing=4)) == pytest.approx(4 * 3 * 1.5)
    with pytest.raises(ValueError):
        IssueWeights(unclear=0)
    ensure_home()
    (learnova_home / "config.toml").write_text("[issues]\nmissing = 2.5\n")
    assert load_settings().issues == IssueWeights(missing=2.5)


def test_fingerprint_ignores_spacing_case_and_punctuation():
    a = issues_service.fingerprint("unclear", "AOF is  safer!")
    assert a == issues_service.fingerprint("unclear", "aof is safer")
    assert a != issues_service.fingerprint("factual_error", "AOF is safer")
    assert issues_service.fingerprint("missing", topic="Replication") == issues_service.fingerprint(
        "missing", "ignored quote", "replication."
    )


# --- lifecycle -------------------------------------------------------------------------------


def test_new_issues_link_to_points_and_are_not_repeated(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    first = Script(
        Redis=lambda r: reply(
            new=[("Persistence", 5)],
            issues=[ERROR.model_copy(update={"point": "N1"}), UNCLEAR],
            missing=[MISSING],
        )
    )
    run = run_review(repos, ws, first)
    created = run.reviewed[0].new_issues
    assert [i.kind for i in created] == ["factual_error", "unclear", "missing"]
    assert created[0].point_id == run.reviewed[0].outcomes[0].point.id  # N1 = the new point
    assert created[1].point_id is None and created[2].topic == "Replication"

    # Second review after an unrelated edit: the model says still present and re-raises one of
    # them in other words; nothing new is created.
    resync(repos, ws, source, REDIS, LONG + "Unrelated line.\n")
    requests = []

    def second(req):
        requests.append(req)
        return reply(
            status=[("I1", "still_present"), ("I2", "still_present"), ("I3", "still_present")],
            issues=[
                Issue(kind="factual_error", quote="Redis stores data  on disk ONLY.", problem="x")
            ],
        )

    run = run_review(repos, ws, Script(Redis=second))
    assert run.reviewed[0].new_issues == [] and run.reviewed[0].fixed_issues == []
    assert sorted(requests[0].open_issues) == ["I1", "I2", "I3"]
    assert len(open_issues(repos, ws)) == 3


def test_fixed_issues_close_and_show_in_the_report(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run_review(repos, ws, Script(Redis=lambda r: reply(issues=[ERROR, UNCLEAR])))
    resync(repos, ws, source, REDIS, LONG + "Redis keeps its data in memory.\n")

    def after_edit(req):
        refs = {i.quote: ref for ref, i in req.open_issues.items()}
        return reply(status=[(refs[ERROR.quote], "fixed"), (refs[UNCLEAR.quote], "still_present")])

    run = run_review(repos, ws, Script(Redis=after_edit), everything=False)
    fixed = run.reviewed[0].fixed_issues
    assert [(i.quote, i.status, i.closed_by) for i in fixed] == [
        (ERROR.quote, IssueStatus.FIXED, "review")
    ]
    assert [i.quote for i in open_issues(repos, ws)] == [UNCLEAR.quote]

    from learnova.report import render_review

    user = repos.users.get_or_create_local()
    ranked = issues_service.ranked(repos, IssueWeights(), ws.id)
    text = render_review(run, "m", repos.knowledge.list_for_user(user.id), ranked, T1)
    fixed_section = text.split("## Fixed this run")[1]
    assert "**Factual error** in Interview / Redis: Redis stores data on disk only" in fixed_section
    assert "1 fixed · 1 open in this workspace" in text


def test_unmentioned_open_issues_stay_open(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run_review(repos, ws, Script(Redis=lambda r: reply(issues=[ERROR])))
    resync(repos, ws, source, REDIS, LONG + "Unrelated line.\n")
    run = run_review(repos, ws, Script(Redis=lambda r: reply()))
    assert run.reviewed[0].warnings == ["1 open issue(s) not mentioned; kept open."]
    assert len(open_issues(repos, ws)) == 1


def test_dismissed_issues_are_not_raised_again(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run_review(repos, ws, Script(Redis=lambda r: reply(issues=[UNCLEAR], missing=[MISSING])))
    for issue in open_issues(repos, ws):
        issues_service.change_status(repos, str(issue.id), "dismiss", T1)
    resync(repos, ws, source, REDIS, LONG + "Unrelated line.\n")

    requests = []

    def again(req):
        requests.append(req)  # the model is told about them …
        return reply(issues=[UNCLEAR], missing=[Missing(topic="replication", why="again")])

    run = run_review(repos, ws, Script(Redis=again))
    assert sorted(requests[0].dismissed) == ["D1", "D2"] and requests[0].open_issues == {}
    assert run.reviewed[0].new_issues == []  # … and the fingerprint check catches a repeat
    assert open_issues(repos, ws) == []


def test_deleted_notes_make_issues_obsolete_and_ignored_notes_hide_them(repos, tmp_path):
    from test_ignore_sync import ALL, BEH, STAR, TOP

    user = repos.users.get_or_create_local()
    ws = repos.workspaces.add(user.id, "w", TOP, tmp_path / "w")
    ws.path.mkdir()
    sync_workspace(ws, IgnoringSource(dict(ALL)), repos.notes, repos.images)
    run_review(repos, ws, Script(
        Interview=lambda r: reply(), Redis=lambda r: reply(issues=[ERROR]),
        Behaviour=lambda r: reply(issues=[UNCLEAR]), STAR=lambda r: reply(missing=[MISSING]),
    ))  # fmt: skip
    assert len(open_issues(repos, ws)) == 3

    from test_ignore_sync import HIDE_BEHAVIOUR, visible

    # Behaviour (and STAR under it) ignored: their issues are hidden, not closed.
    sync_workspace(ws, IgnoringSource(visible(ALL, BEH, STAR), HIDE_BEHAVIOUR), repos.notes,
                   repos.images)  # fmt: skip
    assert [i.quote for i in open_issues(repos, ws)] == [ERROR.quote]
    # Un-ignored: back again.
    sync_workspace(ws, IgnoringSource(dict(ALL)), repos.notes, repos.images)
    assert len(open_issues(repos, ws)) == 3

    # Redis deleted in Notion: its issue becomes obsolete and keeps where it was.
    sync_workspace(ws, IgnoringSource(visible(ALL, "r1")), repos.notes, repos.images)
    obsolete = repos.issues.list(user.id, ws.id, [IssueStatus.OBSOLETE])
    assert [(i.quote, i.note_id, i.note_path) for i in obsolete] == [
        (ERROR.quote, None, ("Interview", "Redis"))
    ]
    with pytest.raises(issues_service.IssueStatusConflict, match="note was deleted"):
        issues_service.change_status(repos, str(obsolete[0].id), "reopen", T1)


def test_merge_moves_issues_to_the_kept_point(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run = run_review(repos, ws, Script(Redis=lambda r: reply(
        new=[("RDB", 5), ("RDB snapshots", 3)], issues=[ERROR.model_copy(update={"point": "N2"})]
    )))  # fmt: skip
    keep, dup = (o.point for o in run.reviewed[0].outcomes)
    knowledge.merge_points(repos, str(dup.id), str(keep.id))
    assert open_issues(repos, ws)[0].point_id == keep.id


# --- `learnova issues` -----------------------------------------------------------------------


def test_issues_command_lists_by_priority_and_changes_status(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run_review(repos, ws, Script(Redis=lambda r: reply(
        new=[("Persistence", 5)], issues=[UNCLEAR, ERROR.model_copy(update={"point": "N1"})],
        missing=[MISSING],
    )))  # fmt: skip
    from learnova.cli.main import app

    runner = CliRunner()
    r = runner.invoke(app, ["issues"])
    assert r.exit_code == 0, r.output
    rows = [line for line in r.output.splitlines() if "  " in line and line[:8].isalnum()]
    kinds = [line.split()[2] for line in rows]
    assert kinds == ["error", "unclear", "missing"]  # 3×5×1.5 > 2×3×1.5 > 1×3×1.5
    assert "22.5" in r.output and "3 issue(s)." in r.output

    r = runner.invoke(app, ["issues", "--kind", "missing", "-w", "w"])
    assert "Replication" in r.output and "1 issue(s)." in r.output

    error_id = next(i for i in open_issues(repos, ws) if i.kind == IssueKind.FACTUAL_ERROR).id
    short = str(error_id)[:8]
    r = runner.invoke(app, ["issues", "dismiss", short])
    assert r.exit_code == 0 and f"Issue {short} is now dismissed" in r.output
    assert runner.invoke(app, ["issues", "dismiss", short]).exit_code == 1  # not open any more
    assert "2 issue(s)." in runner.invoke(app, ["issues"]).output
    assert "dismissed" in runner.invoke(app, ["issues", "--status", "dismissed"]).output
    assert "is now open" in runner.invoke(app, ["issues", "reopen", short]).output
    assert "is now fixed" in runner.invoke(app, ["issues", "fix", short]).output
    r = runner.invoke(app, ["issues", "fix", "abc"])
    assert r.exit_code == 1 and "at least the first 8 characters" in r.output
    r = runner.invoke(app, ["issues", "fix", "00000000"])
    assert r.exit_code == 1 and "No issue with id" in r.output


def test_report_lists_other_open_issues_by_note(repos, tmp_path):
    ws, _ = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    many = [Issue(kind="unclear", quote=f"passage {n}", problem=f"vague {n}") for n in range(7)]
    run = run_review(repos, ws, Script(Redis=lambda r: reply(issues=many)))
    from learnova.report import render_review

    user = repos.users.get_or_create_local()
    ranked = issues_service.ranked(repos, IssueWeights(), ws.id)
    text = render_review(run, "m", repos.knowledge.list_for_user(user.id), ranked, T1)
    fix_first = text.split("## Fix first")[1].split("## Fixed this run")[0]
    assert fix_first.count("**Unclear**") == 5
    other = text.split("## Other open issues")[1]
    assert other.startswith("\n\n### Interview / Redis") and other.count("**Unclear**") == 2


# --- the same issue, reworded ----------------------------------------------------------------

# Real cases from a review: the model quoted a shorter piece of an already open issue's passage.
TTL_QUOTE = "I’d use longer TTLs, or sliding expiration for user data."
STATS_QUOTE = (
    "check `evicted_keys` and `expired_keys` in `INFO stats`, and track hit rates per key prefix"
)


def old(kind, quote="", topic="", block_id=None):
    import uuid

    return ReviewIssue(
        id=uuid.uuid4(), user_id=uuid.uuid4(), workspace_id=uuid.uuid4(), note_id=None,
        note_path=("Interview", "Redis"), kind=kind, quote=quote, topic=topic, problem="p",
        point_id=None, status=IssueStatus.OPEN,
        fingerprint=issues_service.fingerprint(kind, quote, topic), first_seen=T1, last_seen=T1,
        closed_at=None, closed_by=None, block_id=block_id,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "quote", "topic", "block", "existing", "same"),
    [
        ("unclear", "sliding expiration for user data", "", None, old("unclear", TTL_QUOTE), True),
        ("unclear", "track hit rates per key prefix", "", None, old("unclear", STATS_QUOTE), True),
        ("unclear", TTL_QUOTE, "", None, old("unclear", "sliding expiration for user data"), True),
        ("unclear", "I'd use longer TTL, or sliding expiry for user data", "", None,
         old("unclear", TTL_QUOTE), True),  # 80% alike
        ("factual_error", "sliding expiration for user data", "", None,
         old("unclear", TTL_QUOTE), False),  # another kind
        ("unclear", "user data", "", None, old("unclear", TTL_QUOTE), False),  # too short
        ("unclear", "passage 1", "", None, old("unclear", "passage 2"), False),
        ("unclear", "RDB takes snapshots of the dataset", "", None,
         old("unclear", TTL_QUOTE), False),
        ("unclear", "RDB takes snapshots", "", "b1", old("unclear", TTL_QUOTE, block_id="b1"),
         True),  # same block
        ("unclear", "RDB takes snapshots", "", None, old("unclear", TTL_QUOTE), False),
        ("missing", "", "Redis Sentinel", None, old("missing", topic="Replication"), False),
        ("missing", "", "Key expiration (TTLs)", None,
         old("missing", topic="Key expiration (TTL)"), True),
    ],
)  # fmt: skip
def test_is_same_issue(kind, quote, topic, block, existing, same):
    assert issues_service.is_same_issue(kind, quote, topic, block, existing) is same


def test_reworded_issue_is_not_raised_again(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    ttl = Issue(kind="unclear", quote=TTL_QUOTE, problem="Stale data?")
    run_review(repos, ws, Script(Redis=lambda r: reply(issues=[ttl])))
    [first] = open_issues(repos, ws)
    resync(repos, ws, source, REDIS, LONG + "Unrelated line.\n")

    # Once more, quoting part of the passage, and twice within this run in different words.
    again = [
        Issue(kind="unclear", quote="sliding expiration for user data", problem="Stale?"),
        Issue(kind="unclear", quote="RDB snapshots run on a timer", problem="Which timer?"),
        Issue(kind="unclear", quote="RDB snapshots run on a timer only", problem="Again."),
    ]
    run = run_review(repos, ws, Script(Redis=lambda r: reply(issues=again)), everything=False)
    assert [i.quote for i in run.reviewed[0].new_issues] == ["RDB snapshots run on a timer"]
    assert repos.issues.get(first.id).last_seen > first.last_seen  # seen again


# --- unchanged notes keep their issues -------------------------------------------------------


def test_unchanged_note_keeps_its_issues_until_it_changes(repos, tmp_path):
    ws, source = workspace(repos, tmp_path, "w", {REDIS: ("Redis", T1, LONG)})
    run = run_review(repos, ws, Script(Redis=lambda r: reply(issues=[ERROR, UNCLEAR])))
    assert not run.reviewed[0].issues_kept  # never reviewed for issues: full update
    before = {i.id: i for i in open_issues(repos, ws)}

    # `review --all` on the unchanged note: the model claims a fix and finds something new, but
    # the text is the same, so the issues stay exactly as they were.
    def noisy(req):
        return reply(
            status=[("I1", "fixed")], issues=[Issue(kind="unclear", quote="New", problem="?")]
        )

    run = run_review(repos, ws, Script(Redis=noisy))
    result = run.reviewed[0]
    assert result.issues_kept and result.new_issues == [] and result.fixed_issues == []
    assert result.warnings == [] and len(result.still_open) == 2
    assert {i.id: i for i in open_issues(repos, ws)} == before  # last_seen untouched too

    from learnova.report import render_review

    user = repos.users.get_or_create_local()
    ranked = issues_service.ranked(repos, IssueWeights(), ws.id)
    text = render_review(run, "m", repos.knowledge.list_for_user(user.id), ranked, T1)
    assert "Issues left as they were for 1 note(s) unchanged" in text

    # After an edit the same reply takes effect.
    resync(repos, ws, source, REDIS, LONG + "Redis keeps its data in memory.\n")
    run = run_review(repos, ws, Script(Redis=noisy), everything=False)
    result = run.reviewed[0]
    assert not result.issues_kept and len(result.fixed_issues) == 1
    assert [i.quote for i in result.new_issues] == ["New"]
