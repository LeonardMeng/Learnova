"""Compares the cached Markdown of an article with the current version, section by section
(split at #, ## and ### headings outside code blocks), so the report can say which sections
were added, changed or removed and show the changed lines."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

INTRO = "(开头)"  # the text before the first heading
MAX_NAMES = 5
_HEADING = re.compile(r"^(#{1,3}) +(.*)$")

Sections = list[tuple[str, str]]


def content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()


def split_sections(markdown: str) -> Sections:
    """(heading, body) in order; repeated headings get " (2)", " (3)", ..."""
    out: list[tuple[str, list[str]]] = [(INTRO, [])]
    fenced = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        if m:
            out.append((m[2].replace("**", "").strip(), []))
        else:
            out[-1][1].append(line)
    counts: dict[str, int] = {}
    result: Sections = []
    for heading, lines in out:
        body = "\n".join(lines).strip()
        if heading == INTRO and not body:
            continue
        counts[heading] = counts.get(heading, 0) + 1
        name = heading if counts[heading] == 1 else f"{heading} ({counts[heading]})"
        result.append((name, body))
    return result


@dataclass(frozen=True)
class ArticleDiff:
    added: list[tuple[str, str]]  # (heading, new body)
    changed: list[tuple[str, list[str]]]  # (heading, unified diff lines)
    removed: list[tuple[str, str]]  # (heading, old body)

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    def describe(self) -> str:
        """新增「A」「B」，修改「C」，删除「D」"""
        parts = []
        for label, names in (
            ("新增", [h for h, _ in self.added]),
            ("修改", [h for h, _ in self.changed]),
            ("删除", [h for h, _ in self.removed]),
        ):
            if names:
                shown = "".join(f"「{n}」" for n in names[:MAX_NAMES])
                more = f" 等 {len(names)} 节" if len(names) > MAX_NAMES else ""
                parts.append(f"{label}{shown}{more}")
        return "，".join(parts) or "只有格式上的改动"


def compare(old_markdown: str, new_markdown: str) -> ArticleDiff:
    old, new = split_sections(old_markdown), split_sections(new_markdown)
    old_map, new_map = dict(old), dict(new)
    changed = []
    for heading, body in new:
        if heading in old_map and old_map[heading] != body:
            lines = list(
                difflib.unified_diff(
                    old_map[heading].splitlines(), body.splitlines(), lineterm="", n=1
                )
            )[2:]  # drop the ---/+++ file header
            changed.append((heading, lines))
    return ArticleDiff(
        added=[(h, b) for h, b in new if h not in old_map],
        changed=changed,
        removed=[(h, b) for h, b in old if h not in new_map],
    )
