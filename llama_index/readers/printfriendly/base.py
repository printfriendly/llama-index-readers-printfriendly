"""PrintFriendly web page reader."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from llama_index.core.bridge.pydantic import Field, field_validator
from llama_index.core.readers.base import BasePydanticReader
from llama_index.core.schema import Document

from llama_index.readers.printfriendly._client import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    Page,
    PrintFriendlyClient,
    PrintFriendlyError,
    TextOutput,
    resolve_api_key,
    validate_urls,
)

logger = logging.getLogger(__name__)

#: Metadata that adds noise to embeddings: a URL, an image address, a count.
EXCLUDED_EMBED_METADATA_KEYS = ["source", "image", "word_count", "excerpt"]
#: Metadata that is not useful in the text an LLM reads.
EXCLUDED_LLM_METADATA_KEYS = ["image", "word_count"]


class PrintFriendlyReader(BasePydanticReader):
    """Read web pages as clean, ad-free text with the PrintFriendly API.

    PrintFriendly fetches each page, keeps the article and drops ads,
    navigation, cookie banners and other clutter. Each URL becomes one
    `Document` whose id is the URL, so loading the same page again updates it in
    a docstore instead of adding a copy.

    Get a free API key (100 documents a month) at
    https://www.printfriendly.com/me/api-key.

    Args:
        api_key: PrintFriendly API key. Defaults to the ``PRINTFRIENDLY_API_KEY``
            environment variable. It is never included in ``to_dict()``.
        output: ``"markdown"`` (default), ``"text"`` or ``"html"``.
        include_links: Keep link and image addresses in markdown and text
            output. Off by default: links keep their words and lose their
            addresses.
        timeout: Seconds to wait for each response. The API answers within 27
            seconds, so the default is 60.
        max_retries: How many times to retry a request after a rate limit, a
            connection failure or an error the API marks ``retryable``. Set 0 to
            never retry.
        continue_on_failure: If True, log a warning and skip a page that cannot
            be read instead of raising. Key and plan errors (``invalid_api_key``,
            ``quota_exceeded`` and the like) always raise, because every later
            page would fail the same way.
        base_url: API base URL. Change only for testing.

    Example:
        ```python
        from llama_index.readers.printfriendly import PrintFriendlyReader

        reader = PrintFriendlyReader()  # reads PRINTFRIENDLY_API_KEY
        documents = reader.load_data(urls=["https://en.wikipedia.org/wiki/Eclipse"])
        ```

    Pages are read one at a time, in the order given. A free key allows 10
    requests a minute; on a 429 the reader waits for ``Retry-After`` and tries
    again, up to ``max_retries`` times. Only a successful read counts as a
    document on your plan.

    Metadata:
        ``source`` is the URL you passed in. ``title``, ``site``, ``author``,
        ``date``, ``excerpt``, ``image`` and ``word_count`` come from the page.
        Fields the page does not have are left out.
    """

    is_remote: bool = True

    api_key: str = Field(
        default="",
        repr=False,
        exclude=True,
        validate_default=True,
        description="PrintFriendly API key. Defaults to $PRINTFRIENDLY_API_KEY.",
    )
    output: TextOutput = Field(
        default="markdown", description="markdown, text or html."
    )
    include_links: bool = Field(
        default=False,
        description="Keep link and image addresses in markdown and text output.",
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT, gt=0, description="Seconds to wait for each response."
    )
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        ge=0,
        description="Retries after a rate limit, a connection failure or a "
        "retryable error.",
    )
    continue_on_failure: bool = Field(
        default=False,
        description="Skip pages that cannot be read instead of raising.",
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL, description="API base URL. Change only for testing."
    )

    @field_validator("api_key", mode="before")
    @classmethod
    def _resolve_api_key(cls, value: Any) -> str:
        return resolve_api_key(value if isinstance(value, str) and value else None)

    @classmethod
    def class_name(cls) -> str:
        """Name used to identify this class in serialized data."""
        return "PrintFriendlyReader"

    def _client(self) -> PrintFriendlyClient:
        return PrintFriendlyClient(
            self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    def _should_skip(self, error: PrintFriendlyError, url: str) -> bool:
        if self.continue_on_failure and not error.is_account_error:
            logger.warning("Skipping %s: %s", url, error)
            return True
        return False

    @staticmethod
    def _to_document(page: Page) -> Document:
        return Document(
            text=page.content,
            doc_id=page.url,
            metadata=dict(page.metadata),
            excluded_embed_metadata_keys=list(EXCLUDED_EMBED_METADATA_KEYS),
            excluded_llm_metadata_keys=list(EXCLUDED_LLM_METADATA_KEYS),
        )

    def lazy_load_data(self, urls: str | Sequence[str]) -> Iterator[Document]:  # type: ignore[override]
        """Read the pages one at a time, yielding a `Document` for each.

        Args:
            urls: One or more http(s) page addresses.

        Raises:
            ValueError: If a URL is not an http(s) address.
            PrintFriendlyError: If a page cannot be read and
                ``continue_on_failure`` is False, or on any key or plan error.
        """
        checked = validate_urls(urls)
        with self._client() as client:
            for url in checked:
                try:
                    page = client.convert(
                        url, output=self.output, include_links=self.include_links
                    )
                except PrintFriendlyError as error:
                    if self._should_skip(error, url):
                        continue
                    raise
                yield self._to_document(page)

    def load_data(self, urls: str | Sequence[str]) -> list[Document]:  # type: ignore[override]
        """Read the pages and return one `Document` per URL.

        Args:
            urls: One or more http(s) page addresses.

        Returns:
            The documents, in the order of ``urls``. A page skipped under
            ``continue_on_failure`` is left out.
        """
        return list(self.lazy_load_data(urls))

    async def alazy_load_data(  # type: ignore[override]
        self, urls: str | Sequence[str]
    ) -> Iterable[Document]:
        """Async version of `load_data`, using ``httpx.AsyncClient``."""
        checked = validate_urls(urls)
        documents: list[Document] = []
        async with self._client() as client:
            for url in checked:
                try:
                    page = await client.aconvert(
                        url, output=self.output, include_links=self.include_links
                    )
                except PrintFriendlyError as error:
                    if self._should_skip(error, url):
                        continue
                    raise
                documents.append(self._to_document(page))
        return documents

    async def aload_data(self, urls: str | Sequence[str]) -> list[Document]:  # type: ignore[override]
        """Async version of `load_data`."""
        return list(await self.alazy_load_data(urls))
