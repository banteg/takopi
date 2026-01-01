from __future__ import annotations

import re
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


def _parse_str_list(
    value: Any,
    *,
    field: str,
    config_path: Path,
) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [item for item in re.split(r"[,\s]+", value.strip()) if item]
        return items
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ConfigError(
        f"Invalid `claude.{field}` in {config_path}; expected a string or list of strings."
    )


def _parse_str(
    value: Any,
    *,
    field: str,
    config_path: Path,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ConfigError(
        f"Invalid `claude.{field}` in {config_path}; expected a string."
    )


def _parse_bool(
    value: Any,
    *,
    field: str,
    config_path: Path,
) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ConfigError(
        f"Invalid `claude.{field}` in {config_path}; expected a boolean."
    )


def _parse_int(
    value: Any,
    *,
    field: str,
    config_path: Path,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"Invalid `claude.{field}` in {config_path}; expected an integer."
        )
    return value


def _parse_float(
    value: Any,
    *,
    field: str,
    config_path: Path,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            f"Invalid `claude.{field}` in {config_path}; expected a number."
        )
    return float(value)


def _claude_check_setup(config: EngineConfig, _config_path: Path) -> list[SetupIssue]:
    cmd = config.get("cmd")
    claude_cmd = cmd if isinstance(cmd, str) and cmd else "claude"
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


def _claude_build_runner(config: EngineConfig, config_path: Path) -> Runner:
    cmd = config.get("cmd")
    if cmd is None:
        claude_cmd = shutil.which("claude")
    elif isinstance(cmd, str):
        claude_cmd = shutil.which(cmd) or cmd
    else:
        raise ConfigError(
            f"Invalid `claude.cmd` in {config_path}; expected a string."
        )
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
    elif isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `claude.extra_args` in {config_path}; expected a list of strings."
        )

    model = _parse_str(config.get("model"), field="model", config_path=config_path)
    system_prompt = _parse_str(
        config.get("system_prompt"), field="system_prompt", config_path=config_path
    )
    append_system_prompt = _parse_str(
        config.get("append_system_prompt"),
        field="append_system_prompt",
        config_path=config_path,
    )
    permission_mode = _parse_str(
        config.get("permission_mode"),
        field="permission_mode",
        config_path=config_path,
    )
    output_style = _parse_str(
        config.get("output_style"), field="output_style", config_path=config_path
    )
    allowed_tools = _parse_str_list(
        config.get("allowed_tools"), field="allowed_tools", config_path=config_path
    )
    disallowed_tools = _parse_str_list(
        config.get("disallowed_tools"),
        field="disallowed_tools",
        config_path=config_path,
    )
    tools = _parse_str_list(config.get("tools"), field="tools", config_path=config_path)
    max_turns = _parse_int(
        config.get("max_turns"), field="max_turns", config_path=config_path
    )
    max_budget_usd = _parse_float(
        config.get("max_budget_usd"),
        field="max_budget_usd",
        config_path=config_path,
    )
    include_partial_messages = _parse_bool(
        config.get("include_partial_messages"),
        field="include_partial_messages",
        config_path=config_path,
    )
    dangerously_skip_permissions = _parse_bool(
        config.get("dangerously_skip_permissions"),
        field="dangerously_skip_permissions",
        config_path=config_path,
    )
    idle_timeout_s = _parse_float(
        config.get("idle_timeout_s"),
        field="idle_timeout_s",
        config_path=config_path,
    )

    mcp_config = _parse_str_list(
        config.get("mcp_config"), field="mcp_config", config_path=config_path
    )
    add_dirs = _parse_str_list(
        config.get("add_dirs"), field="add_dirs", config_path=config_path
    )

    title = _parse_str(config.get("title"), field="title", config_path=config_path)
    if title is None:
        title = model or "claude"

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
        include_partial_messages=bool(include_partial_messages),
        dangerously_skip_permissions=bool(dangerously_skip_permissions),
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
