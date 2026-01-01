from __future__ import annotations

from pathlib import Path

from takopi.utils.paths import relativize_command


def test_relativize_command_rewrites_cwd_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = (
        f'find {base}/tests -type f -name "*.py" | head -20'
    )
    expected = 'find tests -type f -name "*.py" | head -20'
    assert relativize_command(command, base_dir=base) == expected


def test_relativize_command_rewrites_equals_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = f'rg -n --files -g "*.py" --path={base}/src'
    expected = 'rg -n --files -g "*.py" --path=src'
    assert relativize_command(command, base_dir=base) == expected
