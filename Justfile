check:
    uv run ruff format --check src tests
    uv run ruff check src tests
    uv run ty check src tests
    uv run pytest

docs-serve:
    uv run --no-sync python scripts/docs_prebuild.py
    uv run --group docs zensical serve

docs-build:
    uv run --no-sync python scripts/docs_prebuild.py
    uv run --group docs zensical build

bundle:
    #!/usr/bin/env bash
    set -euo pipefail
    bundle="takopi.git.bundle"
    git bundle create "$bundle" --all
    open -R "$bundle"
