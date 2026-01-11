from __future__ import annotations

from ..utils.files import (
    ZipTooLargeError,
    default_upload_name,
    default_upload_path,
    deny_reason,
    file_usage,
    format_bytes,
    normalize_relative_path,
    parse_file_command,
    parse_file_prompt,
    resolve_path_within_root,
    split_command_args,
    write_bytes_atomic,
    zip_directory,
)

__all__ = [
    "ZipTooLargeError",
    "default_upload_name",
    "default_upload_path",
    "deny_reason",
    "file_get_usage",
    "file_put_usage",
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


def file_put_usage() -> str:
    return "usage: `/file put <path>`"


def file_get_usage() -> str:
    return "usage: `/file get <path>`"
