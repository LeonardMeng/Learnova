"""`learnova web import`: the one-time copy of a tutorial into Notion. The `--into` page is laid
out like the index page (introduction, headings, descriptions), with each linked article as a
sub-page in the place of its link; the article page holds the original text and your notes. The
same text is saved as Markdown in the local cache for `web check`.

Notion adds a new sub-page at the end of its parent, so the layout is written strictly in order
and the run stops at the first error: running it again continues from that step (`cursor`), so
the order stays right. Once everything is written the import is complete and never writes to
Notion again."""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from notion_client.errors import APIResponseError

from learnova.errors import ConflictError, LearnovaError
from learnova.home import home_dir
from learnova.models import WebArticle, WebArticleState, WebImport
from learnova.sources.notion import parse_page_id
from learnova.storage.repositories import WebImportRepository
from learnova.webimport import gfg
from learnova.webimport.diff import content_hash
from learnova.webimport.fetch import Fetcher, FetchError
from learnova.webimport.html_blocks import Block, html_to_blocks
from learnova.webimport.markdown import blocks_to_markdown, cache_path
from learnova.webimport.notion_writer import NotionWriter

ERRORS = (APIResponseError, FetchError, gfg.ParseError, httpx.HTTPError, OSError)


class UnsupportedSite(LearnovaError):
    pass


class AlreadyImported(ConflictError):
    pass


def import_dir(imp: WebImport, base: Path | None = None) -> Path:
    """<user home>/web/<slug>: the cache/ and reports/ of one import."""
    return (base or home_dir() / "web") / imp.slug


def forget(repo: WebImportRepository, imp: WebImport, base: Path | None = None) -> Path:
    """Drop an import's records and its cache/ (reports/ are kept), so the tutorial can be
    imported again from scratch. Notion is not touched. Returns the import's folder."""
    repo.delete(imp.id)
    folder = import_dir(imp, base)
    shutil.rmtree(folder / "cache", ignore_errors=True)
    return folder


def register(
    repo: WebImportRepository, user_id: uuid.UUID, index_url: str, into: str | None
) -> WebImport:
    """The import for this index page: a new one (needs --into), or an unfinished one to carry
    on with. A finished import is refused: updates are only checked, never imported."""
    if not gfg.supports(index_url):
        raise UnsupportedSite("Only GeeksforGeeks tutorials are supported for now.")
    url = gfg.normalize_url(index_url, index_url)
    existing = repo.get_by_url(user_id, url)
    target = parse_page_id(into) if into else None
    if existing is not None:
        if existing.completed_at is not None:
            raise AlreadyImported(
                f"{url} was already imported in full on {_day(existing.completed_at)}. "
                "Use `learnova web check` to see what changed since."
            )
        if target and not _same_id(target, existing.target_page_id):
            raise ConflictError(
                f"{url} is being imported into another Notion page ({existing.target_page_id}). "
                "Leave out --into to carry on with it."
            )
        return existing
    if target is None:
        raise LearnovaError("Say where the tutorial goes in Notion with --into <page>.")
    taken = {i.slug for i in repo.list(user_id)}
    return repo.add(user_id, url, target, _slug(url, taken))


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    ERROR = "error"


@dataclass(frozen=True)
class ImportResult:
    url: str
    title: str
    section: tuple[str, ...]
    outcome: ImportOutcome
    detail: str = ""


@dataclass
class ImportReport:
    results: list[ImportResult] = field(default_factory=list)
    remaining: int = 0  # listed articles not in Notion yet
    completed: bool = False
    stopped: ImportResult | None = None  # the error the run stopped at


class LayoutChanged(ConflictError):
    pass


