from takopi.backends_helpers import install_issue


def test_install_issue_with_command() -> None:
    issue = install_issue("codex", "npm install -g @openai/codex")
    assert issue.title == "install codex"
    assert "npm install" in issue.lines[0]


def test_install_issue_without_command() -> None:
    issue = install_issue("myengine", None)
    assert issue.title == "install myengine"
    assert "setup docs" in issue.lines[0].lower()
