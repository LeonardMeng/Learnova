"""Web tutorials: parsing GeeksforGeeks pages, converting HTML to Notion blocks and Markdown, the
one-time import into Notion, and `web check` against the local cache."""

from __future__ import annotations

import copy
import itertools
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from bs4 import BeautifulSoup

from learnova.errors import ConflictError, LearnovaError
from learnova.models import WebArticleState
from learnova.storage.database import open_repositories
from learnova.webimport import gfg
from learnova.webimport.checker import CheckOutcome, CheckRun, ImportNotFinished
from learnova.webimport.diff import compare, split_sections
from learnova.webimport.fetch import Fetcher, Page
from learnova.webimport.html_blocks import MAX_TEXT, html_to_blocks
from learnova.webimport.importer import (
    AlreadyImported,
    ImportOutcome,
    ImportRun,
    LayoutChanged,
    UnsupportedSite,
    register,
)
from learnova.webimport.markdown import blocks_to_markdown
from learnova.webimport.notion_writer import NotionWriter, batches

FIXTURES = Path(__file__).parent / "fixtures" / "gfg"
INDEX = "https://www.geeksforgeeks.org/artificial-intelligence/artificial-intelligence/"
ARTICLE = "https://www.geeksforgeeks.org/machine-learning/q-learning-in-python/"
TARGET = "3e6135e3ecf04389846ce4126df126b0"
A1 = "https://www.geeksforgeeks.org/artificial-intelligence/intro/"
A2 = "https://www.geeksforgeeks.org/machine-learning/linear-regression/"
A3 = "https://www.geeksforgeeks.org/machine-learning/k-means/"


# ---- fakes ------------------------------------------------------------------------------


class FakeNotion:
    """An in-memory Notion: pages and blocks in one dict, children lists per parent. Counts
    every write so a test can check that a run wrote nothing."""

    def __init__(self):
        self.blocks: dict[str, dict[str, Any]] = {}
        self.children: dict[str, list[str]] = {TARGET: []}
        self.writes = 0
        self._ids = (f"id-{n}" for n in itertools.count(1))
        self.pages = _Ns(create=self._create_page)
        self.blocks_api = _Ns(
            delete=self._delete,
            update=self._update,
            retrieve=lambda block_id: self.blocks[block_id],
            children=_Ns(append=self._append, list=self._list),
        )

    def _create_page(self, parent, properties):
        self.writes += 1
        page_id = next(self._ids)
        title = properties["title"]["title"][0]["text"]["content"]
        self.blocks[page_id] = {"id": page_id, "type": "child_page", "child_page": {"title": title}}
        self.children.setdefault(parent["page_id"], []).append(page_id)
        self.children[page_id] = []
        return {"id": page_id}

    def _append(self, block_id, children):
        self.writes += 1
        assert len(children) <= 100
        return {"results": [self._add(block_id, copy.deepcopy(c)) for c in children]}

    def _add(self, parent: str, block: dict) -> dict:
        block_id = next(self._ids)
        kids = block[block["type"]].pop("children", [])
        block["id"] = block_id
        self.blocks[block_id] = block
        self.children.setdefault(parent, []).append(block_id)
        self.children[block_id] = []
        for kid in kids:
            self._add(block_id, kid)
        return block

    def _list(self, block_id, start_cursor=None, **_):
        return {
            "results": [self.blocks[i] for i in self.children.get(block_id, [])],
            "has_more": False,
            "next_cursor": None,
        }

    def _delete(self, block_id):
        self.writes += 1
        for kids in self.children.values():
            if block_id in kids:
                kids.remove(block_id)

    def _update(self, block_id, **payload):
        self.writes += 1
        ((kind, data),) = payload.items()
        self.blocks[block_id][kind].update(data)
        return self.blocks[block_id]

    # helpers for assertions
    def child_pages(self, parent: str) -> dict[str, str]:
        return {
            self.blocks[i]["child_page"]["title"]: i
            for i in self.children.get(parent, [])
            if self.blocks[i]["type"] == "child_page"
        }

    def texts(self, parent: str) -> list[str]:
        out = []
        for i in self.children.get(parent, []):
            block = self.blocks[i]
            rich = block[block["type"]].get("rich_text", [])
            out.append("".join(p.get("text", {}).get("content", "") for p in rich))
        return out


