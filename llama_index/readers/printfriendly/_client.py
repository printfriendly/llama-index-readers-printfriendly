"""Small HTTP client for the PrintFriendly API.

This module is private. Use `PrintFriendlyReader`.

It owns three things the public class relies on:

* the request and response shapes of ``POST /v2/convert`` and ``GET /v2/usage``,
* a bounded retry loop (rate limits, errors marked ``retryable``, dropped
  connections),
* one exception type, `PrintFriendlyError`, that carries the API's error code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import metadata
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

API_KEY_ENV_VAR = "PRINTFRIENDLY_API_KEY"
API_KEY_URL = "https://www.printfriendly.com/me/api-key"
DEFAULT_BASE_URL = "https://api.printfriendly.com"

#: The service answers a synchronous call within 27 seconds. The default leaves
#: room for the network on top of that.
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2

#: If the API asks us to wait longer than this, we give up instead of blocking
#: the caller for minutes. The error is raised with the API's own code.
MAX_RETRY_WAIT = 60.0
_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 10.0

TextOutput = Literal["markdown", "text", "html"]
TEXT_OUTPUTS: tuple[str, ...] = ("markdown", "text", "html")

#: Errors about the key or the plan, not about one page. Every later request in
#: the same run would fail the same way, so callers should never skip past them.
ACCOUNT_ERROR_CODES = frozenset(
    {
        "api_key_required",
        "invalid_api_key",
        "subscription_inactive",
        "limit_exceeded",
        "quota_exceeded",
    }
)

_RETRYABLE_STATUS = frozenset({502, 503, 504})

#: Page fields copied from the API's ``document`` object into Document metadata.
METADATA_FIELDS: tuple[str, ...] = (
    "title",
    "site",
    "author",
    "date",
    "excerpt",
    "image",
    "word_count",
)


def _package_version() -> str:
    try:
        return metadata.version("llama-index-readers-printfriendly")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from source
        return "0+unknown"


USER_AGENT = f"llama-index-readers-printfriendly/{_package_version()}"


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _asleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class PrintFriendlyError(RuntimeError):
    """The PrintFriendly API could not complete a request.

    Attributes:
        code: The API's machine-readable error code, for example
            ``fetch_failed`` or ``quota_exceeded``. Client-side problems use
            ``connection_error``, ``timeout``, ``transport_error`` and
            ``invalid_response``.
        retryable: Whether sending the same request again can work.
        status_code: The HTTP status, when there was a response.
        request_id: The ``X-Request-Id`` to quote to PrintFriendly support.
        upgrade_url: Where to upgrade the plan. Only set with ``quota_exceeded``.
        url: The page being read when the error happened, if known.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
        upgrade_url: str | None = None,
        retry_after: float | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id
        self.upgrade_url = upgrade_url
        self.retry_after = retry_after
        self.url = url

    @property
    def is_account_error(self) -> bool:
        """True for key and plan errors that no other page can get past."""
        return self.code in ACCOUNT_ERROR_CODES

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.url:
            parts.append(f"(page: {self.url})")
        if self.upgrade_url:
            parts.append(f"Upgrade: {self.upgrade_url}")
        if self.request_id:
            parts.append(f"(request id: {self.request_id})")
        return " ".join(parts)


@dataclass(frozen=True)
class Page:
    """One page as the API returned it."""

    url: str
    content: str
    metadata: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class Usage:
    """What ``GET /v2/usage`` reports. Reading it is free."""

    plan: str
    used_this_period: int
    included: int
    remaining: int


def resolve_api_key(api_key: str | None) -> str:
    """Return the key to use: the argument, else ``PRINTFRIENDLY_API_KEY``."""
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR, "")
    key = key.strip()
    if not key:
        raise ValueError(
            "A PrintFriendly API key is required. Pass api_key=... or set the "
            f"{API_KEY_ENV_VAR} environment variable. Get a free key (100 "
            f"documents a month) at {API_KEY_URL}"
        )
    return key


