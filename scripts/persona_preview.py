"""Preview how each persona mode looks in practice."""

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def render_topic_tabs() -> Table:
    """Horizontal tabs showing different topics."""
    active_label = "backend @feat/api"
    inactive_label = "happy-gadgets"
    grid = Table.grid(padding=(0, 2))
    grid.pad_edge = False
    grid.add_column()
    grid.add_column()
    grid.add_row(Text(active_label, style="cyan"), Text(inactive_label, style="dim"))
    grid.add_row(Text("─" * len(active_label), style="cyan"), Text(""))
    return grid


def render_topic_conversation() -> Text:
    """Conversation inside the selected topic."""
    return Text.assemble(
        ("[you] ", "bold cyan"),
        "/topic backend @feat/api\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("[you] ", "bold cyan"),
        "review the error handling\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 10s\n", "dim"),
        ("[you] ", "bold cyan"),
        "also add cache invalidation\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 6s", "dim"),
    )


def render_assistant_preview() -> Text:
    """Assistant: messages auto-continue in one conversation."""
    return Text.assemble(
        ("[you] ", "bold cyan"),
        "explain what this repo does\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("[you] ", "bold cyan"),
        "now add tests\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 12s\n", "dim"),
        ("[you] ", "bold cyan"),
        ("/new", "bold green"),
        ("  ← start fresh\n", "yellow"),
        ("[you] ", "bold cyan"),
        "review the error handling\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 6s\n", "dim"),
        ("[you] ", "bold cyan"),
        "implement caching\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 4s", "dim"),
    )


def render_handoff_preview() -> Text:
    """Handoff: every message starts fresh, reply to continue."""
    return Text.assemble(
        ("[you] ", "bold cyan"),
        "explain what this repo does\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("      codex resume ", "dim"),
        ("abc123 ", "cyan"),
        ("← reply\n", "yellow"),
        ("[you] ", "bold cyan"),
        "add a health check endpoint\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 3s\n", "dim"),
        ("      codex resume ", "dim"),
        ("def456\n", "green"),
        ("[you] ", "bold cyan"),
        ("(reply) ", "bold green"),
        "now add tests\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("      codex resume ", "dim"),
        ("abc123", "cyan"),
    )


PANEL_WIDTH = 42


def main():
    console = Console()

    workspace_layout = Group(
        render_topic_tabs(),
        Text(""),
        render_topic_conversation(),
    )
    workspace_panel = Panel(
        workspace_layout,
        title=Text("workspace", style="bold"),
        subtitle="project/branch workspaces",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
        width=PANEL_WIDTH,
    )

    assistant_panel = Panel(
        render_assistant_preview(),
        title=Text("assistant", style="bold"),
        subtitle="ongoing chat (recommended)",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1),
        width=PANEL_WIDTH,
    )

    handoff_panel = Panel(
        render_handoff_preview(),
        title=Text("handoff", style="bold"),
        subtitle="reply to continue · terminal",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1),
        width=PANEL_WIDTH,
    )

    console.print()
    console.print(Columns([workspace_panel, assistant_panel, handoff_panel]))
    console.print()


if __name__ == "__main__":
    main()