class _Ns:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class Client:  # what NotionWriter sees
    def __init__(self, fake: FakeNotion):
        self.pages = fake.pages
        self.blocks = fake.blocks_api


class FakeFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requests: list[str] = []

    def get(self, url: str, etag: str | None = None) -> Page:
        self.requests.append(url)
        return Page(url=url, html=self.pages[url], etag=None)


def index_html(links: list[tuple[str, str, str | None]]) -> str:
    """links: (h2, url, h3)"""
    parts, last = [], (None, None)
    for h2, url, h3 in links:
        if h2 != last[0]:
            parts.append(f"<h2>{h2}</h2><p>about {h2}</p>")
        if h3 and (h2, h3) != last:
            parts.append(f"<h3>{h3}</h3><p>about {h3}</p>")
        parts.append(f'<ul><li><a href="{url}"><span>{url.rsplit("/", 2)[-2]}</span></a></li></ul>')
        last = (h2, h3)
    body = "<p>Tutorial intro.</p><gfg-carousel>ad</gfg-carousel>" + "".join(parts)
    return f'<html><body><h1>AI Tutorial</h1><div class="text">{body}</div></body></html>'


def article_html(title: str, modified: str, body: str) -> str:
    return (
        f'<html><head><meta property="article:modified_time" content="{modified}"/></head>'
        f'<body><h1>{title}</h1><div class="article--viewer_content"><div class="text">{body}'
        f'<script>ads()</script></div></div><div class="sidebar">Related</div></body></html>'
    )


@pytest.fixture
def env(tmp_path):
    repos = open_repositories(f"sqlite:///{tmp_path / 'test.db'}")
    user = repos.users.get_or_create_local()
    fake = FakeNotion()
    site = {
        INDEX: index_html(
            [
                ("Basics", A1, None),
                ("Machine Learning", A2, "Supervised"),
                ("Machine Learning", A3, "Unsupervised"),
            ]
        ),
        A1: article_html(
            "What is AI", "2026-01-01 00:00:00+00:00", "<p>AI intro.</p><h2>History</h2><p>Old.</p>"
        ),
        A2: article_html(
            "Linear Regression",
            "2026-01-01 00:00:00+00:00",
            "<p>Fit a line.</p><h2>Formula</h2><p><gfg-tex>y = wx + b</gfg-tex></p>",
        ),
        A3: article_html("K-Means", "2026-01-01 00:00:00+00:00", "<p>Clusters.</p>"),
    }
    fetcher = FakeFetcher(site)
    base = tmp_path / "web"
    clock = lambda: datetime(2026, 11, 26, tzinfo=UTC)  # noqa: E731

    def current():
        return repos.web_imports.get_by_url(user.id, gfg.normalize_url(INDEX, INDEX))

    def do_import(**kw):
        imp = register(repos.web_imports, user.id, INDEX, TARGET)
        writer = NotionWriter(Client(fake))
        return ImportRun(repos.web_imports, fetcher, writer, base_dir=base, now=clock, **kw).run(
            imp
        )

    def check():
        return CheckRun(repos.web_imports, fetcher, base_dir=base, now=clock).run(current())

    return _Ns(
        repos=repos, user=user, fake=fake, site=site, fetcher=fetcher, base=base,
        do_import=do_import, check=check, current=current,
    )  # fmt: skip


def _article(env, url):
    return next(a for a in env.repos.web_imports.articles(env.current().id) if a.url == url)


# ---- parsing the real pages ---------------------------------------------------------------


def test_parse_index_fixture():
    entries = gfg.parse_index((FIXTURES / "index.html").read_text(), INDEX)
    assert len(entries) == 102
    assert entries[0].section == ("Basics",)
    assert entries[0].link_text == "Introduction"
    assert ("Problem Solving", "Search Algorithms") in {e.section for e in entries}
    assert len({e.url for e in entries}) == len(entries)
    assert all(e.url.startswith("https://www.geeksforgeeks.org/") for e in entries)


