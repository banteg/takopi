#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "changelog.md"
DEST = ROOT / "docs" / "reference" / "changelog.md"


def main() -> None:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.is_symlink():
        DEST.unlink()
    shutil.copyfile(SRC, DEST)


if __name__ == "__main__":
    main()
