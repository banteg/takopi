from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from .backends import EngineConfig, SetupIssue


def which_issue(
    cmd: str, install_cmd: str
) -> Callable[[EngineConfig, Path], list[SetupIssue]]:
    issue = install_issue(cmd, install_cmd)

    def _check(_cfg: EngineConfig, _path: Path) -> list[SetupIssue]:
        return [] if shutil.which(cmd) else [issue]

    return _check


def install_issue(cmd: str, install_cmd: str) -> SetupIssue:
    return SetupIssue(
        f"install {cmd}",
        (f"   [dim]$[/] {install_cmd}",),
    )