def test_parse_article_fixture():
    article = gfg.parse_article((FIXTURES / "article.html").read_text(), ARTICLE)
    assert article.title == "Q-Learning in Reinforcement Learning"
    assert article.modified == "2026-05-11 16:56:44+00:00"
    blocks = html_to_blocks(article.body, ARTICLE)
    kinds = {b["type"] for b in blocks}
    assert {
        "heading_2",
        "heading_3",
        "paragraph",
        "bulleted_list_item",
        "code",
        "equation",
        "image",
    } <= kinds
    codes = [b["code"] for b in blocks if b["type"] == "code"]
    assert codes[0]["language"] == "python"
    assert codes[0]["rich_text"][0]["text"]["content"].startswith("import numpy as np")
    heads = [h for h, _ in split_sections(blocks_to_markdown(blocks))]
    assert "Key Components" in heads and "Advantages" in heads


# ---- HTML to blocks ---------------------------------------------------------------------


def _blocks(html: str):
    return html_to_blocks(BeautifulSoup(f"<div>{html}</div>", "html.parser").div, ARTICLE)


def test_long_text_is_split_and_styles_kept():
    (block,) = _blocks(f"<p>{'a' * 4500} <b>bold</b> <a href='/x/'>link</a></p>")
    rich = block["paragraph"]["rich_text"]
    assert all(len(p["text"]["content"]) <= MAX_TEXT for p in rich)
    assert any(p.get("annotations", {}).get("bold") for p in rich)
    assert rich[-1]["text"]["link"]["url"] == "https://www.geeksforgeeks.org/x/"


def test_table_lists_and_nesting_limit():
    blocks = _blocks(
        "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td></tr></table>"
        "<ul><li>one<ul><li>two<ul><li>three<ul><li>four</li></ul></li></ul></li></ul></li></ul>"
    )
    table = blocks[0]["table"]
    assert table["has_column_header"] and table["table_width"] == 2
    assert len(table["children"][1]["table_row"]["cells"]) == 2  # padded

    def depth(b, d=0):
        kids = b[b["type"]].get("children", [])
        return max([depth(k, d + 1) for k in kids], default=d)

    assert all(depth(b) <= 2 for b in blocks)
    texts = []

    def walk(b):
        texts.append(b[b["type"]]["rich_text"][0]["text"]["content"])
        for k in b[b["type"]].get("children", []):
            walk(k)

    for b in blocks[1:]:
        walk(b)
    assert texts == ["one", "two", "three", "four"]  # nothing lost, only flattened


def test_batches_respect_limits():
    blocks = [{"type": "paragraph", "paragraph": {"rich_text": []}}] * 250
    assert [len(b) for b in batches(blocks)] == [100, 100, 50]


# ---- markdown and diff ------------------------------------------------------------------


def test_cache_markdown_matches_blocks():
    md = blocks_to_markdown(
        _blocks(
            "<h2>Title</h2><p>Some <b>bold</b> <a href='https://x.org/'>link</a></p>"
            "<figure><img src='https://img/x.png'><figcaption>Pic</figcaption></figure>"
            "<gfg-tabs><gfg-tab>Python</gfg-tab><gfg-panel data-code-lang='python3'>"
            "<pre># not a heading\nx = 1</pre></gfg-panel></gfg-tabs>"
        )
    )
    assert "## Title" in md
    assert "**bold**" in md and "[link](https://x.org/)" in md
    assert "![Pic](https://img/x.png)" in md
    assert "```python\n# not a heading\nx = 1\n```" in md
    assert [h for h, _ in split_sections(md)] == ["Title"]  # the # inside code is no heading


def test_section_diff():
    old = "intro\n\n## A\n\nx\n\n## B\n\ny"
    new = "intro\n\n## A\n\nx2\n\n## C\n\nz"
    diff = compare(old, new)
    assert [h for h, _ in diff.added] == ["C"]
    assert diff.changed == [("A", ["@@ -1 +1 @@", "-x", "+x2"])]
    assert [h for h, _ in diff.removed] == ["B"]
    assert diff.describe() == "新增「C」，修改「A」，删除「B」"


# ---- web import ---------------------------------------------------------------------------


def _layout(env):
    """The --into page as (type, text) pairs."""
    target = env.current().target_page_id
    out = []
    for block_id in env.fake.children[target]:
        block = env.fake.blocks[block_id]
        if block["type"] == "child_page":
            out.append(("page", block["child_page"]["title"]))
        else:
            rich = block[block["type"]].get("rich_text", [])
            out.append((block["type"], "".join(p["text"]["content"] for p in rich)))
    return out


