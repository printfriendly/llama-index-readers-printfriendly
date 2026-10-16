from __future__ import annotations

import json
import logging
from collections.abc import Generator

import httpx
import pytest
import respx
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.indices import VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.readers.base import BasePydanticReader, BaseReader
from llama_index.core.schema import Document, MetadataMode

from llama_index.readers.printfriendly import PrintFriendlyError, PrintFriendlyReader
from tests.conftest import PAGE, TEST_KEY, generation_error, success_body

OTHER = "https://example.org/post/1"
THIRD = "https://example.net/post/2"


def reader(**kwargs: object) -> PrintFriendlyReader:
    kwargs.setdefault("api_key", TEST_KEY)
    return PrintFriendlyReader(**kwargs)  # type: ignore[arg-type]


def echo_url(request: httpx.Request) -> httpx.Response:
    url = json.loads(request.content)["url"]
    return httpx.Response(200, json=success_body(f"# Page\n\ntext for {url}", url=url))


def test_class() -> None:
    names_of_base_classes = [b.__name__ for b in PrintFriendlyReader.__mro__]
    assert BaseReader.__name__ in names_of_base_classes
    assert BasePydanticReader.__name__ in names_of_base_classes
    assert PrintFriendlyReader.class_name() == "PrintFriendlyReader"


def test_load_data_returns_one_document_per_url(api: respx.MockRouter) -> None:
    api.post("/v2/convert").mock(side_effect=echo_url)
    docs = reader().load_data(urls=[PAGE, OTHER])
    assert [type(d) for d in docs] == [Document, Document]
    assert docs[0].text == f"# Page\n\ntext for {PAGE}"
    assert docs[0].metadata["source"] == PAGE
    assert docs[1].metadata["source"] == OTHER


def test_document_id_is_the_url(api: respx.MockRouter) -> None:
    api.post("/v2/convert").mock(side_effect=echo_url)
    (doc,) = reader().load_data(urls=[PAGE])
    assert doc.doc_id == PAGE


def test_metadata_fields(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        200,
        json=success_body(
            author="Ada",
            date="2026-01-02",
            excerpt="An excerpt.",
            image="https://img.test/a.png",
        ),
    )
    (doc,) = reader().load_data(urls=PAGE)
    assert doc.metadata == {
        "source": PAGE,
        "title": "Eclipse",
        "site": "en.wikipedia.org",
        "author": "Ada",
        "date": "2026-01-02",
        "excerpt": "An excerpt.",
        "image": "https://img.test/a.png",
        "word_count": 7,
    }


def test_noisy_metadata_is_kept_out_of_embeddings_but_not_the_llm_text(
    api: respx.MockRouter,
) -> None:
    api.post("/v2/convert").respond(
        200, json=success_body(excerpt="An excerpt.", image="https://img.test/a.png")
    )
    (doc,) = reader().load_data(urls=PAGE)
    embed_text = doc.get_content(metadata_mode=MetadataMode.EMBED)
    llm_text = doc.get_content(metadata_mode=MetadataMode.LLM)
    assert "title: Eclipse" in embed_text
    for noisy in ("source:", "image:", "word_count:", "excerpt:"):
        assert noisy not in embed_text
    assert f"source: {PAGE}" in llm_text
    assert "image:" not in llm_text
    assert "word_count:" not in llm_text


def test_options_are_sent_to_the_api(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(200, json=success_body(output="html"))
    reader(output="html", include_links=True).load_data(urls=PAGE)
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"url": PAGE, "output": "html", "include_links": True}


