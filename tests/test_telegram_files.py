from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from takopi.telegram import files as tg_files
from takopi.telegram.files import ZipTooLargeError, zip_directory


def test_zip_directory_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "dir"
    target.mkdir()
    (target / "safe.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link_path = target / "leak.txt"
    try:
        link_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")

    payload = zip_directory(root, Path("dir"), deny_globs=())

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())

    assert "dir/safe.txt" in names
    assert "dir/leak.txt" not in names


def test_zip_directory_limits_size(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "dir"
    target.mkdir()
    (target / "data.bin").write_bytes(b"x" * 1024)

    with pytest.raises(ZipTooLargeError):
        zip_directory(root, Path("dir"), deny_globs=(), max_bytes=10)


def test_split_command_args_falls_back_on_bad_quotes() -> None:
    assert tg_files.split_command_args('bad "quote') == ("bad", '"quote')


def test_parse_file_command_unknown_command() -> None:
    command, rest, error = tg_files.parse_file_command("nope arg")
    assert command is None
    assert rest == "arg"
    assert error == tg_files.file_usage()


def test_parse_file_prompt_errors() -> None:
    path, force, error = tg_files.parse_file_prompt("--wat", allow_empty=False)
    assert path is None
    assert force is False
    assert error == "unknown flag: --wat"

    path, force, error = tg_files.parse_file_prompt("", allow_empty=False)
    assert path is None
    assert force is False
    assert error == "missing path"


def test_parse_file_prompt_force_flag() -> None:
    path, force, error = tg_files.parse_file_prompt(
        "--force note.txt", allow_empty=False
    )
    assert path == "note.txt"
    assert force is True
    assert error is None


def test_normalize_relative_path_rejects_invalid() -> None:
    for value in ("", "   ", "~/.ssh", "/etc/passwd", "../secret", ".git/config"):
        assert tg_files.normalize_relative_path(value) is None


def test_normalize_relative_path_rejects_dot_only() -> None:
    assert tg_files.normalize_relative_path("./") is None


def test_normalize_relative_path_strips_dots() -> None:
    assert tg_files.normalize_relative_path("docs/./guide.txt") == Path(
        "docs/guide.txt"
    )


def test_resolve_path_within_root_rejects_escape(tmp_path: Path) -> None:
    assert tg_files.resolve_path_within_root(tmp_path, Path("../escape")) is None


def test_deny_reason_matches_patterns() -> None:
    assert tg_files.deny_reason(Path(".git/config"), ["**/*.pem"]) == ".git/**"
    assert tg_files.deny_reason(Path("secrets/key.pem"), ["**/*.pem"]) == "**/*.pem"


def test_format_bytes_various_units() -> None:
    assert tg_files.format_bytes(0) == "0 b"
    assert tg_files.format_bytes(1536) == "1.5 kb"
    assert tg_files.format_bytes(20480) == "20 kb"


def test_default_upload_name_fallbacks() -> None:
    assert tg_files.default_upload_name("", "files/report.txt") == "report.txt"
    assert tg_files.default_upload_name(None, None) == "upload.bin"