LAYOUT = [
    ("paragraph", "Tutorial intro."),
    ("heading_2", "Basics"),
    ("paragraph", "about Basics"),
    ("page", "What is AI"),
    ("heading_2", "Machine Learning"),
    ("paragraph", "about Machine Learning"),
    ("heading_3", "Supervised"),
    ("paragraph", "about Supervised"),
    ("page", "Linear Regression"),
    ("heading_3", "Unsupervised"),
    ("paragraph", "about Unsupervised"),
    ("page", "K-Means"),
]


def test_import_lays_out_the_index_with_article_pages_in_place(env):
    report = env.do_import()
    assert [r.outcome for r in report.results] == [ImportOutcome.IMPORTED] * 3
    assert report.completed
    assert _layout(env) == LAYOUT
    page = _article(env, A2).notion_page_id
    kinds = [env.fake.blocks[i]["type"] for i in env.fake.children[page]]
    assert kinds == ["paragraph", "paragraph", "heading_2", "equation"]  # source line first
    assert env.fake.texts(page)[0] == f"原文：{A2}"
    assert env.fake.child_pages(page) == {}  # no sub-pages, no notes template
    cached = env.base / "geeksforgeeks-artificial-intelligence" / _article(env, A2).cache_path
    assert cached.read_text().startswith("Fit a line.")
    assert "ads()" not in cached.read_text()


def test_layout_of_the_real_index():
    items = gfg.parse_index_layout((FIXTURES / "index.html").read_text(), INDEX)
    assert sum(1 for i in items if i.article) == 102
    first = [i.content.get_text(" ", strip=True) for i in items[:2]]
    assert first[0].startswith("Artificial Intelligence (AI) refers to")
    assert first[1].startswith("Basics This section introduces the fundamental concepts")
    assert [i.article.link_text for i in items[2:6]] == [
        "Introduction",
        "Types of AI Based on Capabilities",
        "Types of AI Based on Functionalities",
        "AI vs. ML vs. DL",
    ]


def test_import_resumes_with_limit_and_then_refuses(env):
    first = env.do_import(limit=2)
    assert len(first.results) == 2 and first.remaining == 1 and not first.completed
    second = env.do_import(limit=2)
    assert [r.url for r in second.results] == [A3] and second.completed
    with pytest.raises(AlreadyImported):
        env.do_import()
    assert _layout(env) == LAYOUT  # same order, nothing twice


def test_import_stops_at_an_error_and_continues_in_place(env):
    real_append = env.fake._append
    calls = {"n": 0}

    def flaky(block_id, children):
        calls["n"] += 1
        if calls["n"] == 4:  # 1-2: intro and Basics; 3: first 100 blocks of A1; 4: fails
            raise httpx.ConnectError("network down")
        return real_append(block_id, children)

    env.site[A1] = article_html(
        "What is AI", "2026-01-01 00:00:00+00:00", "".join(f"<p>p{i}</p>" for i in range(150))
    )
    env.fake.blocks_api.children.append = flaky
    report = env.do_import()
    assert report.stopped is not None and report.stopped.url == A1
    assert _layout(env) == LAYOUT[:4]  # nothing written after the failed article
    env.fake.blocks_api.children.append = real_append
    assert env.do_import().completed
    assert _layout(env) == LAYOUT
    texts = env.fake.texts(_article(env, A1).notion_page_id)
    assert texts.count("p0") == 1 and len(texts) == 151  # source line + 150 paragraphs


def test_index_changed_during_an_unfinished_import(env):
    env.do_import(limit=1)
    env.site[INDEX] = index_html([("Basics", A1, None), ("Deep Learning", A3, None)])
    with pytest.raises(LayoutChanged):
        env.do_import()


# ---- web check ----------------------------------------------------------------------------


def test_check_needs_a_finished_import(env):
    env.do_import(limit=1)
    with pytest.raises(ImportNotFinished):
        env.check()


def test_check_with_no_changes_writes_no_report_and_touches_no_notion(env):
    env.do_import()
    writes = env.fake.writes
    report = env.check()
    assert [r.outcome for r in report.results] == [CheckOutcome.UNCHANGED] * 3
    assert report.path is None
    assert env.fake.writes == writes


