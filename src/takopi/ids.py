from __future__ import annotations

import re

ID_PATTERN = r"^[a-z0-9_]{1,32}$"
_ID_RE = re.compile(ID_PATTERN)


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))
