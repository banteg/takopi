from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError
from .runner import Runner
from .runners.codex import CodexRunner
from .runners.claude import ClaudeRunner

EngineConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SetupIssue:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EngineBackend:
    id: str
    display_name: str
    check_setup: Callable[[EngineConfig, Path], list[SetupIssue]]
    build_runner: Callable[[EngineConfig, Path], Runner]
    startup_message: Callable[[str], str]


def _codex_check_setup(_config: EngineConfig, _config_path: Path) -> list[SetupIssue]:
    if shutil.which("codex") is None:
        return [
            SetupIssue(
                "Install the Codex CLI",
                ("   [dim]$[/] npm install -g @openai/codex",),
            )
        ]
    return []


def _codex_build_runner(config: EngineConfig, config_path: Path) -> Runner:
    codex_cmd = shutil.which("codex")
    if not codex_cmd:
        raise ConfigError(
            "codex not found on PATH. Install the Codex CLI with:\n"
            "  npm install -g @openai/codex\n"
            "  # or on macOS\n"
            "  brew install codex"
        )

    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = ["-c", "notify=[]"]
    elif isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `codex.extra_args` in {config_path}; expected a list of strings."
        )

    title = "Codex"
    profile_value = config.get("profile")
    if profile_value:
        if not isinstance(profile_value, str):
            raise ConfigError(
                f"Invalid `codex.profile` in {config_path}; expected a string."
            )
        extra_args.extend(["--profile", profile_value])
        title = profile_value

    return CodexRunner(codex_cmd=codex_cmd, extra_args=extra_args, title=title)


def _codex_startup_message(cwd: str) -> str:
    return f"codex is ready\npwd: {cwd}"


def _claude_check_setup(_config: EngineConfig, _config_path: Path) -> list[SetupIssue]:
    claude_cmd = "claude"
    if shutil.which(claude_cmd) is None:
        return [
            SetupIssue(
                "Install the Claude Code CLI",
                (
                    "   [dim]$[/] npm install -g @anthropic-ai/claude-code",
                    "   [dim]# or[/]",
                    "   [dim]$[/] claude install",
                ),
            )
        ]
    return []


def _claude_build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    claude_cmd = shutil.which("claude")
    if not claude_cmd:
        raise ConfigError(
            "claude not found on PATH. Install Claude Code with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "  # or run\n"
            "  claude install"
        )

    model = config.get("model")
    allowed_tools = config.get("allowed_tools")
    dangerously_skip_permissions = config.get("dangerously_skip_permissions")
    use_api_billing = config.get("use_api_billing")
    title = str(model) if model is not None else "claude"

    return ClaudeRunner(
        claude_cmd=claude_cmd,
        model=model,
        allowed_tools=allowed_tools,
        dangerously_skip_permissions=dangerously_skip_permissions,
        use_api_billing=use_api_billing,
        session_title=title,
    )


def _claude_startup_message(cwd: str) -> str:
    return f"claude is ready\npwd: {cwd}"


_ENGINE_BACKENDS: dict[str, EngineBackend] = {
    "codex": EngineBackend(
        id="codex",
        display_name="Codex",
        check_setup=_codex_check_setup,
        build_runner=_codex_build_runner,
        startup_message=_codex_startup_message,
    ),
    "claude": EngineBackend(
        id="claude",
        display_name="Claude",
        check_setup=_claude_check_setup,
        build_runner=_claude_build_runner,
        startup_message=_claude_startup_message,
    ),
}


def get_backend(engine_id: str) -> EngineBackend:
    try:
        return _ENGINE_BACKENDS[engine_id]
    except KeyError as exc:
        available = ", ".join(sorted(_ENGINE_BACKENDS))
        raise ConfigError(
            f"Unknown engine {engine_id!r}. Available: {available}."
        ) from exc


def list_backends() -> list[EngineBackend]:
    return list(_ENGINE_BACKENDS.values())


def list_backend_ids() -> list[str]:
    return sorted(_ENGINE_BACKENDS)


def get_engine_config(
    config: dict[str, Any], engine_id: str, config_path: Path
) -> EngineConfig:
    engine_cfg = config.get(engine_id) or {}
    if not isinstance(engine_cfg, dict):
        raise ConfigError(
            f"Invalid `{engine_id}` config in {config_path}; expected a table."
        )
    return engine_cfg
