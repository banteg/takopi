#!/usr/bin/env bash
set -euo pipefail

pip install uv
uv sync --frozen --no-install-project --group docs
uv run --no-sync python scripts/docs_prebuild.py
uv run --no-sync zensical build --clean
