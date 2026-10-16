.PHONY: all format lint test live_test build

all: format lint test

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy llama_index tests
	uv run codespell .

# Unit tests: no network. pytest-socket fails any test that tries to open one.
# CI in the LlamaIndex repo fails below 50% coverage; this package is above 90%.
test:
	uv run pytest --disable-socket --allow-unix-socket --cov=llama_index --cov-report=term-missing tests

# Real API. Needs PRINTFRIENDLY_API_KEY. Reads two pages: two documents of the plan.
live_test:
	uv run pytest tests/test_live.py

build:
	uv build
