from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..model import ActionKind
from ..utils.paths import relativize_command, relativize_path


def tool_input_path(
    tool_input: Mapping[str, Any],
    *,
    path_keys: Sequence[str],
) -> str | None:
    for key in path_keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def tool_kind_and_title(
    tool_name: str,
    tool_input: Mapping[str, Any],
    *,
    path_keys: Sequence[str],
    task_kind: ActionKind = "subagent",
) -> tuple[ActionKind, str]:
    name_lower = tool_name.lower()

    if name_lower in {"bash", "shell", "killshell"}:
        command = tool_input.get("command")
        display = relativize_command(str(command or tool_name))
        return "command", display

    if name_lower in {"edit", "write", "notebookedit", "multiedit"}:
        path = tool_input_path(tool_input, path_keys=path_keys)
        if path:
            return "file_change", relativize_path(str(path))
        return "file_change", str(tool_name)

    if name_lower == "read":
        path = tool_input_path(tool_input, path_keys=path_keys)
        if path:
            return "tool", f"read: `{relativize_path(str(path))}`"
        return "tool", "read"

    if name_lower == "glob":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"glob: `{pattern}`"
        return "tool", "glob"

    if name_lower == "grep":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"grep: {pattern}"
        return "tool", "grep"

    if name_lower == "find":
        pattern = tool_input.get("pattern")
        if pattern:
            return "tool", f"find: {pattern}"
        return "tool", "find"

    if name_lower == "ls":
        path = tool_input_path(tool_input, path_keys=path_keys)
        if path:
            return "tool", f"ls: `{relativize_path(str(path))}`"
        return "tool", "ls"

    if name_lower in {"websearch", "web_search"}:
        query = tool_input.get("query")
        return "web_search", str(query or "search")

    if name_lower in {"webfetch", "web_fetch"}:
        url = tool_input.get("url")
        return "web_search", str(url or "fetch")

    if name_lower in {"todowrite", "todoread"}:
        return "note", "update todos" if "write" in name_lower else "read todos"

    if name_lower == "askuserquestion":
        return "note", "ask user"

    if name_lower in {"task", "agent"}:
        desc = tool_input.get("description") or tool_input.get("prompt")
        return task_kind, str(desc or tool_name)

    return "tool", tool_name
