"""Review issues: their fingerprints, their priority, and changing their status by hand. The
review itself opens and closes issues (services/review.py); this module is what `learnova issues`
and the report use."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True)
class RankedIssue:
    issue: ReviewIssue
    priority: float
    point: KnowledgePoint | None
    mastery: Mastery | None


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
    out = []
    for issue in issues:
        point = points.get(issue.point_id) if issue.point_id else None
        m = mastery.get(point.id) if point else None
        value = priority(
            issue.kind, point.importance if point else None, m.mastery if m else None, weights
        )
        out.append(RankedIssue(issue, value, point, m))
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
