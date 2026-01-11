from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..logging import get_logger
from openai import AsyncOpenAI, OpenAIError
from .client import BotClient
from .types import TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = [
    "TelegramVoiceTranscriptionConfig",
    "transcribe_voice",
]

OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


@dataclass(frozen=True)
class TelegramVoiceTranscriptionConfig:
    enabled: bool = False


async def transcribe_voice(
    *,
    bot: BotClient,
    msg: TelegramIncomingMessage,
    settings: TelegramVoiceTranscriptionConfig | None,
    reply: Callable[..., Awaitable[None]],
) -> str | None:
    voice = msg.voice
    if voice is None:
        return msg.text
    if settings is None or not settings.enabled:
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
    if voice.file_size is not None and voice.file_size > OPENAI_AUDIO_MAX_BYTES:
        await reply(text="voice message is too large to transcribe")
        return None
    file_info = await bot.get_file(voice.file_id)
    if not isinstance(file_info, dict):
        await reply(text="failed to fetch voice file")
        return None
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        await reply(text="failed to fetch voice file")
        return None
    audio_bytes = await bot.download_file(file_path)
    if not audio_bytes:
        await reply(text="failed to download voice message")
        return None
    if len(audio_bytes) > OPENAI_AUDIO_MAX_BYTES:
        await reply(text="voice message is too large to transcribe")
        return None
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
