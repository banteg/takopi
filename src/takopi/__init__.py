from __future__ import annotations

import inspect
import typing

__version__ = "0.21.3"


# Workaround for Pydantic calling typing._eval_type with a removed kwarg on Python 3.14.
def _patch_typing_eval_type_for_pydantic() -> None:
    eval_type = getattr(typing, "_eval_type", None)
    if eval_type is None:
        return
    try:
        sig = inspect.signature(eval_type)
    except (TypeError, ValueError):
        return
    if "prefer_fwd_module" in sig.parameters:
        return

    def _eval_type_compat(*args, **kwargs):
        kwargs.pop("prefer_fwd_module", None)
        return eval_type(*args, **kwargs)

    typing._eval_type = _eval_type_compat  # type: ignore[attr-defined]


_patch_typing_eval_type_for_pydantic()
