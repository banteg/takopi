from __future__ import annotations

import io
import os
import shlex
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

__all__ = [
    "ZipTooLargeError",
    "default_upload_name",
    "default_upload_path",
    "deny_reason",
    "file_usage",
    "format_bytes",
    "normalize_relative_path",
    "parse_file_command",
    "parse_file_prompt",
    "resolve_path_within_root",
    "split_command_args",
    "write_bytes_atomic",
    "zip_directory",
]


def split_command_args(text: str) -> tuple[str, ...]:
    if not text.strip():
        return ()
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())


def file_usage() -> str:
    return "usage: `/file put <path>` or `/file get <path>`"


def parse_file_command(args_text: str) -> tuple[str | None, str, str | None]:
    tokens = split_command_args(args_text)
    if not tokens:
        return None, "", file_usage()
    command = tokens[0].lower()
    rest = " ".join(tokens[1:]).strip()
    if command not in {"put", "get"}:
        return None, rest, file_usage()
    return command, rest, None


def parse_file_prompt(
    prompt: str, *, allow_empty: bool
) -> tuple[str | None, bool, str | None]:
    tokens = split_command_args(prompt)
    force = False
    parts: list[str] = []
    for token in tokens:
        if token == "--force":
            force = True
            continue
        if token.startswith("--"):
            return None, force, f"unknown flag: {token}"
        parts.append(token)
    path = " ".join(parts).strip()
    if not path and not allow_empty:
        return None, force, "missing path"
    return (path or None), force, None


def normalize_relative_path(value: str) -> Path | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("~"):
        return None
    path = Path(cleaned)
    if path.is_absolute():
        return None
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        return None
    if ".." in parts:
        return None
    if ".git" in parts:
        return None
    return Path(*parts)


def resolve_path_within_root(root: Path, rel_path: Path) -> Path | None:
    root_resolved = root.resolve(strict=False)
    target = (root / rel_path).resolve(strict=False)
    if not target.is_relative_to(root_resolved):
        return None
    return target


def deny_reason(rel_path: Path, deny_globs: Sequence[str]) -> str | None:
    if ".git" in rel_path.parts:
        return ".git/**"
    posix = PurePosixPath(rel_path.as_posix())
    for pattern in deny_globs:
        if posix.match(pattern):
            return pattern
    return None


def format_bytes(value: int) -> str:
    size = max(0.0, float(value))
    units = ("b", "kb", "mb", "gb", "tb")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "b":
                return f"{int(size)} b"
            if size < 10:
                return f"{size:.1f} {unit}"
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{int(size)} B"


def default_upload_name(filename: str | None, file_path: str | None) -> str:
    name = Path(filename or "").name
    if not name and file_path:
        name = Path(file_path).name
    if not name:
        name = "upload.bin"
    return name


def default_upload_path(
    uploads_dir: str, filename: str | None, file_path: str | None
) -> Path:
    return Path(uploads_dir) / default_upload_name(filename, file_path)


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", delete=False, dir=path.parent, prefix=".takopi-upload-"
    ) as handle:
        handle.write(payload)
        temp_name = handle.name
    Path(temp_name).replace(path)


class ZipTooLargeError(Exception):
    pass


def zip_directory(
    root: Path,
    rel_path: Path,
    deny_globs: Sequence[str],
    *,
    max_bytes: int | None = None,
) -> bytes:
    target = root / rel_path
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _, filenames in os.walk(target, followlinks=False):
            dir_path = Path(dirpath)
            for filename in filenames:
                item = dir_path / filename
                if item.is_symlink():
                    continue
                if not item.is_file():
                    continue
                rel_item = rel_path / item.relative_to(target)
                if deny_reason(rel_item, deny_globs) is not None:
                    continue
                archive.write(item, arcname=rel_item.as_posix())
                if max_bytes is not None and buffer.tell() > max_bytes:
                    raise ZipTooLargeError()
    payload = buffer.getvalue()
    if max_bytes is not None and len(payload) > max_bytes:
        raise ZipTooLargeError()
    return payload
