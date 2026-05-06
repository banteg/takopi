"""Shared helpers for wrapping engine CLIs with `hcom`.

When ``hcom`` is enabled in an engine's config, takopi spawns
``hcom [hcom_args...] <engine> [engine_args...]`` instead of ``<engine>
[engine_args...]``. This lets users plug into the
[hcom](https://github.com/aannoo/hcom) inter-agent messaging layer without
changing the engine binary itself.

The behavior is intentionally minimal: takopi treats hcom as a transparent
prefix and forwards the existing engine arguments unchanged. If hcom does not
support a particular engine subcommand or flag combination (for example, codex
``exec`` mode), the underlying error surfaces through the normal subprocess
output.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError


@dataclass(frozen=True, slots=True)
class HcomWrap:
    """Configured invocation prefix for `hcom <engine> ...`."""

    enabled: bool
    cmd: str
    args: tuple[str, ...]

    def wrap_command(self, engine_cmd: str) -> str:
        """Return the program to spawn (`hcom` or the original engine cmd)."""
        if not self.enabled:
            return engine_cmd
        return self.cmd

    def wrap_args(
        self,
        engine_cmd: str,
        engine_args: list[str],
    ) -> list[str]:
        """Return the argv tail to pass to the wrapped command.

        When disabled, returns ``engine_args`` unchanged. When enabled, returns
        ``[*hcom_args, <engine-subcommand>, *engine_args]`` where the
        engine-subcommand is the basename of ``engine_cmd``. This lets hcom's
        argv parser see the canonical tool name (``claude``/``codex``) even if
        the user pointed ``claude_cmd``/``codex_cmd`` at an absolute path.
        """
        if not self.enabled:
            return list(engine_args)
        engine_subcommand = Path(engine_cmd).name or engine_cmd
        return [*self.args, engine_subcommand, *engine_args]


HCOM_DISABLED = HcomWrap(enabled=False, cmd="hcom", args=())


def parse_hcom_config(
    config: dict[str, Any],
    *,
    config_path: Path,
    section: str,
) -> HcomWrap:
    """Parse ``[<section>] hcom*`` keys into an :class:`HcomWrap`.

    Recognized keys:

    - ``hcom`` (bool, default ``false``): enable the wrapper.
    - ``hcom_cmd`` (string, default ``"hcom"``): path/name of the hcom binary.
    - ``hcom_args`` (list[str], default ``[]``): extra args inserted between
      ``hcom`` and the engine subcommand (e.g. ``["--tag", "takopi"]``).
    """
    enabled_value = config.get("hcom", False)
    if not isinstance(enabled_value, bool):
        raise ConfigError(
            f"Invalid `{section}.hcom` in {config_path}; expected a boolean."
        )

    cmd_value = config.get("hcom_cmd", "hcom")
    if not isinstance(cmd_value, str) or not cmd_value:
        raise ConfigError(
            f"Invalid `{section}.hcom_cmd` in {config_path}; "
            "expected a non-empty string."
        )

    args_value = config.get("hcom_args", [])
    if args_value is None:
        args_tuple: tuple[str, ...] = ()
    elif isinstance(args_value, list) and all(
        isinstance(item, str) for item in args_value
    ):
        args_tuple = tuple(args_value)
    else:
        raise ConfigError(
            f"Invalid `{section}.hcom_args` in {config_path}; "
            "expected a list of strings."
        )

    if not enabled_value:
        return HcomWrap(enabled=False, cmd=cmd_value, args=args_tuple)

    resolved = shutil.which(cmd_value) or cmd_value
    return HcomWrap(enabled=True, cmd=resolved, args=args_tuple)
