#!/usr/bin/env bash
set -euo pipefail

pip install uv
uv python install 3.14
uv sync --frozen --only-group docs
uv run --no-sync python scripts/docs_prebuild.py
uv run --no-sync zensical build --clean
