"""Everything specific to GeeksforGeeks: which links in a tutorial index are its articles (and
under which headings), and where an article's title, last-modified time and body are. Supporting
another site means writing another module with the same two functions."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from learnova.errors import LearnovaError

HOST = "www.geeksforgeeks.org"
BODY_SELECTORS = (".article--viewer_content .text", ".text", "article")
# Never part of the article itself.
JUNK_TAGS = ("script", "style", "noscript", "iframe", "ins", "gfg-carousel", "button", "form")


class ParseError(LearnovaError):
    status = 502


@dataclass(frozen=True)
class IndexEntry:
    url: str
    link_text: str
    section: tuple[str, ...]  # (h2,) or (h2, h3)


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    modified: str | None
    body: Tag


def supports(url: str) -> bool:
    return urlsplit(url).hostname in (HOST, "geeksforgeeks.org")


def normalize_url(href: str, base: str) -> str:
    """Absolute, https, no query or fragment, with a trailing slash."""
    parts = urlsplit(urljoin(base, href))
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit(("https", HOST, path, "", ""))


@dataclass(frozen=True)
class LayoutItem:
    """One step of the index page, in reading order: either some content (headings,
    descriptions, pictures; `content` is a <div> holding copies of the elements) or one article
    from a list of links."""

    section: tuple[str, ...]
    content: Tag | None = None
    article: IndexEntry | None = None


def parse_index_layout(html: str, index_url: str) -> list[LayoutItem]:
    """The index page as it reads: headings and descriptions, with each list of article links
    replaced by its articles. A URL listed twice keeps its first place."""
    soup = BeautifulSoup(html, "html.parser")
    body = _body(soup, index_url)
    for junk in body.find_all(JUNK_TAGS):
        junk.decompose()
    own = normalize_url(index_url, index_url)
    seen: set[str] = set()
    items: list[LayoutItem] = []
    section: tuple[str, ...] = ()

    def add_content(el: Tag) -> None:
        if items and items[-1].content is not None and items[-1].section == section:
            items[-1].content.append(copy.copy(el))
        else:
            box = soup.new_tag("div")
            box.append(copy.copy(el))
            items.append(LayoutItem(section=section, content=box))

    def walk(parent: Tag) -> None:
        nonlocal section
        for el in parent.children:
            if not isinstance(el, Tag):
                continue
            if el.name == "h2":
                section = (_text(el),)
                add_content(el)
            elif el.name == "h3":
                section = (section[0], _text(el)) if section else (_text(el),)
                add_content(el)
            elif el.name in ("ul", "ol") and _links(el):
                for a in _links(el):
                    url = normalize_url(str(a["href"]), index_url)
                    if url == own or url in seen:
                        continue
                    seen.add(url)
                    entry = IndexEntry(url=url, link_text=_text(a), section=section)
                    items.append(LayoutItem(section=section, article=entry))
            elif el.find(["h2", "h3"]) is not None or any(
                _links(u) for u in el.find_all(["ul", "ol"])
            ):
                walk(el)  # a wrapper around headings or link lists
            elif el.get_text(strip=True) or el.find("img") is not None:
                add_content(el)

    walk(body)
    if not any(item.article for item in items):
        raise ParseError(f"No article links found in {index_url}. Has the page layout changed?")
    return items


def parse_index(html: str, index_url: str) -> list[IndexEntry]:
    """The articles listed in the index, in order, each with the h2 (and h3) it sits under."""
    return [i.article for i in parse_index_layout(html, index_url) if i.article is not None]


def _links(list_el: Tag) -> list[Tag]:
    """Links to articles on this site in a list's items."""
    out = []
    for a in list_el.find_all("a", href=True):
        if a.find_parent("li") is None:
            continue
        if urlsplit(str(a["href"])).hostname not in (None, HOST, "geeksforgeeks.org"):
            continue
        out.append(a)
    return out


def parse_article(html: str, url: str) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"property": "article:modified_time"})
    modified = str(meta["content"]) if isinstance(meta, Tag) and meta.get("content") else None
    h1 = soup.find("h1")
    title = _text(h1) if isinstance(h1, Tag) else ""
    if not title and soup.title:
        title = soup.title.get_text().split(" - GeeksforGeeks")[0].strip()
    body = _body(soup, url)
    for junk in body.find_all(JUNK_TAGS):
        junk.decompose()
    return Article(url=url, title=title or url, modified=modified, body=body)


def _body(soup: BeautifulSoup, url: str) -> Tag:
    for selector in BODY_SELECTORS:
        found = soup.select_one(selector)
        if found is not None:
            return found
    raise ParseError(f"Could not find the article body in {url}. Has the page layout changed?")


def _text(el: Tag) -> str:
    return " ".join(el.get_text(" ", strip=True).split())
