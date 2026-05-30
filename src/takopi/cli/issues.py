from __future__ import annotations

from functools import partial
from pathlib import Path

import anyio
import typer

from ..config import ConfigError, ProjectConfig
from ..github import GitHubError, fetch_issues, issue_branch_name, parse_github_remote
from ..logging import get_logger, setup_logging
from ..settings import load_settings

logger = get_logger(__name__)


def _load_project(
    project: str | None,
) -> tuple[str, Path, int | None, ProjectConfig]:
    """Load settings and resolve a project. Returns (project_key, project_path, chat_id)."""
    settings, config_path = load_settings()
    from ..engines import list_backend_ids
    from ..runtime_loader import resolve_plugins_allowlist

    engine_ids = list_backend_ids(allowlist=resolve_plugins_allowlist(settings))
    projects_config = settings.to_projects_config(
        config_path=config_path, engine_ids=engine_ids
    )

    if project is not None:
        project_key = project.lower()
    else:
        # try to match cwd against configured project paths
        cwd = Path.cwd().resolve()
        project_key = None
        for key, proj in projects_config.projects.items():
            if cwd == proj.path.resolve() or cwd.is_relative_to(proj.path.resolve()):
                project_key = key
                break
        if project_key is None:
            project_key = projects_config.default_project
        if project_key is None:
            raise ConfigError(
                "no project specified and no default_project configured. "
                "run `takopi init` first or pass --project."
            )

    proj = projects_config.projects.get(project_key)
    if proj is None:
        available = ", ".join(sorted(projects_config.projects))
        raise ConfigError(
            f"unknown project {project_key!r}. available: {available}"
        )

    tg = settings.transports.telegram
    chat_id = proj.chat_id
    if chat_id is None and tg is not None:
        chat_id = tg.chat_id

    return project_key, proj.path, chat_id, proj


async def _run_issues_sync(
    project: str | None,
    labels: list[str] | None,
    limit: int,
    dry_run: bool,
) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()

    try:
        project_key, project_path, chat_id, project_cfg = _load_project(project)
    except ConfigError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    try:
        owner, repo = parse_github_remote(project_path)
    except GitHubError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"fetching issues from [bold]{owner}/{repo}[/bold]...")

    try:
        issues = await fetch_issues(owner, repo, labels=labels)
    except GitHubError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not issues:
        console.print("no open issues found.")
        return

    table = Table(title=f"issues for {owner}/{repo} ({len(issues)} open)")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("title")
    table.add_column("labels", style="dim")
    table.add_column("branch", style="green")

    for issue in issues:
        labels_str = ", ".join(issue.labels) if issue.labels else ""
        branch = issue_branch_name(issue.number)
        table.add_row(str(issue.number), issue.title, labels_str, f"@{branch}")

    console.print(table)

    if dry_run:
        console.print("[yellow]dry run — no topics created.[/yellow]")
        return

    if chat_id is None:
        console.print(
            "[red]error:[/red] no chat_id configured for this project or transport. "
            "set projects.<alias>.chat_id or transports.telegram.chat_id."
        )
        raise typer.Exit(1)

    from ..settings import load_settings as reload_settings
    from ..telegram.client import TelegramClient
    from ..telegram.issue_topics import create_topics_from_issues
    from ..telegram.topic_state import TopicStateStore, resolve_state_path

    settings, config_path = reload_settings()
    tg = settings.transports.telegram
    if tg is None:
        console.print("[red]error:[/red] telegram transport not configured.")
        raise typer.Exit(1)

    if not tg.topics.enabled:
        console.print("[red]error:[/red] topics are not enabled. set topics.enabled = true.")
        raise typer.Exit(1)

    bot_client = TelegramClient(tg.bot_token)
    store = TopicStateStore(resolve_state_path(config_path))

    try:
        results = await create_topics_from_issues(
            issues=issues,
            project=project_key,
            chat_id=chat_id,
            bot=bot_client._client,
            store=store,
            project_config=project_cfg,
            limit=limit,
        )
    finally:
        await bot_client.close()

    created = [r for r in results if not r.already_existed]
    skipped = [r for r in results if r.already_existed]

    if created:
        console.print(f"[green]created {len(created)} topic(s):[/green]")
        for r in created:
            console.print(f"  #{r.issue.number} {r.issue.title}")
    if skipped:
        console.print(f"[dim]skipped {len(skipped)} existing topic(s)[/dim]")
    if not created and not skipped:
        console.print("[yellow]no topics created.[/yellow]")


_PROJECT_OPTION = typer.Option(None, "--project", "-p", help="Project alias. Defaults to default_project.")
_LABEL_OPTION = typer.Option(None, "--label", "-l", help="Filter issues by label (can be repeated).")
_LIMIT_OPTION = typer.Option(30, "--limit", "-n", help="Maximum number of issues to fetch.")
_DRY_RUN_OPTION = typer.Option(False, "--dry-run", help="Preview issues without creating topics.")


def issues_sync(
    project: str | None = _PROJECT_OPTION,
    label: list[str] | None = _LABEL_OPTION,
    limit: int = _LIMIT_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Fetch GitHub issues and create Telegram topics for each."""
    setup_logging(debug=False, cache_logger_on_first_use=False)
    anyio.run(partial(_run_issues_sync, project, label, limit, dry_run))


def create_issues_app() -> typer.Typer:
    app = typer.Typer(help="Manage GitHub issue topics.")
    app.command(name="sync")(issues_sync)
    return app
