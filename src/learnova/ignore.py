""".learnovaignore: sub-pages a workspace leaves out. Pure matching, no Notion or database.

One pattern per line, like .gitignore but matching page titles:

    Behaviour Questions        a page with this title, at any depth (and everything under it)
    Draft*                     * and ? work within a title
    Database / Old notes       a path of titles, counted from the workspace's top page
    **/Scratch                 ** matches any number of levels
    3e6135e3ecf0…  or a URL    a page by its Notion ID (survives renames)
    !Draft – final             ! brings a page back; the last matching line wins
    # comment                  comments and blank lines are skipped

Matching ignores case. Pages under an ignored page are never read, so `!` can't bring one back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from learnova.errors import LearnovaError

IGNORE_FILE = ".learnovaignore"

_ID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)

TEMPLATE = """\
# Sub-pages this workspace leaves out. One pattern per line; matching ignores case.
#
#   Behaviour Questions        a page with this title, at any depth, and everything under it
#   Draft*                     * and ? work within a title
#   Database / Old notes       a path of titles, counted from the workspace's top page
#   **/Scratch                 ** matches any number of levels
#   3e6135e3ecf04389846c…      a page by its Notion ID or URL (keeps working after renames)
#   !Draft – final             ! brings a page back; the last matching line wins
#
# Ignored pages are hidden from review and quiz but kept, so removing a line restores them.
"""


class IgnoreFileError(LearnovaError):
    pass


def _normal_id(text: str) -> str | None:
    """A Notion page ID (or URL containing one) as a dashed lowercase UUID, else None."""
    candidate = text.split("?", 1)[0].split("#", 1)[0]
    is_url = candidate.startswith(("http://", "https://"))
    matches = _ID.findall(candidate)
    if not matches or (not is_url and _ID.fullmatch(candidate) is None):
        return None
    h = matches[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _segments_match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(_segments_match(pattern[1:], path[i:]) for i in range(len(path) + 1))
    return (
        bool(path) and fnmatchcase(path[0], pattern[0]) and _segments_match(pattern[1:], path[1:])
    )


@dataclass(frozen=True)
class Rule:
    line: int
    negate: bool
    page_id: str | None = None  # an ID rule
    segments: tuple[str, ...] = ()  # lowercased titles / globs
    anchored: bool = False  # a path from the top page (contains "/")

    def matches(self, path: tuple[str, ...], page_id: str) -> bool:
        if self.page_id is not None:
            return page_id.replace("-", "").lower() == self.page_id.replace("-", "")
        lowered = tuple(t.lower() for t in path)
        if not self.anchored:
            return bool(lowered) and fnmatchcase(lowered[-1], self.segments[0])
        return _segments_match(self.segments, lowered)


@dataclass(frozen=True)
class IgnoreRules:
    rules: tuple[Rule, ...] = ()

    def ignores(self, path: tuple[str, ...], page_id: str) -> bool:
        """`path` is the page's titles below the workspace's top page (top page excluded)."""
        if not path:
            return False  # the top page is the workspace itself
        ignored = False
        for rule in self.rules:
            if rule.matches(path, page_id):
                ignored = not rule.negate
        return ignored

    def __bool__(self) -> bool:
        return bool(self.rules)


def parse(text: str) -> IgnoreRules:
    rules = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        body = line[1:].strip() if negate else line
        if not body:
            raise IgnoreFileError(f"{IGNORE_FILE} line {number}: nothing after '!'.")
        page_id = _normal_id(body)
        if page_id is not None:
            rules.append(Rule(number, negate, page_id=page_id))
            continue
        anchored = "/" in body
        segments = tuple(s.strip().lower() for s in body.strip("/").split("/"))
        if any(not s for s in segments):
            raise IgnoreFileError(
                f"{IGNORE_FILE} line {number}: {raw.strip()!r} has an empty title between '/'."
            )
        if not anchored and segments == ("**",):
            raise IgnoreFileError(
                f"{IGNORE_FILE} line {number}: '**' alone would ignore every page."
            )
        rules.append(Rule(number, negate, segments=segments, anchored=anchored))
    return IgnoreRules(tuple(rules))


def load(workspace_dir: Path) -> IgnoreRules:
    path = workspace_dir / IGNORE_FILE
    return parse(path.read_text(encoding="utf-8")) if path.is_file() else IgnoreRules()
