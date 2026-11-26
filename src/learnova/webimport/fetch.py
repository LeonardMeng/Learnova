"""Polite HTTP fetching for import-web: one request at a time with a minimum gap between them,
conditional requests (ETag) so unchanged pages cost almost nothing, and a few retries on 429 and
server errors."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from learnova.errors import LearnovaError

USER_AGENT = "Mozilla/5.0 (compatible; Learnova/0.1; personal study notes)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(LearnovaError):
    status = 502


@dataclass(frozen=True)
class Page:
    url: str
    html: str | None  # None when the server answered 304 Not Modified
    etag: str | None

    @property
    def not_modified(self) -> bool:
        return self.html is None


class Fetcher:
    def __init__(
        self,
        min_interval: float = 2.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        retries: int = 3,
    ):
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
        )
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._retries = retries
        self._last: float | None = None

    def get(self, url: str, etag: str | None = None) -> Page:
        headers = {"If-None-Match": etag} if etag else {}
        for attempt in range(self._retries + 1):
            self._wait_turn()
            try:
                response = self._client.get(url, headers=headers)
            except httpx.TransportError as e:
                if attempt == self._retries:
                    raise FetchError(f"Could not fetch {url}: {e}") from e
                self._sleep(2**attempt)
                continue
            if response.status_code == 304:
                return Page(url=url, html=None, etag=etag)
            if response.status_code in RETRY_STATUSES and attempt < self._retries:
                self._sleep(_retry_after(response) or 2**attempt * 5)
                continue
            if response.status_code != 200:
                raise FetchError(f"{url} answered HTTP {response.status_code}.")
            return Page(url=url, html=response.text, etag=response.headers.get("etag"))
        raise FetchError(f"Could not fetch {url}.")  # pragma: no cover (loop always returns)

    def _wait_turn(self) -> None:
        now = self._clock()
        if self._last is not None:
            gap = self._min_interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now += gap
        self._last = now


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
