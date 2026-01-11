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
VOICE_TRANSCRIPTION_DISABLED_HINT = (
    "voice transcription is disabled. enable it in config:\n"
    "```toml\n"
    "[transports.telegram]\n"
    "voice_transcription = true\n"
    "```"
)


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
        await reply(text=VOICE_TRANSCRIPTION_DISABLED_HINT)
        return None
    file_path = (await bot.get_file(voice.file_id) or {}).get("file_path", "")
    audio_bytes = await bot.download_file(file_path) or b""
    filename = "voice.ogg"
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    async with AsyncOpenAI(timeout=120) as client:
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

    return response.text