def test_defaults_to_markdown_without_links(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(200, json=success_body())
    reader().load_data(urls=[PAGE])
    sent = json.loads(route.calls.last.request.content)
    assert sent["output"] == "markdown"
    assert sent["include_links"] is False


def test_api_key_comes_from_the_environment(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRINTFRIENDLY_API_KEY", "env-key")
    route = api.post("/v2/convert").respond(200, json=success_body())
    PrintFriendlyReader().load_data(urls=PAGE)
    assert route.calls.last.request.headers["Authorization"] == "Bearer env-key"


def test_explicit_key_beats_the_environment(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRINTFRIENDLY_API_KEY", "env-key")
    route = api.post("/v2/convert").respond(200, json=success_body())
    PrintFriendlyReader(api_key="arg-key").load_data(urls=PAGE)
    assert route.calls.last.request.headers["Authorization"] == "Bearer arg-key"


def test_missing_key_fails_at_construction() -> None:
    with pytest.raises(ValueError, match="PRINTFRIENDLY_API_KEY"):
        PrintFriendlyReader()


@pytest.mark.parametrize(
    "kwargs",
    [{"output": "pdf"}, {"timeout": 0}, {"max_retries": -1}],
)
def test_bad_options_fail_at_construction(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        reader(**kwargs)


def test_bad_url_fails_before_any_request(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(200, json=success_body())
    with pytest.raises(ValueError, match="not-a-url"):
        reader().load_data(urls=[PAGE, "not-a-url"])
    assert route.call_count == 0


def test_the_key_is_not_in_repr_or_serialized_data() -> None:
    instance = reader()
    assert TEST_KEY not in repr(instance)
    assert TEST_KEY not in str(instance)
    assert TEST_KEY not in instance.to_json()
    assert "api_key" not in instance.to_dict()


def test_round_trip_through_a_dict_takes_the_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = reader(output="text", include_links=True, max_retries=5)
    monkeypatch.setenv("PRINTFRIENDLY_API_KEY", "env-key")
    copy = PrintFriendlyReader.from_dict(original.to_dict())
    assert (copy.output, copy.include_links, copy.max_retries) == ("text", True, 5)
    assert copy.api_key == "env-key"


def test_lazy_load_data_reads_one_page_at_a_time(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").mock(side_effect=echo_url)
    iterator = reader().lazy_load_data(urls=[PAGE, OTHER, THIRD])
    assert isinstance(iterator, Generator)
    assert route.call_count == 0
    next(iterator)
    assert route.call_count == 1
    next(iterator)
    assert route.call_count == 2
    iterator.close()
    assert route.call_count == 2


def test_failure_raises_by_default(api: respx.MockRouter) -> None:
    api.post("/v2/convert").respond(
        422, json=generation_error("parse_failed", "No article here", retryable=False)
    )
    with pytest.raises(PrintFriendlyError, match="No article here") as info:
        reader().load_data(urls=PAGE)
    assert info.value.url == PAGE


def fail_on_other(request: httpx.Request) -> httpx.Response:
    url = json.loads(request.content)["url"]
    if url == OTHER:
        return httpx.Response(
            422, json=generation_error("fetch_failed", "Gone", retryable=False)
        )
    return httpx.Response(200, json=success_body(url=url))


def test_continue_on_failure_skips_the_bad_page_and_logs(
    api: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    api.post("/v2/convert").mock(side_effect=fail_on_other)
    with caplog.at_level(logging.WARNING, logger="llama_index.readers.printfriendly"):
        docs = reader(continue_on_failure=True).load_data(urls=[PAGE, OTHER, THIRD])
    assert [d.metadata["source"] for d in docs] == [PAGE, THIRD]
    assert any(OTHER in record.getMessage() for record in caplog.records)


def test_continue_on_failure_still_raises_for_a_bad_key(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(
        401,
        json={
            "status": "failed",
            "error": {
                "type": "parameters-validation",
                "errors": {},
                "message": "Bad key",
                "code": "invalid_api_key",
                "retryable": False,
            },
        },
    )
    with pytest.raises(PrintFriendlyError) as info:
        reader(continue_on_failure=True).load_data(urls=[PAGE, OTHER])
    assert info.value.code == "invalid_api_key"
    assert route.call_count == 1  # it did not carry on to the second page


def test_continue_on_failure_still_raises_when_quota_is_used_up(
    api: respx.MockRouter,
) -> None:
    api.post("/v2/convert").respond(
        402, json=generation_error("quota_exceeded", "Used up", retryable=False)
    )
    with pytest.raises(PrintFriendlyError) as info:
        reader(continue_on_failure=True).load_data(urls=[PAGE, OTHER])
    assert info.value.code == "quota_exceeded"


def test_rate_limit_is_waited_out_between_pages(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(200, json=success_body()),
            httpx.Response(
                429,
                headers={"Retry-After": "5"},
                json=generation_error("rate_limited", "Slow down", retryable=True),
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    assert len(reader().load_data(urls=[PAGE, OTHER])) == 2
    assert waits == [5.0]


def test_max_retries_is_passed_through(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(
        422, json=generation_error("fetch_failed", "Timed out", retryable=True)
    )
    with pytest.raises(PrintFriendlyError):
        reader(max_retries=0).load_data(urls=PAGE)
    assert route.call_count == 1


def test_documents_go_into_an_index_and_come_back_with_their_metadata(
    api: respx.MockRouter,
) -> None:
    """The point of a reader: its output feeds the rest of LlamaIndex."""
    api.post("/v2/convert").mock(side_effect=echo_url)
    docs = reader().load_data(urls=[PAGE, OTHER])
    index = VectorStoreIndex.from_documents(
        docs,
        embed_model=MockEmbedding(embed_dim=8),
        transformations=[SentenceSplitter(tokenizer=str.split)],
    )
    nodes = index.as_retriever(similarity_top_k=2).retrieve("eclipse")
    assert {n.node.ref_doc_id for n in nodes} == {PAGE, OTHER}
    assert all(n.node.metadata["title"] == "Eclipse" for n in nodes)


async def test_alazy_load_data(api: respx.MockRouter) -> None:
    api.post("/v2/convert").mock(side_effect=echo_url)
    docs = list(await reader().alazy_load_data(urls=[PAGE, OTHER]))
    assert [d.metadata["source"] for d in docs] == [PAGE, OTHER]
    assert all(isinstance(d, Document) for d in docs)


async def test_aload_data(api: respx.MockRouter) -> None:
    api.post("/v2/convert").mock(side_effect=echo_url)
    docs = await reader().aload_data(urls=PAGE)
    assert len(docs) == 1
    assert docs[0].doc_id == PAGE


async def test_async_continue_on_failure_and_account_errors(
    api: respx.MockRouter,
) -> None:
    api.post("/v2/convert").mock(side_effect=fail_on_other)
    docs = await reader(continue_on_failure=True).aload_data(urls=[PAGE, OTHER, THIRD])
    assert [d.metadata["source"] for d in docs] == [PAGE, THIRD]

    with pytest.raises(PrintFriendlyError):
        await reader().aload_data(urls=[PAGE, OTHER])


async def test_async_retry_on_rate_limit(
    api: respx.MockRouter, waits: list[float]
) -> None:
    api.post("/v2/convert").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json=generation_error("rate_limited", "Slow down", retryable=True),
            ),
            httpx.Response(200, json=success_body()),
        ]
    )
    assert len(await reader().aload_data(urls=PAGE)) == 1
    assert waits == [3.0]


async def test_async_options_are_sent_to_the_api(api: respx.MockRouter) -> None:
    route = api.post("/v2/convert").respond(200, json=success_body(output="text"))
    await reader(output="text", include_links=True).aload_data(urls=PAGE)
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"url": PAGE, "output": "text", "include_links": True}
