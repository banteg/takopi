check:
    uv run ruff format --check
    uv run ruff check .
    uv run ty check .
    uv run pytest

bundle:
    #!/usr/bin/env bash
    set -euo pipefail
    bundle="takopi.git.bundle"
    git bundle create "$bundle" --all
    open -R "$bundle"
