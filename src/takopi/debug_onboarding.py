from __future__ import annotations

import typer

from .config import ConfigError
from .engines import SetupIssue, get_backend, list_backend_ids
from .onboarding import SetupResult, check_setup, render_setup_guide


def run(
    engine: str = typer.Option(
        "codex",
        "--engine",
        help=f"Engine backend id ({', '.join(list_backend_ids())}).",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Render onboarding panel even if setup looks OK.",
    ),
) -> None:
    try:
        backend = get_backend(engine)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    setup = check_setup(backend)
    if setup.ok and force:
        setup = SetupResult(
            issues=[
                SetupIssue(
                    "Setup looks good",
                    ("Everything appears configured correctly.",),
                )
            ],
            config_path=setup.config_path,
        )
    render_setup_guide(setup)


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
