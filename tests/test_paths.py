from __future__ import annotations

from pathlib import Path

from takopi.utils.paths import (
    relativize_command,
    relativize_path,
    reset_run_base_dir,
    set_run_base_dir,
)


def test_relativize_command_rewrites_cwd_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = f'find {base}/tests -type f -name "*.py" | head -20'
    expected = 'find tests -type f -name "*.py" | head -20'
    assert relativize_command(command, base_dir=base) == expected


def test_relativize_command_rewrites_equals_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = f'rg -n --files -g "*.py" --path={base}/src'
    expected = 'rg -n --files -g "*.py" --path=src'
    assert relativize_command(command, base_dir=base) == expected


def test_relativize_path_ignores_sibling_prefix(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    value = str(tmp_path / "repo2" / "file.txt")
    assert relativize_path(value, base_dir=base) == value


def test_relativize_path_inside_base(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    value = str(base / "src" / "app.py")
    assert relativize_path(value, base_dir=base) == "src/app.py"


def test_relativize_path_uses_run_base_dir(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    token = set_run_base_dir(base)
    try:
        value = str(base / "src" / "app.py")
        assert relativize_path(value) == "src/app.py"
    finally:
        reset_run_base_dir(token)
