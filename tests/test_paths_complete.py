import os
from pathlib import Path

from takopi.utils.paths import relativize_path, relativize_command


def test_relativize_path_empty_value() -> None:
    assert relativize_path("") == ""


def test_relativize_path_equal_to_base() -> None:
    cwd = Path.cwd()
    assert relativize_path(str(cwd)) == "."


def test_relativize_path_starts_with_base() -> None:
    cwd = Path.cwd()
    test_path = str(cwd / "test.py")
    assert relativize_path(test_path) == "test.py"


def test_relativize_path_with_custom_base() -> None:
    base = Path("/tmp")
    test_path = str(base / "test.py")
    assert relativize_path(test_path, base_dir=base) == "test.py"


def test_relativize_path_empty_base() -> None:
    assert relativize_path("test.py", base_dir=Path("")) == "test.py"


def test_relativize_path_starts_with_base_trailing_sep() -> None:
    cwd = Path.cwd()
    test_path = f"{str(cwd)}{os.sep}test.py"
    assert relativize_path(test_path) == "test.py"


def test_relativize_path_forward_slash_prefix() -> None:
    cwd = Path.cwd()
    test_path = f"{str(cwd)}/test.py"
    assert relativize_path(test_path) == "test.py"


def test_relativize_path_not_matching_base() -> None:
    assert relativize_path("/other/path/test.py") == "/other/path/test.py"


def test_relativize_command() -> None:
    cwd = Path.cwd()
    cmd = f"python {cwd / 'script.py'}"
    expected = f"python script.py"
    assert relativize_command(cmd) == expected


def test_relativize_command_with_custom_base() -> None:
    base = Path("/tmp")
    cmd = f"python {base / 'script.py'}"
    expected = f"python script.py"
    assert relativize_command(cmd, base_dir=base) == expected
