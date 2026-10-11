#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv sync --frozen --extra dev --extra documents
uv run --frozen ruff check src tests scripts
uv run --frozen pytest -q
uv run --frozen python scripts/smoke.py
uv build
