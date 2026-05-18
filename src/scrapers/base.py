"""Shared HTTP client for Korean gov/startup site scrapers. Sites are often
slow and return 5xx under load — the client retries with exponential backoff
and exposes a uniform exception so adapters can decide whether to surface a
'⚠️ scrape failed' notice in the daily mail."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
BACKOFF_BASE = 1.5      # 1.5s, 2.25s, 3.4s …
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    """Raised when a source can't be reached / parsed after retries. Caller
    decides whether to surface the failure in the daily mail footer or fail
    the whole archive."""


@dataclass
class HttpClient:
    """Thin httpx wrapper with retry + sensible defaults. One instance per
    scrape run is fine; the underlying client uses connection pooling."""

    user_agent: str = DEFAULT_UA
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=self.timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, url: str, params: dict | None = None) -> str:
        """GET with retry. Returns response text on success; raises ScrapeError
        after `self.retries` failed attempts."""
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self._client.get(url, params=params)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} for {url}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r.text
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt < self.retries - 1:
                    sleep_for = BACKOFF_BASE ** (attempt + 1)
                    time.sleep(sleep_for)
        raise ScrapeError(f"failed after {self.retries} attempts: {url}") from last_exc
