"""Shared fixtures: a mocked PrintFriendly API and a sleep that never sleeps."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx

from llama_index.readers.printfriendly import _client

BASE_URL = "https://api.printfriendly.com"
TEST_KEY = "pf_test_key_not_real"
PAGE = "https://en.wikipedia.org/wiki/Eclipse"


def success_body(
    content: str = "# Eclipse\n\nAn eclipse is an astronomical event.",
    *,
    output: str = "markdown",
    **document: Any,
) -> dict[str, Any]:
    """A 200 body shaped like the real API's."""
    doc: dict[str, Any] = {
        "title": "Eclipse",
        "url": PAGE,
        "site": "en.wikipedia.org",
        "word_count": 7,
    }
    doc.update(document)
    return {
        "status": "success",
        "output": output,
        "content": content,
        "document": doc,
        "usage": {"used_this_period": 3, "included": 100, "plan": "free"},
    }


def generation_error(
    code: str, message: str, *, retryable: bool, **extra: Any
) -> dict[str, Any]:
    """A failure body shaped like the API's ``GenerationError``."""
    return {
        "status": "failed",
        "message": message,
        "error": {"code": code, "message": message, "retryable": retryable, **extra},
    }


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """Mock every httpx call. Unmatched requests fail instead of going out."""
    with respx.mock(
        base_url=BASE_URL, assert_all_called=False, assert_all_mocked=True
    ) as router:
        yield router


@pytest.fixture(autouse=True)
def waits(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> list[float]:
    """Record every retry wait instead of sleeping. Live tests really sleep."""
    recorded: list[float] = []
    if request.node.get_closest_marker("live"):
        return recorded

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def fake_asleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(_client, "_sleep", fake_sleep)
    monkeypatch.setattr(_client, "_asleep", fake_asleep)
    return recorded


@pytest.fixture(autouse=True)
def clean_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure a developer's real key never leaks into a unit test.

    Live tests are the one place the key is wanted, so they keep it.
    """
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.delenv("PRINTFRIENDLY_API_KEY", raising=False)
