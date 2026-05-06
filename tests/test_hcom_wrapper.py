from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ConfigError
from takopi.runners import claude as claude_runner
from takopi.runners import codex as codex_runner
from takopi.runners.hcom_wrap import (
    HCOM_DISABLED,
    HcomWrap,
    parse_hcom_config,
)


def test_hcom_disabled_returns_engine_command_unchanged() -> None:
    wrap = HCOM_DISABLED
    assert wrap.wrap_command("/usr/bin/claude") == "/usr/bin/claude"
    assert wrap.wrap_args(
        "/usr/bin/claude", ["-p", "--output-format", "stream-json"]
    ) == ["-p", "--output-format", "stream-json"]


def test_hcom_enabled_prepends_engine_subcommand() -> None:
    wrap = HcomWrap(enabled=True, cmd="/usr/local/bin/hcom", args=("--tag", "takopi"))
    assert wrap.wrap_command("/usr/bin/claude") == "/usr/local/bin/hcom"
    assert wrap.wrap_args(
        "/usr/bin/claude",
        ["-p", "--output-format", "stream-json", "--", "hi"],
    ) == [
        "--tag",
        "takopi",
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--",
        "hi",
    ]


def test_hcom_enabled_uses_basename_when_engine_cmd_is_absolute_path(
    tmp_path: Path,
) -> None:
    wrap = HcomWrap(enabled=True, cmd="hcom", args=())
    fake_codex = tmp_path / "codex"
    assert wrap.wrap_args(str(fake_codex), ["exec"]) == ["codex", "exec"]


def test_parse_hcom_config_defaults_disabled(tmp_path: Path) -> None:
    wrap = parse_hcom_config({}, config_path=tmp_path / "takopi.toml", section="claude")
    assert wrap.enabled is False
    assert wrap.cmd == "hcom"
    assert wrap.args == ()


def test_parse_hcom_config_resolves_binary_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "takopi.runners.hcom_wrap.shutil.which",
        lambda name: f"/opt/bin/{name}" if name == "hcom" else None,
    )
    wrap = parse_hcom_config(
        {"hcom": True, "hcom_args": ["--tag", "takopi"]},
        config_path=tmp_path / "takopi.toml",
        section="claude",
    )
    assert wrap.enabled is True
    assert wrap.cmd == "/opt/bin/hcom"
    assert wrap.args == ("--tag", "takopi")


def test_parse_hcom_config_keeps_literal_cmd_when_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("takopi.runners.hcom_wrap.shutil.which", lambda name: None)
    wrap = parse_hcom_config(
        {"hcom": True, "hcom_cmd": "hcom"},
        config_path=tmp_path / "takopi.toml",
        section="codex",
    )
    assert wrap.enabled is True
    assert wrap.cmd == "hcom"


def test_parse_hcom_config_rejects_non_bool_enable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        parse_hcom_config(
            {"hcom": "yes"},
            config_path=tmp_path / "takopi.toml",
            section="claude",
        )


def test_parse_hcom_config_rejects_empty_cmd(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        parse_hcom_config(
            {"hcom_cmd": ""},
            config_path=tmp_path / "takopi.toml",
            section="claude",
        )


def test_parse_hcom_config_rejects_non_string_args(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        parse_hcom_config(
            {"hcom_args": ["--tag", 1]},
            config_path=tmp_path / "takopi.toml",
            section="claude",
        )


def test_claude_build_runner_disables_hcom_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_runner.shutil, "which", lambda name: f"/opt/bin/{name}")
    runner = claude_runner.build_runner({}, tmp_path / "takopi.toml")
    assert isinstance(runner, claude_runner.ClaudeRunner)
    assert runner.hcom.enabled is False
    assert runner.command() == "/opt/bin/claude"
    args = runner.build_args("hello", None, state=None)
    assert args[-1] == "hello"
    assert "--output-format" in args
    assert "stream-json" in args


def test_claude_build_runner_wraps_with_hcom_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_which(name: str) -> str:
        return f"/opt/bin/{name}"

    monkeypatch.setattr(claude_runner.shutil, "which", fake_which)
    monkeypatch.setattr("takopi.runners.hcom_wrap.shutil.which", fake_which)

    runner = claude_runner.build_runner(
        {"hcom": True, "hcom_args": ["--tag", "takopi"]},
        tmp_path / "takopi.toml",
    )
    assert isinstance(runner, claude_runner.ClaudeRunner)
    assert runner.hcom.enabled is True

    assert runner.command() == "/opt/bin/hcom"
    args = runner.build_args("hello", None, state=None)
    assert args[:3] == ["--tag", "takopi", "claude"]
    assert "--output-format" in args
    assert args[-1] == "hello"


def test_codex_build_runner_disables_hcom_by_default(tmp_path: Path) -> None:
    runner = codex_runner.build_runner({}, tmp_path / "takopi.toml")
    assert isinstance(runner, codex_runner.CodexRunner)
    assert runner.hcom.enabled is False
    assert runner.command() == "codex"
    args = runner.build_args("hi", None, state=None)
    assert "exec" in args
    assert "--json" in args


def test_codex_build_runner_wraps_with_hcom_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "takopi.runners.hcom_wrap.shutil.which",
        lambda name: f"/opt/bin/{name}" if name == "hcom" else None,
    )

    runner = codex_runner.build_runner(
        {"hcom": True, "hcom_args": ["--tag", "takopi"]},
        tmp_path / "takopi.toml",
    )
    assert isinstance(runner, codex_runner.CodexRunner)
    assert runner.hcom.enabled is True

    assert runner.command() == "/opt/bin/hcom"
    args = runner.build_args("hi", None, state=None)
    assert args[:3] == ["--tag", "takopi", "codex"]
    assert "exec" in args
    assert "--json" in args


def test_codex_build_runner_rejects_invalid_hcom_args(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        codex_runner.build_runner(
            {"hcom": True, "hcom_args": "not-a-list"},
            tmp_path / "takopi.toml",
        )


def test_claude_build_runner_rejects_invalid_hcom_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_runner.shutil, "which", lambda name: f"/opt/bin/{name}")
    with pytest.raises(ConfigError):
        claude_runner.build_runner({"hcom": 1}, tmp_path / "takopi.toml")
