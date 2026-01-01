from __future__ import annotations

import typer

from .config import ConfigError
from .engines import SetupIssue, get_backend, get_install_issue, list_backend_ids
from .onboarding import SetupResult, check_setup, config_issue, render_setup_guide


def _dedupe_issues(issues: list[SetupIssue]) -> list[SetupIssue]:
    seen: set[SetupIssue] = set()
    deduped: list[SetupIssue] = []
    for issue in issues:
        if issue in seen:
            continue
        seen.add(issue)
        deduped.append(issue)
    return deduped


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
    if force:
        forced_issues = [
            get_install_issue(backend.id),
            config_issue(setup.config_path),
        ]
        setup = SetupResult(
            issues=_dedupe_issues([*setup.issues, *forced_issues]),
            config_path=setup.config_path,
        )
    render_setup_guide(setup)


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
