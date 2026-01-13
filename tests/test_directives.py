from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.directives import has_directive, parse_directives, DirectiveError


def _projects(aliases: list[str] | None = None) -> ProjectsConfig:
    if aliases is None:
        aliases = ["proj", "other"]
    projects = {
        alias.lower(): ProjectConfig(
            alias=alias,
            path=Path("."),
            worktrees_dir=Path(".worktrees"),
        )
        for alias in aliases
    }
    return ProjectsConfig(projects=projects, default_project=None)


ENGINE_IDS = ("codex", "claude", "pi")


class TestHasDirective:
    def test_empty_text(self) -> None:
        assert has_directive("", engine_ids=ENGINE_IDS, projects=_projects()) is False

    def test_plain_text(self) -> None:
        assert (
            has_directive("hello world", engine_ids=ENGINE_IDS, projects=_projects())
            is False
        )

    def test_engine_directive(self) -> None:
        assert (
            has_directive("/claude hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_engine_directive_case_insensitive(self) -> None:
        assert (
            has_directive("/CLAUDE hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_project_directive(self) -> None:
        assert (
            has_directive("/proj hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_project_directive_case_insensitive(self) -> None:
        assert (
            has_directive("/PROJ hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_unknown_slash_command(self) -> None:
        # /unknown is neither engine nor project
        assert (
            has_directive("/unknown hi", engine_ids=ENGINE_IDS, projects=_projects())
            is False
        )

    def test_engine_with_bot_mention(self) -> None:
        # /claude@botname should still match
        assert (
            has_directive(
                "/claude@mybot hi", engine_ids=ENGINE_IDS, projects=_projects()
            )
            is True
        )

    def test_branch_directive_alone(self) -> None:
        # @branch alone is not enough - needs /engine or /project
        assert (
            has_directive("@main do it", engine_ids=ENGINE_IDS, projects=_projects())
            is False
        )

    def test_whitespace_before_directive(self) -> None:
        assert (
            has_directive("  /claude hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_newline_before_directive(self) -> None:
        assert (
            has_directive("\n/claude hi", engine_ids=ENGINE_IDS, projects=_projects())
            is True
        )

    def test_slash_only(self) -> None:
        assert (
            has_directive("/ hi", engine_ids=ENGINE_IDS, projects=_projects()) is False
        )

    def test_multiple_directives(self) -> None:
        assert (
            has_directive(
                "/claude /proj hi", engine_ids=ENGINE_IDS, projects=_projects()
            )
            is True
        )


class TestParseDirectives:
    def test_engine_directive(self) -> None:
        result = parse_directives(
            "/claude hi", engine_ids=ENGINE_IDS, projects=_projects()
        )
        assert result.engine == "claude"
        assert result.prompt == "hi"

    def test_project_directive(self) -> None:
        result = parse_directives(
            "/proj hi", engine_ids=ENGINE_IDS, projects=_projects()
        )
        assert result.project == "proj"
        assert result.prompt == "hi"

    def test_engine_and_project(self) -> None:
        result = parse_directives(
            "/claude /proj hi", engine_ids=ENGINE_IDS, projects=_projects()
        )
        assert result.engine == "claude"
        assert result.project == "proj"
        assert result.prompt == "hi"

    def test_multiple_engines_raises(self) -> None:
        with pytest.raises(DirectiveError, match="multiple engine"):
            parse_directives(
                "/claude /pi hi", engine_ids=ENGINE_IDS, projects=_projects()
            )

    def test_multiple_projects_raises(self) -> None:
        with pytest.raises(DirectiveError, match="multiple project"):
            parse_directives(
                "/proj /other hi", engine_ids=ENGINE_IDS, projects=_projects()
            )

    def test_branch_directive(self) -> None:
        result = parse_directives(
            "/proj @main hi", engine_ids=ENGINE_IDS, projects=_projects()
        )
        assert result.project == "proj"
        assert result.branch == "main"
        assert result.prompt == "hi"
