"""Tests for the commands module."""

from pathlib import Path

from takopi.commands import (
    Command,
    CommandCatalog,
    build_command_prompt,
    load_commands_from_dirs,
    normalize_command,
    parse_command_dirs,
    _parse_command_file,
)
from takopi.telegram.bridge import _trim_command_description


class TestNormalizeCommand:
    def test_simple_name(self) -> None:
        assert normalize_command("review") == "review"

    def test_with_leading_slash(self) -> None:
        assert normalize_command("/review") == "review"

    def test_uppercase(self) -> None:
        assert normalize_command("Review") == "review"

    def test_with_spaces(self) -> None:
        assert normalize_command("code review") == "code_review"

    def test_with_special_chars(self) -> None:
        assert normalize_command("code-review!") == "code_review"

    def test_multiple_underscores(self) -> None:
        assert normalize_command("code__review") == "code_review"

    def test_leading_trailing_underscores(self) -> None:
        assert normalize_command("_review_") == "review"

    def test_empty(self) -> None:
        assert normalize_command("") == ""

    def test_only_special_chars(self) -> None:
        assert normalize_command("---") == ""


class TestBuildCommandPrompt:
    def test_prompt_only(self) -> None:
        command = Command(
            name="review",
            description="Review code",
            prompt="Please review this code for best practices.",
            location=Path("/test"),
            source="test",
        )
        result = build_command_prompt(command, "")
        assert result == "Please review this code for best practices."

    def test_args_only(self) -> None:
        command = Command(
            name="review",
            description="Review code",
            prompt="",
            location=Path("/test"),
            source="test",
        )
        result = build_command_prompt(command, "def foo(): pass")
        assert result == "def foo(): pass"

    def test_prompt_and_args(self) -> None:
        command = Command(
            name="review",
            description="Review code",
            prompt="Please review this code.",
            location=Path("/test"),
            source="test",
        )
        result = build_command_prompt(command, "def foo(): pass")
        assert result == "Please review this code.\n\ndef foo(): pass"

    def test_both_empty(self) -> None:
        command = Command(
            name="review",
            description="Review code",
            prompt="",
            location=Path("/test"),
            source="test",
        )
        result = build_command_prompt(command, "")
        assert result == ""


class TestCommandCatalog:
    def test_empty(self) -> None:
        catalog = CommandCatalog.empty()
        assert catalog.commands == ()
        assert catalog.by_name == {}
        assert catalog.by_command == {}

    def test_from_commands(self) -> None:
        commands = [
            Command(
                name="review",
                description="Review code",
                prompt="Review this.",
                location=Path("/test/review.md"),
                source="test",
            ),
            Command(
                name="explain",
                description="Explain code",
                prompt="Explain this.",
                location=Path("/test/explain.md"),
                source="test",
            ),
        ]
        catalog = CommandCatalog.from_commands(commands)

        assert len(catalog.commands) == 2
        assert "review" in catalog.by_name
        assert "explain" in catalog.by_name
        assert "review" in catalog.by_command
        assert "explain" in catalog.by_command

    def test_deduplication(self) -> None:
        commands = [
            Command(
                name="review",
                description="First",
                prompt="First prompt.",
                location=Path("/test/review1.md"),
                source="test1",
            ),
            Command(
                name="Review",  # Same name, different case
                description="Second",
                prompt="Second prompt.",
                location=Path("/test/review2.md"),
                source="test2",
            ),
        ]
        catalog = CommandCatalog.from_commands(commands)

        # Should keep the second one (overwrites)
        assert len(catalog.commands) == 1
        assert catalog.by_name["review"].description == "Second"

    def test_empty_name_ignored(self) -> None:
        commands = [
            Command(
                name="",
                description="Empty",
                prompt="Empty.",
                location=Path("/test/empty.md"),
                source="test",
            ),
        ]
        catalog = CommandCatalog.from_commands(commands)
        assert len(catalog.commands) == 0


class TestTrimCommandDescription:
    def test_short_description(self) -> None:
        result = _trim_command_description("Short description")
        assert result == "Short description"

    def test_exact_limit(self) -> None:
        text = "x" * 64
        result = _trim_command_description(text)
        assert result == text
        assert len(result) == 64

    def test_over_limit(self) -> None:
        text = "x" * 100
        result = _trim_command_description(text)
        assert result.endswith("...")
        assert len(result) == 64

    def test_normalizes_whitespace(self) -> None:
        result = _trim_command_description("Multiple   spaces\n\there")
        assert result == "Multiple spaces here"

    def test_very_small_limit(self) -> None:
        result = _trim_command_description("Hello world", limit=3)
        assert result == "Hel"


class TestParseCommandFile:
    def test_with_heading(self, tmp_path: Path) -> None:
        path = tmp_path / "review.md"
        path.write_text("# Review code for best practices\n\nPlease review this code.")
        command = _parse_command_file(path, "test")

        assert command is not None
        assert command.name == "review"
        assert command.description == "Review code for best practices"
        assert command.prompt == "Please review this code."

    def test_without_heading(self, tmp_path: Path) -> None:
        path = tmp_path / "review.md"
        path.write_text("Please review this code.")
        command = _parse_command_file(path, "test")

        assert command is not None
        assert command.name == "review"
        assert command.description == "run review"  # Default description
        assert command.prompt == "Please review this code."

    def test_empty_prompt(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.md"
        path.write_text("# Just a heading\n\n")
        command = _parse_command_file(path, "test")

        assert command is None  # Empty prompts are rejected

    def test_file_not_found(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.md"
        command = _parse_command_file(path, "test")
        assert command is None


class TestParseCommandDirs:
    def test_no_config(self) -> None:
        result = parse_command_dirs({})
        assert result == []

    def test_string_value(self, tmp_path: Path) -> None:
        config = {"command_dirs": str(tmp_path)}
        result = parse_command_dirs(config)
        assert result == [tmp_path]

    def test_list_value(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        config = {"command_dirs": [str(dir1), str(dir2)]}
        result = parse_command_dirs(config)
        assert result == [dir1, dir2]

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        config = {"command_dirs": str(tmp_path / "nonexistent")}
        result = parse_command_dirs(config)
        assert result == []

    def test_invalid_type(self) -> None:
        config = {"command_dirs": 123}
        result = parse_command_dirs(config)
        assert result == []


class TestLoadCommandsFromDirs:
    def test_loads_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "review.md").write_text("# Review code\n\nReview this code.")
        (tmp_path / "explain.md").write_text("# Explain code\n\nExplain this code.")
        (tmp_path / "ignored.txt").write_text("Not a markdown file")

        catalog = load_commands_from_dirs([tmp_path])

        assert len(catalog.commands) == 2
        assert "review" in catalog.by_command
        assert "explain" in catalog.by_command

    def test_multiple_dirs(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "review.md").write_text("# Review\n\nReview.")
        (dir2 / "explain.md").write_text("# Explain\n\nExplain.")

        catalog = load_commands_from_dirs([dir1, dir2])

        assert len(catalog.commands) == 2

    def test_empty_dirs(self, tmp_path: Path) -> None:
        catalog = load_commands_from_dirs([tmp_path])
        assert len(catalog.commands) == 0
