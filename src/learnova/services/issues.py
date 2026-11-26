"""Review issues: their fingerprints, their priority, and changing their status by hand. The
review itself opens and closes issues (services/review.py); this module is what `learnova issues`
and the report use."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from learnova.anchors import notion_url
from learnova.errors import ConflictError, LearnovaError, NotFoundError
from learnova.models import IssueKind, IssueStatus, KnowledgePoint, Mastery, ReviewIssue
from learnova.scheduler.priority import IssueWeights, priority
from learnova.storage.database import Repositories


class IssueError(LearnovaError):
    pass


class NoSuchIssue(IssueError, NotFoundError):
    pass


class IssueStatusConflict(IssueError, ConflictError):
    pass


def _normal(text: str) -> str:
    """Casefold, drop punctuation and collapse spaces, so small rewordings of spacing or
    punctuation don't count as a different issue."""
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def fingerprint(kind: str, quote: str = "", topic: str = "") -> str:
    """kind + normalized quote, or kind + normalized topic for `missing`."""
    text = topic if kind == IssueKind.MISSING else quote
    digest = hashlib.sha256(_normal(text).encode()).hexdigest()[:32]
    return f"{kind}:{digest}"


SAME_ISSUE_SIMILARITY = 0.8  # quotes (or `missing` topics) at least this similar are one issue
MIN_FUZZY_WORDS = 3  # shorter quotes only match exactly ("passage 1" is not "passage 2")


def is_same_issue(
    kind: str, quote: str, topic: str, block_id: str | None, existing: ReviewIssue
) -> bool:
    """Whether a newly found issue is really `existing` again, worded a little differently.

    Same kind, and one of: one quote contains the other; the quotes (or, for `missing`, the
    topics) are at least SAME_ISSUE_SIMILARITY alike; both are anchored to the same block.
    The first two need quotes of at least MIN_FUZZY_WORDS words."""
    if kind != existing.kind:
        return False
    if kind == IssueKind.MISSING:
        a, b = _normal(topic), _normal(existing.topic)
        return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= SAME_ISSUE_SIMILARITY
    a, b = _normal(quote), _normal(existing.quote)
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    if len(shorter.split()) >= MIN_FUZZY_WORDS and (
        f" {shorter} " in f" {longer} "
        or SequenceMatcher(None, a, b).ratio() >= SAME_ISSUE_SIMILARITY
    ):
        return True
    return block_id is not None and block_id == existing.block_id


@dataclass(frozen=True)
class RankedIssue:
    issue: ReviewIssue
    priority: float
    point: KnowledgePoint | None
    mastery: Mastery | None
    url: str | None = None  # Notion, at the quoted block when known; None for obsolete issues


def ranked(
    repos: Repositories,
    weights: IssueWeights,
    workspace_id=None,
    statuses: list[IssueStatus] | None = None,
    kinds: list[IssueKind] | None = None,
) -> list[RankedIssue]:
    """Issues by priority, highest first (ties: oldest first)."""
    user = repos.users.get_or_create_local()
    issues = repos.issues.list(user.id, workspace_id, statuses or [IssueStatus.OPEN], kinds)
    mastery = repos.attempts.mastery(user.id)
    points = {p.id: p for p in repos.knowledge.list_for_user(user.id)}
    pages = {}  # note id -> Notion page id
    for ws in repos.workspaces.list(user.id):
        if workspace_id is None or ws.id == workspace_id:
            pages.update({n.id: n.source_id for n in repos.notes.list(ws.id)})
    out = []
    for issue in issues:
        point = points.get(issue.point_id) if issue.point_id else None
        m = mastery.get(point.id) if point else None
        value = priority(
            issue.kind, point.importance if point else None, m.mastery if m else None, weights
        )
        page = pages.get(issue.note_id) if issue.note_id else None
        url = notion_url(page, issue.block_id) if page else None
        out.append(RankedIssue(issue, value, point, m, url))
    return sorted(out, key=lambda r: (-r.priority, r.issue.first_seen))


def find_issue(repos: Repositories, text: str) -> ReviewIssue:
    """By id, or an id prefix of at least 8 characters (as shown by `learnova issues`)."""
    needle = text.strip().lower()
    if len(needle) < 8:
        raise IssueError("Give at least the first 8 characters of the issue id.")
    user = repos.users.get_or_create_local()
    every = list(IssueStatus)
    matches = [
        i for i in repos.issues.list(user.id, statuses=every) if str(i.id).startswith(needle)
    ]
    if not matches:
        raise NoSuchIssue(f"No issue with id {text!r}.")
    if len(matches) > 1:
        raise IssueError(f"{len(matches)} issues start with {text!r}; give more of the id.")
    return matches[0]


ACTIONS = {
    "fix": (IssueStatus.FIXED, {IssueStatus.OPEN}),
    "dismiss": (IssueStatus.DISMISSED, {IssueStatus.OPEN}),
    "reopen": (IssueStatus.OPEN, {IssueStatus.FIXED, IssueStatus.DISMISSED}),
}


def change_status(repos: Repositories, text: str, action: str, now: datetime) -> ReviewIssue:
    """fix / dismiss an open issue, or reopen a fixed or dismissed one."""
    issue = find_issue(repos, text)
    target, allowed = ACTIONS[action]
    if issue.status == IssueStatus.OBSOLETE:
        raise IssueStatusConflict("That issue's note was deleted; it can't be changed.")
    if issue.status not in allowed:
        raise IssueStatusConflict(f"That issue is {issue.status.value}; it can't be {action}ed.")
    return repos.issues.set_status(issue.id, target, now, None if action == "reopen" else "user")
