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


def _claude_check_setup(config: EngineConfig, _config_path: Path) -> list[SetupIssue]:
    cmd = config.get("cmd")
    claude_cmd = str(cmd) if cmd else "claude"
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
    cmd = config.get("cmd")
    if cmd is None:
        claude_cmd = shutil.which("claude")
    else:
        cmd_str = str(cmd)
        claude_cmd = shutil.which(cmd_str) or cmd_str
    if not claude_cmd:
        raise ConfigError(
            "claude not found on PATH. Install Claude Code with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "  # or run\n"
            "  claude install"
        )

    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = []
    else:
        if isinstance(extra_args_value, (list, tuple)):
            extra_args = [str(item) for item in extra_args_value]
        else:
            extra_args = [str(extra_args_value)]

    model = config.get("model")
    system_prompt = config.get("system_prompt")
    append_system_prompt = config.get("append_system_prompt")
    permission_mode = config.get("permission_mode")
    output_style = config.get("output_style")
    allowed_tools = config.get("allowed_tools")
    disallowed_tools = config.get("disallowed_tools")
    tools = config.get("tools")
    max_turns = config.get("max_turns")
    max_budget_usd = config.get("max_budget_usd")
    include_partial_messages = config.get("include_partial_messages")
    dangerously_skip_permissions = config.get("dangerously_skip_permissions")
    idle_timeout_s = config.get("idle_timeout_s")

    mcp_config = config.get("mcp_config")
    add_dirs = config.get("add_dirs")

    title = config.get("title")
    if title is None:
        title = model or "claude"
    if title is not None:
        title = str(title)

    return ClaudeRunner(
        claude_cmd=claude_cmd,
        model=model,
        system_prompt=system_prompt,
        append_system_prompt=append_system_prompt,
        permission_mode=permission_mode,
        output_style=output_style,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        tools=tools,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        include_partial_messages=include_partial_messages,
        dangerously_skip_permissions=dangerously_skip_permissions,
        mcp_config=mcp_config,
        add_dirs=add_dirs,
        extra_args=extra_args,
        idle_timeout_s=idle_timeout_s,
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
