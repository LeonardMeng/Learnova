"""`learnova web check`: compares the site with the local cache, never with Notion, and never
writes to Notion. Changed articles, new articles in the index and removed ones go into a
Markdown report for you to decide what belongs in your notes. After the report is written the
cache holds the new version, so each change is reported once."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from learnova.errors import LearnovaError
from learnova.models import WebArticle, WebArticleState, WebImport
from learnova.storage.repositories import WebImportRepository
from learnova.webimport import gfg
from learnova.webimport.diff import ArticleDiff, compare, content_hash
from learnova.webimport.fetch import Fetcher
from learnova.webimport.html_blocks import html_to_blocks
from learnova.webimport.importer import ERRORS, import_dir
from learnova.webimport.markdown import blocks_to_markdown, cache_path

REPORTS_DIR = "reports"


class ImportNotFinished(LearnovaError):
    pass


class CheckOutcome(StrEnum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    NEW = "new"  # in the index, not imported
    REMOVED = "removed"
    RESTORED = "restored"  # back in the index
    ERROR = "error"


@dataclass(frozen=True)
class CheckResult:
    url: str
    title: str
    section: tuple[str, ...]
    outcome: CheckOutcome
    notion_page_id: str | None = None
    old_modified: str | None = None
    new_modified: str | None = None
    diff: ArticleDiff | None = None
    detail: str = ""


@dataclass
class CheckReport:
    index_url: str
    checked_at: datetime
    results: list[CheckResult] = field(default_factory=list)
    path: Path | None = None  # the report file, when there was anything to report

    def of(self, outcome: CheckOutcome) -> list[CheckResult]:
        return [r for r in self.results if r.outcome == outcome]


class CheckRun:
    def __init__(
        self,
        repo: WebImportRepository,
        fetcher: Fetcher,
        *,
        base_dir: Path | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_result: Callable[[CheckResult], None] = lambda r: None,
    ):
        self.repo = repo
        self.fetcher = fetcher
        self.base_dir = base_dir
        self.now = now
        self.on_result = on_result

    def run(self, imp: WebImport) -> CheckReport:
        if imp.completed_at is None:
            raise ImportNotFinished(
                f"The import of {imp.index_url} isn't finished. Run "
                f"`learnova web import {imp.index_url}` first."
            )
        folder = import_dir(imp, self.base_dir)
        report = CheckReport(index_url=imp.index_url, checked_at=self.now())
        index = self.fetcher.get(imp.index_url)
        entries = gfg.parse_index(index.html or "", imp.index_url)
        known = {a.url: a for a in self.repo.articles(imp.id)}

        def add(result: CheckResult) -> None:
            report.results.append(result)
            self.on_result(result)

        for entry in entries:
            art = known.get(entry.url)
            if art is None:
                self.repo.new_article(
                    imp.id, entry.url, entry.section, entry.link_text, WebArticleState.NEW,
                    self.now(),
                )  # fmt: skip
                add(CheckResult(entry.url, entry.link_text, entry.section, CheckOutcome.NEW))
                continue
            if art.state == WebArticleState.NEW:
                continue  # reported when it first appeared
            restored = art.state == WebArticleState.REMOVED
            try:
                result = self._compare(folder, entry, art)
            except ERRORS as e:
                result = CheckResult(
                    entry.url, art.title, entry.section, CheckOutcome.ERROR, art.notion_page_id,
                    detail=str(e),
                )  # fmt: skip
            if restored:
                self.repo.save_article(
                    self._current(art).model_copy(update={"state": WebArticleState.IMPORTED})
                )
                add(CheckResult(entry.url, art.title, entry.section, CheckOutcome.RESTORED,
                                art.notion_page_id))  # fmt: skip
            add(result)

        listed = {e.url for e in entries}
        for url, art in known.items():
            if url not in listed and art.state == WebArticleState.IMPORTED:
                self.repo.save_article(art.model_copy(update={"state": WebArticleState.REMOVED}))
                add(CheckResult(url, art.title, art.section, CheckOutcome.REMOVED,
                                art.notion_page_id))  # fmt: skip

        if any(r.outcome != CheckOutcome.UNCHANGED for r in report.results):
            report.path = write_report(folder / REPORTS_DIR, report)
        return report

    def _current(self, art: WebArticle) -> WebArticle:
        return next(a for a in self.repo.articles(art.import_id) if a.id == art.id)

    def _compare(self, folder: Path, entry: gfg.IndexEntry, art: WebArticle) -> CheckResult:
        def unchanged(**update) -> CheckResult:
            self.repo.save_article(
                art.model_copy(
                    update={"checked_at": self.now(), "section": entry.section, **update}
                )
            )
            return CheckResult(entry.url, art.title, entry.section, CheckOutcome.UNCHANGED,
                               art.notion_page_id)  # fmt: skip

        page = self.fetcher.get(entry.url, etag=art.etag)
        if page.not_modified:
            return unchanged()
        parsed = gfg.parse_article(page.html or "", entry.url)
        if parsed.modified and parsed.modified == art.source_modified:
            return unchanged(etag=page.etag)
        markdown = blocks_to_markdown(html_to_blocks(parsed.body, entry.url))
        digest = content_hash(markdown)
        if digest == art.content_hash:
            return unchanged(etag=page.etag, source_modified=parsed.modified)

        relative = Path(art.cache_path) if art.cache_path else cache_path(entry.url)
        path = folder / relative
        old = path.read_text() if path.is_file() else ""
        diff = compare(old, markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown)  # the cache moves on: this change is reported once
        self.repo.save_article(
            art.model_copy(
                update={
                    "section": entry.section,
                    "source_modified": parsed.modified,
                    "etag": page.etag,
                    "content_hash": digest,
                    "cache_path": relative.as_posix(),
                    "checked_at": self.now(),
                }
            )
        )
        return CheckResult(
            entry.url, art.title, entry.section, CheckOutcome.CHANGED, art.notion_page_id,
            old_modified=art.source_modified, new_modified=parsed.modified, diff=diff,
            detail="" if old else "缓存文件不见了，已重新保存，无法对比",
        )  # fmt: skip


# ---- the report ---------------------------------------------------------------------------


def write_report(folder: Path, report: CheckReport) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    day = report.checked_at.astimezone().strftime("%Y-%m-%d")
    path, n = folder / f"{day}-check.md", 2
    while path.exists():
        path, n = folder / f"{day}-check-{n}.md", n + 1
    path.write_text(render(report))
    return path


def render(report: CheckReport) -> str:
    changed = report.of(CheckOutcome.CHANGED)
    new = report.of(CheckOutcome.NEW)
    removed = report.of(CheckOutcome.REMOVED)
    restored = report.of(CheckOutcome.RESTORED)
    errors = report.of(CheckOutcome.ERROR)
    checked = len(report.of(CheckOutcome.UNCHANGED)) + len(changed) + len(errors)
    when = report.checked_at.astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 原文更新检查",
        "",
        f"{report.index_url}",
        "",
        f"{when} · 检查 {checked} 篇 · {len(changed)} 篇有改动 · {len(new)} 篇新文章 · "
        f"{len(removed)} 篇已移除",
        "",
        "本地缓存已更新为这次的版本，下次检查只会报告之后的改动。要不要把改动写进笔记，由你决定。",
    ]
    if changed:
        lines += ["", "## 有改动的文章"]
        for r in changed:
            lines += _changed(r)
    if new:
        lines += ["", "## 目录里的新文章（没有导入 Notion）", ""]
        lines += [f"- [{r.title}]({r.url}) · {' / '.join(r.section)}" for r in new]
    if removed:
        lines += ["", "## 已从目录移除（Notion 页面保留不动）", ""]
        lines += [f"- {_notion_link(r)} · [原网址]({r.url})" for r in removed]
    if restored:
        lines += ["", "## 重新出现在目录里", ""]
        lines += [f"- {_notion_link(r)} · [原文]({r.url})" for r in restored]
    if errors:
        lines += ["", "## 出错（下次检查会重试）", ""]
        lines += [f"- [{r.title}]({r.url}): {r.detail}" for r in errors]
    return "\n".join(lines) + "\n"


def _changed(r: CheckResult) -> list[str]:
    assert r.diff is not None
    dates = f"原文更新于 {_date(r.old_modified)} → {_date(r.new_modified)}"
    lines = [
        "",
        f"### {_notion_link(r)}",
        "",
        f"[原文]({r.url}) · {' / '.join(r.section)} · {dates}",
        "",
        f"**{r.detail or r.diff.describe()}**",
    ]
    for heading, diff_lines in r.diff.changed:
        lines += ["", f"#### 修改「{heading}」", "", "````diff", *diff_lines, "````"]
    for heading, body in r.diff.added:
        lines += ["", f"#### 新增「{heading}」", "", "````diff"]
        lines += [f"+{line}" for line in body.splitlines()] + ["````"]
    for heading, body in r.diff.removed:
        lines += ["", f"#### 删除「{heading}」", "", "````diff"]
        lines += [f"-{line}" for line in body.splitlines()] + ["````"]
    return lines


def _notion_link(r: CheckResult) -> str:
    if not r.notion_page_id:
        return r.title
    return f"[{r.title}](https://www.notion.so/{r.notion_page_id.replace('-', '')})"


def _date(modified: str | None) -> str:
    return modified[:10] if modified else "未知"
