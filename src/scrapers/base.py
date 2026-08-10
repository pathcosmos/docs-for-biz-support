"""Shared HTTP client for Korean gov/startup site scrapers. Sites are often
slow and return 5xx under load — the client retries with exponential backoff
and exposes a uniform exception so adapters can decide whether to surface a
'⚠️ scrape failed' notice in the daily mail."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Self

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
    the whole archive.

    ``kind`` and ``transient`` preserve the machine-readable failure class so
    callers do not have to infer retry policy by searching the human-readable
    message. Parser-raised errors can continue to omit both fields.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.transient = transient


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

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get_response(
        self,
        url: str,
        params: dict | None = None,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> httpx.Response:
        """GET with retry and return the fully buffered response."""
        last_exc: Exception | None = None
        req_timeout = self.timeout if timeout is None else timeout
        attempt_count = self.retries if attempts is None else attempts
        if attempt_count < 1:
            raise ValueError("attempts must be at least 1")
        for attempt in range(attempt_count):
            try:
                r = self._client.get(url, params=params, timeout=req_timeout)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} for {url}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt < attempt_count - 1:
                    sleep_for = BACKOFF_BASE ** (attempt + 1)
                    time.sleep(sleep_for)
        # Embed the real underlying error (ConnectTimeout / ReadTimeout / 5xx /
        # etc.) in the message — without this, production logs only ever show
        # "failed after 3 attempts" with no way to tell a silent network-level
        # hang apart from a fast server-side rejection.
        error_kind = type(last_exc).__name__ if last_exc else None
        transient = isinstance(last_exc, (httpx.TransportError, httpx.TimeoutException))
        if isinstance(last_exc, httpx.HTTPStatusError):
            transient = last_exc.response.status_code >= 500
        raise ScrapeError(
            f"failed after {attempt_count} attempts: {url} "
            f"(last error: {error_kind}: {last_exc})",
            kind=error_kind,
            transient=transient,
        ) from last_exc

    def get(
        self,
        url: str,
        params: dict | None = None,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> str:
        """GET text with retry. `timeout` overrides the client default."""
        return self._get_response(
            url, params=params, timeout=timeout, attempts=attempts,
        ).text

    def get_bytes(
        self,
        url: str,
        params: dict | None = None,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> bytes:
        """GET binary content with the same retry policy as :meth:`get`."""
        return self._get_response(
            url, params=params, timeout=timeout, attempts=attempts,
        ).content
