from __future__ import annotations

import re

ID_PATTERN = r"^[a-z0-9_]{1,32}$"
_ID_RE = re.compile(ID_PATTERN)

RESERVED_CLI_COMMANDS = frozenset({"config", "doctor", "init", "plugins"})
RESERVED_CHAT_COMMANDS = frozenset(
    {"cancel", "file", "new", "agent", "model", "reasoning", "trigger", "topic", "ctx"}
)
RESERVED_ENGINE_IDS = RESERVED_CLI_COMMANDS | RESERVED_CHAT_COMMANDS
RESERVED_COMMAND_IDS = RESERVED_CLI_COMMANDS | RESERVED_CHAT_COMMANDS


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))
