# Dev setup

Set up Takopi for local development and run the checks.

## Clone and run

```bash
git clone https://github.com/banteg/takopi
cd takopi

# Run directly with uv (installs deps automatically)
uv run takopi --help
```

## Install locally (optional)

```bash
uv tool install .
takopi --help
```

## Run checks

```bash
uv run pytest
uv run ruff check src tests
uv run ty check .

# Or all at once
just check
```

