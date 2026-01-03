from takopi.backends_helpers import install_issue
from takopi.backends import SetupIssue


def test_install_issue_with_install_cmd() -> None:
    result = install_issue("codex", "npm install -g @openai/codex")

    assert result == SetupIssue(
        "install codex",
        ("   [dim]$[/] npm install -g @openai/codex",),
    )


def test_install_issue_without_install_cmd() -> None:
    result = install_issue("codex", None)

    assert result == SetupIssue(
        "install codex",
        ("   [dim]See engine setup docs for install instructions.[/]",),
    )
