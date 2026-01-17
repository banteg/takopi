from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..logging import get_logger
from openai import AsyncOpenAI, OpenAIError

from .client import BotClient
from .types import TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = ["transcribe_voice"]

VOICE_TRANSCRIPTION_DISABLED_HINT = (
    "voice transcription is disabled. enable it in config:\n"
    "```toml\n"
    "[transports.telegram]\n"
    "voice_transcription = true\n"
    "```"
)


class VoiceTranscriber(Protocol):
    async def transcribe(self, *, model: str, audio_bytes: bytes) -> str: ...


class OpenAIVoiceTranscriber:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key

    async def transcribe(self, *, model: str, audio_bytes: bytes) -> str:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "voice.ogg"
        async with AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=120,
        ) as client:
            response = await client.audio.transcriptions.create(
                model=model,
                file=audio_file,
            )
        return response.text


async def transcribe_voice(
    *,
    bot: BotClient,
    msg: TelegramIncomingMessage,
    enabled: bool,
    model: str,
    max_bytes: int | None = None,
    reply: Callable[..., Awaitable[None]],
    transcriber: VoiceTranscriber | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
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
    if transcriber is None:
        transcriber = OpenAIVoiceTranscriber(base_url=base_url, api_key=api_key)
    try:
        return await transcriber.transcribe(model=model, audio_bytes=audio_bytes)
    except OpenAIError as exc:
        logger.error(
            "openai.transcribe.error",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await reply(text=str(exc).strip() or "voice transcription failed")
        return None
    except (RuntimeError, OSError, ValueError) as exc:
        logger.error(
            "voice.transcribe.error",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await reply(text=str(exc).strip() or "voice transcription failed")
        return None
