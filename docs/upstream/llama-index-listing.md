# LlamaIndex listing (ready to paste)

Do not send any of this until `llama-index-readers-printfriendly` is published on PyPI and the GitHub repository is public. Nothing has been sent.

## What LlamaIndex currently asks for

Sources checked 2026-10-16:

- https://github.com/run-llama/llama_index/blob/main/CONTRIBUTING.md
- https://github.com/run-llama/llama_index/pull/21139 ("restrict new packages", merged 2026-03-25)
- https://github.com/run-llama/llama_index/blob/main/docs/src/content/docs/framework/community/integrations.md
- https://developers.llamaindex.ai/python/framework/module_guides/loading/connector/

What they say:

1. "we are no longer accepting new integration packages" in the `llama_index` repository. New integrations are maintained in their own repository and published to PyPI independently. A PR that adds a new `pyproject.toml` is closed automatically. So this package is a standalone repo, not a folder under `llama-index-integrations/readers/`.
2. Integration tests should mock remote systems. CI fails under 50% coverage (this package is above 90%).
3. AI-assisted contributions must say where AI was used and how the result was verified. Say so in any PR (the body below does).
4. The package follows the naming convention `llama-index-readers-<source>` and the import path `llama_index.readers.<source>`. The `pyproject.toml` carries a `[tool.llamahub]` block like the monorepo readers, in case the listing tooling reads it.

## What I could not find

LlamaIndex documents no process for listing an externally hosted reader. llamahub.ai now only says it has moved to developers.llamaindex.ai. The integrations docs page (`community/integrations.md`) names the monorepo `readers` directory as where readers live and has no section for external ones. So **whether and how an external reader gets listed is uncertain**, and a docs PR below may be declined or ignored. It is a small docs-only change (no `pyproject.toml`), so it should not trip the auto-close rule.

## Option A: a docs PR (suggested)

Target repo: `run-llama/llama_index`. File: `docs/src/content/docs/framework/community/integrations.md`. The patch in `llama-index-docs-integrations.patch` applies cleanly to `main` as of 2026-10-16.

```sh
git clone https://github.com/<your-fork>/llama_index.git && cd llama_index
git checkout -b docs/external-reader-printfriendly
git apply /path/to/llama-index-docs-integrations.patch
git commit -am "docs: list the PrintFriendly reader under external data loaders"
```

### Title

```
docs: list the externally maintained PrintFriendly reader
```

### Body

```
## What

Adds a short note and one entry under "Data Loaders" in the community integrations page, for a reader that lives in its own repository and is published to PyPI on its own, as CONTRIBUTING.md now asks of new integrations.

- Package: `llama-index-readers-printfriendly` (import `llama_index.readers.printfriendly`)
- Repo: https://github.com/printfriendly/llama-index-readers-printfriendly
- What it does: `PrintFriendlyReader(BasePydanticReader)` turns web page URLs into `Document`s of clean Markdown through the PrintFriendly API (https://www.printfriendly.com/api). Free key, 100 documents a month.

This is a docs-only change: no new package and no `pyproject.toml` in this repo.

## Why here

CONTRIBUTING.md says new integrations should be maintained in their own repositories, but the integrations page only describes readers that live in this repo, so there is nowhere for users to find the externally maintained ones. If you would rather list external readers somewhere else, or not at all, tell me where and I will move or drop this.

## How the package was tested

- 59 unit tests with the HTTP layer mocked (respx), network access disabled, coverage 94%. They include loading the documents into a `VectorStoreIndex` with `MockEmbedding`.
- Opt-in live tests against the production API: two real pages loaded, sync and async.
- Checked against `llama-index-core` 0.13.0 and 0.14.25, and Python 3.12, 3.13 and 3.14.

## AI use

The package and this change were written with AI assistance (Claude). A human reviewed them, and the behavior was verified by the test suite and live runs described above.
```

## Option B: ask first

If you would rather ask before opening a PR, post this in the LlamaIndex Discord (https://discord.gg/dGcwcsnxhU), in the channel for contributions or integrations:

```
Hi, I maintain llama-index-readers-printfriendly (PrintFriendlyReader, a BasePydanticReader that loads web pages as clean Markdown through the PrintFriendly API). Per CONTRIBUTING.md it lives in its own repo and is published to PyPI: https://github.com/printfriendly/llama-index-readers-printfriendly

Is there a place where externally maintained readers are listed (llamahub.ai moved to developers.llamaindex.ai, and the integrations page only covers readers in the monorepo)? If a docs PR to community/integrations.md is the right way, I have one ready. Thanks.
```
