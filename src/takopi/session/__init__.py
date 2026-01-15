"""Session management module.

This module provides transport-agnostic session management including
hooks execution and response capture.
"""

from .capture import ResponseCapture
from .contexts import (
    OnErrorContext,
    PostSessionContext,
    PreSessionContext,
    SessionIdentity,
)
from .manager import HooksManager

__all__ = [
    "ResponseCapture",
    "SessionIdentity",
    "PreSessionContext",
    "PostSessionContext",
    "OnErrorContext",
    "HooksManager",
]
