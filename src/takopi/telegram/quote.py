from __future__ import annotations

from .types import TelegramIncomingMessage


def format_quote_block(msg: TelegramIncomingMessage) -> str | None:
    if msg.quote_text is None or msg.quote_is_manual is not True:
        return None
    lines = msg.quote_text.splitlines()
    if not lines:
        lines = [""]
    quoted_lines = [f"> {line}" if line else ">" for line in lines]
    return "quoted:\n" + "\n".join(quoted_lines)


def apply_quote_to_prompt(msg: TelegramIncomingMessage, prompt_text: str) -> str:
    quote_block = format_quote_block(msg)
    if quote_block is None:
        return prompt_text
    if prompt_text and prompt_text.strip():
        return f"{quote_block}\n\n{prompt_text}"
    return f"{quote_block}\nmessage: (empty)"
