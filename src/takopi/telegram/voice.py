from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from ..logging import get_logger
from openai import AsyncOpenAI, OpenAIError
from .client import BotClient
from .types import TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = ["transcribe_voice"]

OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


async def transcribe_voice(
    *,
    bot: BotClient,
    msg: TelegramIncomingMessage,
    enabled: bool,
    reply: Callable[..., Awaitable[None]],
) -> str | None:
    voice = msg.voice
    if voice is None:
        return msg.text
    if not enabled:
        await reply(
            text=(
                "voice transcription is disabled. enable it in config:\n"
                "```toml\n"
                "[transports.telegram]\n"
                "voice_transcription = true\n"
                "```"
            )
        )
        return None
    file_info = await bot.get_file(voice.file_id) or {}
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str):
        file_path = ""
    audio_bytes = await bot.download_file(file_path) or b""
    filename = "voice.ogg"
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    client = AsyncOpenAI(timeout=120)
    try:
        try:
            response = await client.audio.transcriptions.create(
                model=OPENAI_TRANSCRIPTION_MODEL,
                file=audio_file,
            )
        except OpenAIError as exc:
            logger.error(
                "openai.transcribe.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
                status_code=getattr(exc, "status_code", None),
            )
            await reply(text=str(exc).strip() or "voice transcription failed")
            return None
    finally:
        await client.close()

    text = response if isinstance(response, str) else getattr(response, "text", None)
    if not isinstance(text, str):
        logger.error(
            "openai.transcribe.invalid_payload",
            response_type=type(response).__name__,
        )
        await reply(text="voice transcription failed")
        return None
    text = text.strip()
    if not text:
        await reply(text="voice transcription returned empty text")
        return None
    return text
