from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Sequence

from .backends import EngineConfig, SetupIssue


def which_issue(
    cmd: str, install_cmds: Sequence[str]
) -> Callable[[EngineConfig, Path], list[SetupIssue]]:
    issue = install_issue(cmd, install_cmds)

    def _check(_cfg: EngineConfig, _path: Path) -> list[SetupIssue]:
        return [] if shutil.which(cmd) else [issue]

    return _check


def install_issue(cmd: str, install_cmds: Sequence[str]) -> SetupIssue:
    lines = tuple(f"   [dim]$[/] {line}" for line in install_cmds)
    return SetupIssue(f"install {cmd}", lines)