def validate_urls(urls: str | Sequence[str]) -> list[str]:
    """Normalise ``urls`` to a list and check each one is an http(s) address."""
    items = [urls] if isinstance(urls, str) else list(urls)
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"urls must be strings, got {type(item).__name__}.")
        parts = urlsplit(item.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(
                f"Not a valid http(s) address: {item!r}. Include the scheme, "
                "for example https://example.com/article."
            )
    return [item.strip() for item in items]


def validate_output(output: str) -> TextOutput:
    """Check ``output`` is one of the text formats this package supports."""
    if output not in TEXT_OUTPUTS:
        raise ValueError(
            f"output must be one of {', '.join(TEXT_OUTPUTS)}, got {output!r}."
        )
    return output  # type: ignore[return-value]


def _retry_after_seconds(
    response: httpx.Response, error: dict[str, Any]
) -> float | None:
    header = response.headers.get("Retry-After")
    candidates: list[Any] = [header, error.get("retry_after_seconds")]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            seconds = float(candidate)
        except (TypeError, ValueError):
            continue
        if seconds >= 0:
            return seconds
    return None


def error_from_response(
    response: httpx.Response, url: str | None
) -> PrintFriendlyError:
    """Turn a non-2xx response into a `PrintFriendlyError`."""
    status = response.status_code
    request_id = response.headers.get("X-Request-Id")
    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = None

    error: dict[str, Any] = {}
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]

    code = error.get("code")
    if not isinstance(code, str) or not code:
        code = f"http_{status}"
    message = error.get("message")
    if not isinstance(message, str) or not message:
        top_level = payload.get("message") if isinstance(payload, dict) else None
        message = top_level if isinstance(top_level, str) and top_level else None
    if message is None:
        message = (response.text or response.reason_phrase or "Request failed")[:300]

    retryable = bool(error.get("retryable", False))
    if status == 429 or status in _RETRYABLE_STATUS:
        retryable = True
    upgrade_url = error.get("upgrade_url")

    return PrintFriendlyError(
        message,
        code=code,
        retryable=retryable,
        status_code=status,
        request_id=request_id,
        upgrade_url=upgrade_url if isinstance(upgrade_url, str) else None,
        retry_after=_retry_after_seconds(response, error),
        url=url,
    )


def _backoff(attempt: int) -> float:
    return float(min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP))


