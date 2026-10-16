# LlamaIndex Readers Integration: PrintFriendly

[LlamaIndex](https://www.llamaindex.ai/) reader for the [PrintFriendly API](https://www.printfriendly.com/api). Give it web page addresses and it returns each article as a `Document` of clean Markdown, without ads, menus, cookie banners or other clutter.

## Install

```bash
pip install -U llama-index-readers-printfriendly
```

## Get an API key

Get a free key at [printfriendly.com/me/api-key](https://www.printfriendly.com/me/api-key). The free plan includes 100 documents a month and needs no card. Paid plans include more.

```bash
export PRINTFRIENDLY_API_KEY="your key"
```

Or pass it in code with `api_key="..."`.

To check that a key works without using a document, call the free usage endpoint. It is never counted:

```bash
curl -H "Authorization: Bearer $PRINTFRIENDLY_API_KEY" https://api.printfriendly.com/v2/usage
```

## Usage

```python
from llama_index.readers.printfriendly import PrintFriendlyReader

reader = PrintFriendlyReader()  # reads PRINTFRIENDLY_API_KEY
documents = reader.load_data(
    urls=[
        "https://en.wikipedia.org/wiki/Eclipse",
        "https://en.wikipedia.org/wiki/Solar_eclipse",
    ]
)

print(documents[0].text[:200])
print(documents[0].metadata)
# {'source': 'https://en.wikipedia.org/wiki/Eclipse',
#  'title': 'Eclipse - Wikipedia',
#  'site': 'en.wikipedia.org',
#  'author': 'Contributors to Wikimedia projects',
#  'date': '2001-11-02',
#  'excerpt': 'Totality during the solar eclipse of August 11, 1999, ...',
#  'image': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0d/Umbra01.svg/330px-Umbra01.svg.png?...',
#  'word_count': 5097}
```

Load lazily, one page at a time:

```python
for document in reader.lazy_load_data(urls=urls):
    print(document.metadata["title"])
```

Or asynchronously (uses `httpx.AsyncClient`):

```python
documents = await reader.aload_data(urls=urls)
```

Pages are read one after another, in the order you gave them.

### Build an index

```python
from llama_index.core import VectorStoreIndex

index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()
print(query_engine.query("What is a total solar eclipse?"))
```

Each `Document` has the page URL as its `doc_id`. Loading the same page again and passing it through an `IngestionPipeline` with a docstore updates the stored copy instead of adding a second one.

The noisy metadata (`source`, `image`, `word_count`, `excerpt`) is excluded from the text that gets embedded, so embeddings are about the page and not about its URL. `title`, `site`, `author` and `date` stay in. The LLM sees `source`, `title`, `site`, `author`, `date` and `excerpt`.

### Options

| Argument | Default | What it does |
| --- | --- | --- |
| `api_key` | `$PRINTFRIENDLY_API_KEY` | Your API key. It is never included in `to_dict()` or the repr. |
| `output` | `"markdown"` | `"markdown"`, `"text"` or `"html"`. |
| `include_links` | `False` | Keep link and image addresses in Markdown and text. Off by default: links keep their words and lose their addresses. |
| `timeout` | `60` | Seconds to wait for each response. The API answers within 27 seconds. |
| `max_retries` | `2` | Retries after a rate limit, a dropped connection or an error the API marks `retryable`. `0` turns retries off. |
| `continue_on_failure` | `False` | Skip a page that cannot be read, with a logged warning, instead of raising. |
| `base_url` | `https://api.printfriendly.com` | Only change this for testing. |

`load_data`, `lazy_load_data`, `aload_data` and `alazy_load_data` take `urls`: one address or a list. Each must be an `http` or `https` address.

### Metadata

| Key | Meaning |
| --- | --- |
| `source` | The URL you passed in. |
| `title` | The page title. |
| `site` | The host name of the page. |
| `author` | The author, when the page names one. |
| `date` | The publish date as ISO 8601, when known. |
| `excerpt` | A short summary of the page. |
| `image` | The main image of the article. |
| `word_count` | Words in the extracted article. |

A field the page does not have is left out, not set to `None`.

## Errors, retries and limits

Every failure raises `PrintFriendlyError`:

```python
from llama_index.readers.printfriendly import PrintFriendlyError

try:
    documents = reader.load_data(urls=urls)
except PrintFriendlyError as error:
    print(error.code)  # for example "fetch_failed" or "quota_exceeded"
    print(error.retryable)  # whether trying again can work
    print(error.request_id)  # quote this to PrintFriendly support
```

- **Rate limits.** Free keys allow 10 requests a minute. On a `429` the reader waits for the time in `Retry-After` and tries again, up to `max_retries` times. If the API asks for more than 60 seconds, the reader raises instead of blocking.
- **Retryable errors.** Errors the API marks `retryable` (a page that timed out, for example) are retried with a short backoff, up to `max_retries` times. A dropped connection is retried too. A read timeout is not, because the document may already have been counted.
- **Everything else raises at once**, with the API's error code and message: `invalid_parameters`, `fetch_failed`, `parse_failed`, `content_blocked`, `render_failed` and so on.
- **`continue_on_failure=True`** skips pages that fail one by one. Key and plan errors (`api_key_required`, `invalid_api_key`, `subscription_inactive`, `limit_exceeded`, `quota_exceeded`) still raise, because every later page would fail the same way.
- **Billing.** Only a successful read counts as a document on your plan. Failed reads are free. The plan limit comes back as `quota_exceeded` with an `upgrade_url`.

See the [API reference](https://www.printfriendly.com/openapi.json) for every code.

## Development

The project uses [uv](https://docs.astral.sh/uv/).

```bash
make test        # unit tests, no network
make lint        # ruff, mypy, codespell
make live_test   # the real API; needs PRINTFRIENDLY_API_KEY and reads two pages
```

The tests mock the HTTP layer with [respx](https://lundberg.github.io/respx/), as LlamaIndex asks of integrations, and run with network access switched off. Coverage is above 90%. `make live_test` uses two documents of your plan and is skipped without the key.

## Links

- [PrintFriendly API](https://www.printfriendly.com/api)
- [API reference (OpenAPI)](https://www.printfriendly.com/openapi.json)
- [Support](https://www.printfriendly.com/support)

## License

MIT. See [LICENSE](LICENSE).