def test_check_reports_changes_once_and_updates_the_cache(env):
    env.do_import()
    writes = env.fake.writes
    env.site[A1] = article_html(
        "What is AI",
        "2026-02-01 00:00:00+00:00",
        "<p>AI intro.</p><h2>History</h2><p>New.</p><h2>Ethics</h2><p>Be fair.</p>",
    )
    report = env.check()
    assert env.fake.writes == writes  # Notion untouched
    (changed,) = report.of(CheckOutcome.CHANGED)
    assert changed.diff.describe() == "新增「Ethics」，修改「History」"
    text = report.path.read_text()
    assert report.path.name == "2026-11-26-check.md"
    assert "-Old." in text and "+New." in text and "+Be fair." in text
    assert "原文更新于 2026-01-01 → 2026-02-01" in text
    assert "https://www.notion.so/" in text
    cache = env.base / "geeksforgeeks-artificial-intelligence" / _article(env, A1).cache_path
    assert "Be fair." in cache.read_text()
    again = env.check()
    assert again.of(CheckOutcome.CHANGED) == [] and again.path is None  # reported once


def test_check_lists_new_and_removed_articles_once(env):
    env.do_import()
    a4 = "https://www.geeksforgeeks.org/deep-learning/cnn/"
    env.site[a4] = article_html("CNN", "2026-03-01 00:00:00+00:00", "<p>Conv.</p>")
    env.site[INDEX] = index_html(
        [("Basics", A1, None), ("Machine Learning", A2, "Supervised"), ("Deep Learning", a4, None)]
    )
    pages = len(env.fake.blocks)
    report = env.check()
    assert [r.url for r in report.of(CheckOutcome.NEW)] == [a4]
    assert [r.url for r in report.of(CheckOutcome.REMOVED)] == [A3]
    assert len(env.fake.blocks) == pages  # the new article is not imported
    text = report.path.read_text()
    assert "## 目录里的新文章" in text and "## 已从目录移除" in text
    assert _article(env, A3).state == WebArticleState.REMOVED
    again = env.check()
    assert again.of(CheckOutcome.NEW) == [] and again.of(CheckOutcome.REMOVED) == []
    env.site[INDEX] = index_html([("Basics", A1, None), ("Machine Learning", A3, "Unsupervised")])
    back = env.check()
    assert [r.url for r in back.of(CheckOutcome.RESTORED)] == [A3]
    assert _article(env, A3).state == WebArticleState.IMPORTED


def test_forget_allows_a_fresh_import(env):
    from learnova.webimport.importer import forget

    env.do_import()
    imp = env.current()
    cache = env.base / imp.slug / "cache"
    assert cache.is_dir()
    forget(env.repos.web_imports, imp, env.base)
    assert env.current() is None and not cache.exists()
    assert env.do_import().completed  # allowed again


def test_register_rules(env):
    imp = register(env.repos.web_imports, env.user.id, INDEX, TARGET)
    with pytest.raises(UnsupportedSite):
        register(env.repos.web_imports, env.user.id, "https://example.com/tutorial/", TARGET)
    with pytest.raises(ConflictError):
        register(env.repos.web_imports, env.user.id, INDEX, "a" * 32)
    other = "https://www.geeksforgeeks.org/machine-learning/machine-learning/"
    with pytest.raises(LearnovaError, match="--into"):
        register(env.repos.web_imports, env.user.id, other, None)
    assert register(env.repos.web_imports, env.user.id, INDEX, None).id == imp.id
    assert imp.slug == "geeksforgeeks-artificial-intelligence"


# ---- fetching -------------------------------------------------------------------------


def test_fetcher_waits_retries_and_uses_etag():
    calls: list[httpx.Request] = []
    answers = iter(
        [
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(200, text="ok", headers={"etag": '"v1"'}),
            httpx.Response(304),
        ]
    )

    def handler(request):
        calls.append(request)
        return next(answers)

    slept: list[float] = []
    now = [0.0]
    fetcher = Fetcher(
        min_interval=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
        clock=lambda: now[0],
    )
    page = fetcher.get("https://www.geeksforgeeks.org/a/")
    assert page.html == "ok" and page.etag == '"v1"'
    again = fetcher.get("https://www.geeksforgeeks.org/a/", etag='"v1"')
    assert again.not_modified
    assert calls[-1].headers["if-none-match"] == '"v1"'
    assert 3 in slept  # Retry-After honoured


def test_cli_check_without_imports_says_how_to_start():
    from typer.testing import CliRunner

    from learnova.cli.main import app

    result = CliRunner().invoke(app, ["web", "check"])
    assert result.exit_code == 0
    assert "learnova web import <url> --into <page>" in result.output