class ImportRun:
    def __init__(
        self,
        repo: WebImportRepository,
        fetcher: Fetcher,
        writer: NotionWriter,
        *,
        base_dir: Path | None = None,
        limit: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_result: Callable[[ImportResult], None] = lambda r: None,
    ):
        self.repo = repo
        self.fetcher = fetcher
        self.writer = writer
        self.base_dir = base_dir
        self.limit = limit
        self.now = now
        self.on_result = on_result

    def run(self, imp: WebImport) -> ImportReport:
        index = self.fetcher.get(imp.index_url)
        items = gfg.parse_index_layout(index.html or "", imp.index_url)
        digest = layout_hash(items)
        if imp.cursor and imp.layout_hash != digest:
            raise LayoutChanged(
                "The index page changed while the import was unfinished, so the rest can't be "
                "put in the right places. Delete the imported pages in Notion, run "
                f"`learnova web forget {imp.index_url}`, then import again."
            )
        known = {a.url: a for a in self.repo.articles(imp.id)}
        report = ImportReport()
        cursor = imp.cursor
        imported = 0
        while cursor < len(items):
            item = items[cursor]
            if item.article is not None and not _done(known.get(item.article.url)):
                if self.limit is not None and imported >= self.limit:
                    break
                entry = item.article
                try:
                    art = self._one(imp, entry, known.get(entry.url))
                except ERRORS as e:
                    result = ImportResult(
                        entry.url, entry.link_text, entry.section, ImportOutcome.ERROR, str(e)
                    )
                    report.results.append(result)
                    report.stopped = result
                    self.on_result(result)
                    break
                known[entry.url] = art
                imported += 1
                result = ImportResult(entry.url, art.title, entry.section, ImportOutcome.IMPORTED)
                report.results.append(result)
                self.on_result(result)
            elif item.content is not None:
                self.writer.append(imp.target_page_id, html_to_blocks(item.content, imp.index_url))
            cursor += 1
            self.repo.set_progress(imp.id, cursor, digest)
        report.remaining = sum(
            1 for item in items[cursor:] if item.article and not _done(known.get(item.article.url))
        )
        if cursor == len(items):
            self.repo.mark_completed(imp.id, self.now())
            report.completed = True
        return report

    def _one(self, imp: WebImport, entry: gfg.IndexEntry, art: WebArticle | None) -> WebArticle:
        page = self.fetcher.get(entry.url)
        parsed = gfg.parse_article(page.html or "", entry.url)
        blocks = html_to_blocks(parsed.body, entry.url)
        markdown = blocks_to_markdown(blocks)
        if art is None:
            art = self.repo.new_article(
                imp.id, entry.url, entry.section, parsed.title, WebArticleState.IMPORTED,
                self.now(),
            )  # fmt: skip
        if art.notion_page_id is None:
            page_id = self.writer.create_page(imp.target_page_id, parsed.title)
            art = self.repo.save_article(art.model_copy(update={"notion_page_id": page_id}))
        else:  # an earlier run stopped halfway through this page; it is already in its place
            self.writer.clear(art.notion_page_id)
        assert art.notion_page_id is not None
        self.writer.append(art.notion_page_id, _source_line(entry.url) + blocks)
        relative = cache_path(entry.url)
        path = import_dir(imp, self.base_dir) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown)
        return self.repo.save_article(
            art.model_copy(
                update={
                    "title": parsed.title,
                    "section": entry.section,
                    "source_modified": parsed.modified,
                    "etag": page.etag,
                    "content_hash": content_hash(markdown),
                    "cache_path": relative.as_posix(),
                    "imported_at": self.now(),
                    "checked_at": self.now(),
                }
            )
        )


def layout_hash(items: list[gfg.LayoutItem]) -> str:
    """Identifies the index layout: the order of its contents and articles."""
    parts = [
        f"a:{i.article.url}"
        if i.article
        else f"c:{i.content.get_text(' ', strip=True) if i.content else ''}"
        for i in items
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _done(art: WebArticle | None) -> bool:
    return art is not None and art.content_hash is not None


def _source_line(url: str) -> list[Block]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "原文："}},
                    {"type": "text", "text": {"content": url, "link": {"url": url}}},
                ]
            },
        }
    ]


def _slug(url: str, taken: set[str]) -> str:
    parts = urlsplit(url)
    site = (parts.hostname or "site").removeprefix("www.").split(".")[0]
    last = [p for p in parts.path.split("/") if p][-1:] or ["index"]
    base = re.sub(r"[^a-z0-9]+", "-", f"{site}-{last[0]}".lower()).strip("-")
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _same_id(a: str, b: str) -> bool:
    return a.replace("-", "").lower() == b.replace("-", "").lower()


def _day(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d")
