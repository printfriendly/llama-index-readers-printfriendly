from __future__ import annotations

import json

import httpx
import pytest
import respx

from llama_index.readers.printfriendly import PrintFriendlyError, _client
from llama_index.readers.printfriendly._client import PrintFriendlyClient
from tests.conftest import PAGE, TEST_KEY, generation_error, success_body


def make_client(**kwargs: float) -> PrintFriendlyClient:
    return PrintFriendlyClient(TEST_KEY, **kwargs)  # type: ignore[arg-type]


def test_sends_bearer_key_json_body_and_user_agent(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(200, json=success_body())
    with make_client() as client:
        page = client.convert(PAGE, output="text", include_links=True)
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert request.headers["User-Agent"].startswith(
        "llama-index-readers-printfriendly/"
    )
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.read()) == {
        "url": PAGE,
        "output": "text",
        "include_links": True,
    }
    assert page.content.startswith("# Eclipse")


def test_metadata_has_source_and_page_fields_but_no_empty_values(
    api: respx.MockRouter,
) -> None:
    body = success_body(
        author="", date=None, excerpt="About eclipses.", image="https://x/y.png"
    )
    api.post("/v2/convert").respond(200, json=body)
    with make_client() as client:
        page = client.convert(PAGE)
    assert page.metadata == {
        "source": PAGE,
        "title": "Eclipse",
        "site": "en.wikipedia.org",
        "excerpt": "About eclipses.",
        "image": "https://x/y.png",
        "word_count": 7,
    }
    assert page.usage["plan"] == "free"


def test_word_count_zero_is_kept(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(200, json=success_body(word_count=0))
    with make_client() as client:
        assert client.convert(PAGE).metadata["word_count"] == 0


def test_rate_limit_waits_for_retry_after_then_succeeds(
    api: respx.MockRouter, waits: list[float]
) -> None:
    route = api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "7"},
                json=generation_error("rate_limited", "Slow down", retryable=True),
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        page = client.convert(PAGE)
    assert page.content
    assert route.call_count == 2
    assert waits == [7.0]


