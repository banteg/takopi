from __future__ import annotations

import io
from collections.abc import Awaitable, Callable

from ..logging import get_logger
from ..settings import TelegramTranscriptionSettings
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
    max_bytes: int | None = None,
    transcription: TelegramTranscriptionSettings,
    reply: Callable[..., Awaitable[None]],
) -> str | None:
    voice = msg.voice
    if voice is None:
        return msg.text
    if not enabled:
        await reply(text=VOICE_TRANSCRIPTION_DISABLED_HINT)
        return None
    if (
        max_bytes is not None
        and voice.file_size is not None
        and voice.file_size > max_bytes
    ):
        await reply(text="voice message is too large to transcribe.")
        return None
    file_info = await bot.get_file(voice.file_id)
    if file_info is None:
        await reply(text="failed to fetch voice file.")
        return None
    audio_bytes = await bot.download_file(file_info.file_path)
    if audio_bytes is None:
        await reply(text="failed to download voice file.")
        return None
    if max_bytes is not None and len(audio_bytes) > max_bytes:
        await reply(text="voice message is too large to transcribe.")
        return None
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"
    model = transcription.model or OPENAI_TRANSCRIPTION_MODEL
    client_kwargs: dict[str, object] = {"timeout": 120}
    if transcription.base_url is not None:
        client_kwargs["base_url"] = transcription.base_url
    if transcription.api_key is not None:
        client_kwargs["api_key"] = transcription.api_key
    async with AsyncOpenAI(**client_kwargs) as client:
        try:
            response = await client.audio.transcriptions.create(
                model=model,
                file=audio_file,
            )
        except OpenAIError as exc:
            logger.error(
                "openai.transcribe.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            await reply(text=str(exc).strip() or "voice transcription failed")
            return None

    return response.text
