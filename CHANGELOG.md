# Changelog

## 0.1.0

First release.

- `PrintFriendlyReader`: a LlamaIndex reader. One `Document` per URL, as Markdown, text or HTML, with the URL as its `doc_id`. `load_data`, `lazy_load_data`, `aload_data` and `alazy_load_data`.
- Waits out `429` responses (honoring `Retry-After`) and retries errors the API marks `retryable`, a bounded number of times.
- `continue_on_failure` skips a page that cannot be read. Key and plan errors always raise.