def _wait_for(error: PrintFriendlyError, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None when we should give up."""
    wait = error.retry_after if error.retry_after is not None else _backoff(attempt)
    return wait if wait <= MAX_RETRY_WAIT else None


def _transport_error(exc: httpx.HTTPError, url: str | None) -> PrintFriendlyError:
    """Map an httpx failure to an error, marking only safe cases retryable.

    A connection that never opened cannot have used a document, so it is safe
    to try again. A read timeout might have happened after the server finished,
    so it is not retried: a second call could be billed twice.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return PrintFriendlyError(
            f"Could not connect to the PrintFriendly API: {exc}",
            code="connection_error",
            retryable=True,
            url=url,
        )
    if isinstance(exc, httpx.TimeoutException):
        return PrintFriendlyError(
            f"The PrintFriendly API did not answer in time: {exc}",
            code="timeout",
            retryable=False,
            url=url,
        )
    return PrintFriendlyError(
        f"Request to the PrintFriendly API failed: {exc}",
        code="transport_error",
        retryable=False,
        url=url,
    )


def _page_from_response(response: httpx.Response, url: str) -> Page:
    request_id = response.headers.get("X-Request-Id")
    try:
        data = response.json()
    except ValueError as exc:
        raise PrintFriendlyError(
            "The PrintFriendly API returned a body that is not JSON.",
            code="invalid_response",
            status_code=response.status_code,
            request_id=request_id,
            url=url,
        ) from exc
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        raise PrintFriendlyError(
            "The PrintFriendly API response has no 'content' text.",
            code="invalid_response",
            status_code=response.status_code,
            request_id=request_id,
            url=url,
        )
    document = data.get("document")
    document = document if isinstance(document, dict) else {}
    meta: dict[str, Any] = {"source": url}
    for key in METADATA_FIELDS:
        value = document.get(key)
        if value is None or value == "":
            continue
        meta[key] = value
    usage = data.get("usage")
    return Page(
        url=url,
        content=content,
        metadata=meta,
        usage=usage if isinstance(usage, dict) else {},
        request_id=request_id,
    )


def _usage_from_response(response: httpx.Response) -> Usage:
    try:
        usage = response.json()["usage"]
        return Usage(
            plan=str(usage["plan"]),
            used_this_period=int(usage["used_this_period"]),
            included=int(usage["included"]),
            remaining=int(usage["remaining"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise PrintFriendlyError(
            "The PrintFriendly API returned an unexpected usage body.",
            code="invalid_response",
            status_code=response.status_code,
            request_id=response.headers.get("X-Request-Id"),
        ) from exc


class PrintFriendlyClient:
    """Synchronous and asynchronous access to the PrintFriendly API.

    Use it as a context manager (``with`` or ``async with``) so the underlying
    connection pool is closed.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Create a client. ``api_key`` must already be resolved and non-empty."""
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._http: httpx.Client | None = None
        self._ahttp: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _sync_http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._base_url,
                headers=self._headers(),
                timeout=self._timeout,
            )
        return self._http

    def _async_http(self) -> httpx.AsyncClient:
        if self._ahttp is None:
            self._ahttp = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers(),
                timeout=self._timeout,
            )
        return self._ahttp

    def close(self) -> None:
        """Close the synchronous connection pool."""
        if self._http is not None:
            self._http.close()
            self._http = None

    async def aclose(self) -> None:
        """Close the asynchronous connection pool."""
        if self._ahttp is not None:
            await self._ahttp.aclose()
            self._ahttp = None

    def __enter__(self) -> PrintFriendlyClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    async def __aenter__(self) -> PrintFriendlyClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    @staticmethod
    def _convert_body(url: str, output: str, include_links: bool) -> dict[str, Any]:
        return {"url": url, "output": output, "include_links": include_links}

    def convert(
        self, url: str, *, output: str = "markdown", include_links: bool = False
    ) -> Page:
        """Read one page. Retries rate limits and retryable errors."""
        body = self._convert_body(url, output, include_links)
        attempt = 0
        while True:
            try:
                response = self._sync_http().post("/v2/convert", json=body)
            except httpx.HTTPError as exc:
                error = _transport_error(exc, url)
            else:
                if response.is_success:
                    return _page_from_response(response, url)
                error = error_from_response(response, url)
            wait = _wait_for(error, attempt)
            if not error.retryable or attempt >= self._max_retries or wait is None:
                raise error
            logger.info(
                "PrintFriendly %s for %s; retrying in %.1fs (retry %d of %d)",
                error.code, url, wait, attempt + 1, self._max_retries,
            )  # fmt: skip
            _sleep(wait)
            attempt += 1

    async def aconvert(
        self, url: str, *, output: str = "markdown", include_links: bool = False
    ) -> Page:
        """Async version of `convert`."""
        body = self._convert_body(url, output, include_links)
        attempt = 0
        while True:
            try:
                response = await self._async_http().post("/v2/convert", json=body)
            except httpx.HTTPError as exc:
                error = _transport_error(exc, url)
            else:
                if response.is_success:
                    return _page_from_response(response, url)
                error = error_from_response(response, url)
            wait = _wait_for(error, attempt)
            if not error.retryable or attempt >= self._max_retries or wait is None:
                raise error
            logger.info(
                "PrintFriendly %s for %s; retrying in %.1fs (retry %d of %d)",
                error.code, url, wait, attempt + 1, self._max_retries,
            )  # fmt: skip
            await _asleep(wait)
            attempt += 1

    def usage(self) -> Usage:
        """Check the key and read the plan's usage. Free: never uses a document."""
        attempt = 0
        while True:
            try:
                response = self._sync_http().get("/v2/usage")
            except httpx.HTTPError as exc:
                error = _transport_error(exc, None)
            else:
                if response.is_success:
                    return _usage_from_response(response)
                error = error_from_response(response, None)
            wait = _wait_for(error, attempt)
            if not error.retryable or attempt >= self._max_retries or wait is None:
                raise error
            _sleep(wait)
            attempt += 1

    async def ausage(self) -> Usage:
        """Async version of `usage`."""
        attempt = 0
        while True:
            try:
                response = await self._async_http().get("/v2/usage")
            except httpx.HTTPError as exc:
                error = _transport_error(exc, None)
            else:
                if response.is_success:
                    return _usage_from_response(response)
                error = error_from_response(response, None)
            wait = _wait_for(error, attempt)
            if not error.retryable or attempt >= self._max_retries or wait is None:
                raise error
            await _asleep(wait)
            attempt += 1
