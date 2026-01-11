from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

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