def test_rate_limit_uses_body_hint_when_header_missing(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(
                429,
                json=generation_error(
                    "rate_limited", "Slow down", retryable=True, retry_after_seconds=4
                ),
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        client.convert(PAGE)
    assert waits == [4.0]


def test_bare_429_without_a_json_body_is_still_retried(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, text="Too Many Requests"),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        assert client.convert(PAGE).content
    assert waits == [3.0]


def test_rate_limit_asking_for_too_long_a_wait_raises_without_sleeping(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").respond(
        429,
        headers={"Retry-After": "600"},
        json=generation_error("rate_limited", "Slow down", retryable=True),
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "rate_limited"
    assert info.value.status_code == 429
    assert waits == []


def test_retryable_error_is_retried_with_backoff_then_gives_up(
    api: respx.MockRouter, waits: list[float]
) -> None:
    route = api.post("/v2/convert").respond(
        422, json=generation_error("fetch_failed", "Timed out", retryable=True)
    )
    with (
        make_client(max_retries=2) as client,
        pytest.raises(PrintFriendlyError) as info,
    ):
        client.convert(PAGE)
    assert info.value.code == "fetch_failed"
    assert info.value.retryable is True
    assert info.value.url == PAGE
    assert route.call_count == 3  # first try plus two retries
    assert waits == [1.0, 2.0]


def test_retryable_error_recovers(api: respx.MockRouter) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(
                422, json=generation_error("render_timeout", "Slow", retryable=True)
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        assert client.convert(PAGE).content


def test_non_retryable_error_is_raised_at_once(
    api: respx.MockRouter, waits: list[float]
) -> None:
    route = api.post("/v2/convert").respond(
        422, json=generation_error("content_blocked", "Blocked", retryable=False)
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "content_blocked"
    assert route.call_count == 1
    assert waits == []


def test_max_retries_zero_never_retries(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(
        422, json=generation_error("fetch_failed", "Timed out", retryable=True)
    )
    with make_client(max_retries=0) as client, pytest.raises(PrintFriendlyError):
        client.convert(PAGE)
    assert route.call_count == 1


def test_validation_error_shape_is_understood(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        400,
        json={
            "status": "failed",
            "error": {
                "type": "parameters-validation",
                "errors": {"base": ["You must provide one of url, html or markdown"]},
                "message": "You must provide one of url, html or markdown",
                "code": "invalid_parameters",
                "retryable": False,
            },
        },
        headers={"X-Request-Id": "req_123"},
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    error = info.value
    assert error.code == "invalid_parameters"
    assert error.request_id == "req_123"
    assert "You must provide one of" in str(error)
    assert "req_123" in str(error)


def test_quota_error_carries_upgrade_url_and_is_an_account_error(
    api: respx.MockRouter,
) -> None:
    api.post("/v2/convert").respond(
        402,
        json=generation_error(
            "quota_exceeded",
            "Your free plan includes 100 documents a month.",
            retryable=False,
            upgrade_url="https://www.printfriendly.com/api#pricing",
        ),
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.is_account_error
    assert info.value.upgrade_url == "https://www.printfriendly.com/api#pricing"
    assert "Upgrade: https://www.printfriendly.com/api#pricing" in str(info.value)


def test_bad_key_is_an_account_error(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        401,
        json={
            "status": "failed",
            "error": {
                "type": "parameters-validation",
                "errors": {"api_key": ["Invalid"]},
                "message": "The API key is not valid.",
                "code": "invalid_api_key",
                "retryable": False,
            },
        },
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "invalid_api_key"
    assert info.value.is_account_error


def test_page_failure_is_not_an_account_error(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        422, json=generation_error("parse_failed", "No article", retryable=False)
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert not info.value.is_account_error


def test_html_error_page_is_retried_as_gateway_error(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(502, text="<html>Bad gateway</html>"),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        assert client.convert(PAGE).content
    assert waits == [1.0]


def test_non_json_client_error_gets_a_status_code(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(418, text="teapot")
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "http_418"
    assert info.value.retryable is False
    assert "teapot" in info.value.message


def test_connection_failure_is_retried(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=success_body()),
        ]
    )
    with make_client() as client:
        assert client.convert(PAGE).content
    assert waits == [1.0]


def test_read_timeout_is_not_retried_so_a_document_is_not_billed_twice(
    api: respx.MockRouter,
) -> None:
    route = api.post("/v2/convert").mock(side_effect=httpx.ReadTimeout("slow"))
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "timeout"
    assert route.call_count == 1


def test_success_without_content_is_an_invalid_response(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(200, json={"status": "success"})
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "invalid_response"


def test_success_that_is_not_json_is_an_invalid_response(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(200, text="ok")
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.convert(PAGE)
    assert info.value.code == "invalid_response"


def test_usage_reads_the_free_endpoint(api: respx.MockRouter) -> None:
    convert = api.post("/v2/convert").respond(200, json=success_body())
    usage = api.get("/v2/usage").respond(
        200,
        json={
            "status": "success",
            "usage": {
                "plan": "free",
                "used_this_period": 42,
                "included": 100,
                "remaining": 58,
            },
        },
    )
    with make_client() as client:
        result = client.usage()
    assert result.plan == "free"
    assert result.used_this_period == 42
    assert result.included == 100
    assert result.remaining == 58
    assert usage.call_count == 1
    assert convert.call_count == 0


def test_usage_with_a_bad_key_raises(api: respx.MockRouter) -> None:
    api.get("/v2/usage").respond(
        401,
        json={
            "status": "failed",
            "error": {
                "type": "parameters-validation",
                "errors": {},
                "message": "Missing key",
                "code": "api_key_required",
                "retryable": False,
            },
        },
    )
    with make_client() as client, pytest.raises(PrintFriendlyError) as info:
        client.usage()
    assert info.value.code == "api_key_required"


def test_base_url_is_configurable() -> None:
    with respx.mock() as router:
        route = router.post("https://staging.example.test/v2/convert").respond(
            200, json=success_body()
        )
        with PrintFriendlyClient(
            TEST_KEY, base_url="https://staging.example.test/"
        ) as client:
            client.convert(PAGE)
    assert route.called


def test_wait_helper_gives_up_over_the_cap() -> None:
    error = PrintFriendlyError(
        "x", code="rate_limited", retryable=True, retry_after=_client.MAX_RETRY_WAIT + 1
    )
    assert _client._wait_for(error, 0) is None
    assert _client._wait_for(PrintFriendlyError("x", code="a", retry_after=3), 0) == 3
    assert _client._backoff(10) == _client._BACKOFF_CAP


async def test_async_convert_and_retry(
    api: respx.MockRouter, waits: list[float]
) -> None:
    route = api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json=generation_error("rate_limited", "Slow down", retryable=True),
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    async with make_client() as client:
        page = await client.aconvert(PAGE)
    assert page.metadata["title"] == "Eclipse"
    assert route.call_count == 2
    assert waits == [2.0]


async def test_async_error_and_usage(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        422, json=generation_error("parse_failed", "No article", retryable=False)
    )
    api.get("/v2/usage").respond(
        200,
        json={
            "status": "success",
            "usage": {
                "plan": "paid",
                "used_this_period": 1,
                "included": 5000,
                "remaining": 4999,
            },
        },
    )
    async with make_client() as client:
        with pytest.raises(PrintFriendlyError) as info:
            await client.aconvert(PAGE)
        assert info.value.code == "parse_failed"
        assert (await client.ausage()).plan == "paid"


async def test_async_connection_failure_exhausts_retries(
    api: respx.MockRouter, waits: list[float]
) -> None:
    route = api.post("/v2/convert").mock(side_effect=httpx.ConnectError("boom"))
    async with make_client(max_retries=1) as client:
        with pytest.raises(PrintFriendlyError) as info:
            await client.aconvert(PAGE)
    assert info.value.code == "connection_error"
    assert route.call_count == 2
    assert waits == [1.0]


def test_resolve_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _client.resolve_api_key("  abc ") == "abc"
    monkeypatch.setenv("PRINTFRIENDLY_API_KEY", "from-env")
    assert _client.resolve_api_key(None) == "from-env"
    assert _client.resolve_api_key("explicit") == "explicit"
    monkeypatch.delenv("PRINTFRIENDLY_API_KEY")
    with pytest.raises(ValueError, match="printfriendly.com/me/api-key"):
        _client.resolve_api_key(None)
    with pytest.raises(ValueError):
        _client.resolve_api_key("   ")


def test_validate_urls_and_output() -> None:
    assert _client.validate_urls(" https://a.test/x ") == ["https://a.test/x"]
    assert _client.validate_urls(("http://a.test", "https://b.test")) == [
        "http://a.test",
        "https://b.test",
    ]
    for bad in ["example.com", "ftp://a.test/x", "https://", "", "javascript:alert(1)"]:
        with pytest.raises(ValueError):
            _client.validate_urls(bad)
    with pytest.raises(ValueError):
        _client.validate_urls([123])  # type: ignore[list-item]
    assert _client.validate_output("html") == "html"
    with pytest.raises(ValueError, match="markdown, text, html"):
        _client.validate_output("pdf")
