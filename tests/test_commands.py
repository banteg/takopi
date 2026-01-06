from pathlib import Path

from takopi.commands import load_commands


def test_load_commands_from_claude(tmp_path: Path) -> None:
    command_dir = tmp_path / ".claude" / "commands"
    command_dir.mkdir(parents=True)
    command_file = command_dir / "commit-all.md"
    command_file.write_text(
        "Commit all current changes.\nIf needed, split into logical commits.\n",
        encoding="utf-8",
    )

    catalog = load_commands(
        cwd=tmp_path,
        include_parents=False,
        include_home=False,
    )

    skill = catalog.by_name["commit-all"]
    assert skill.name == "commit-all"
    assert "Commit all current changes." in skill.description
    assert skill.prompt.startswith("Commit all current changes.")
    assert catalog.by_command["commit_all"] == skill


def test_load_commands_from_opencode(tmp_path: Path) -> None:
    command_dir = tmp_path / ".opencode" / "command" / "git"
    command_dir.mkdir(parents=True)
    command_file = command_dir / "commit-all.md"
    command_file.write_text(
        "---\ndescription: Commit all changes.\n---\nCommit everything.\n",
        encoding="utf-8",
    )

    catalog = load_commands(
        cwd=tmp_path,
        include_parents=False,
        include_home=False,
    )

    skill = catalog.by_name["git/commit-all"]
    assert skill.name == "git/commit-all"
    assert skill.description == "Commit all changes."
    assert skill.prompt == "Commit everything."
