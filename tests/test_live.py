"""Opt-in tests against the real PrintFriendly API.

They are skipped unless ``PRINTFRIENDLY_API_KEY`` is set. Each test that reads a
page uses one document from the key's plan (the free plan has 100 a month), so
this file reads exactly two pages and calls the free ``/v2/usage`` endpoint,
which never counts.

    PRINTFRIENDLY_API_KEY=... uv run pytest tests/test_live.py
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from llama_index.readers.printfriendly import PrintFriendlyReader
from llama_index.readers.printfriendly._client import PrintFriendlyClient

API_KEY = os.environ.get("PRINTFRIENDLY_API_KEY")
LIVE_URL = "https://en.wikipedia.org/wiki/Eclipse"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not API_KEY, reason="PRINTFRIENDLY_API_KEY is not set"),
]


def test_usage_is_free_and_the_key_works() -> None:
    assert API_KEY
    with PrintFriendlyClient(API_KEY) as client:
        before = client.usage()
        again = client.usage()
    assert before.plan in ("free", "paid")
    assert before.included >= before.used_this_period
    assert again.used_this_period == before.used_this_period  # reading it is free


def test_reader_reads_a_real_page() -> None:
    docs = PrintFriendlyReader().load_data(urls=[LIVE_URL])
    assert len(docs) == 1
    doc = docs[0]
    assert doc.doc_id == LIVE_URL
    assert doc.metadata["source"] == LIVE_URL
    assert "eclipse" in doc.metadata["title"].lower()
    assert doc.metadata["word_count"] > 500
    assert len(doc.text) > 2000
    assert "eclipse" in doc.text.lower()
    # Shown with `pytest -s`, so a person can eyeball the real metadata shape.
    sys.stdout.write("\nLIVE METADATA " + json.dumps(doc.metadata) + "\n")


async def test_reader_reads_a_real_page_async() -> None:
    docs = await PrintFriendlyReader(output="text").aload_data(urls=LIVE_URL)
    assert len(docs) == 1
    assert docs[0].metadata["source"] == LIVE_URL
    assert "eclipse" in docs[0].text.lower()
