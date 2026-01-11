from __future__ import annotations

import os
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..logging import get_logger
from openai import AsyncOpenAI
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


def resolve_openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")


def normalize_voice_filename(file_path: str | None, mime_type: str | None) -> str:
    name = Path(file_path).name if file_path else ""
    if not name:
        if mime_type == "audio/ogg":
            return "voice.ogg"
        return "voice.dat"
    if name.endswith(".oga"):
        return f"{name[:-4]}.ogg"
    return name


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    api_key: str,
    model: str,
    language: str | None = None,
    prompt: str | None = None,
    timeout_s: float = 120,
    client: AsyncOpenAI | None = None,
) -> str | None:
    close_client = client is None
    if client is None:
        client = AsyncOpenAI(api_key=api_key, timeout=timeout_s)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    request: dict[str, object] = {
        "model": model,
        "file": audio_file,
    }
    if language:
        request["language"] = language
    if prompt:
        request["prompt"] = prompt
    try:
        try:
            response = await client.audio.transcriptions.create(**request)
        except Exception as exc:
            logger.error(
                "openai.transcribe.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None
    finally:
        if close_client:
            await client.close()

    text: str | None
    if isinstance(response, str):
        text = response
    else:
        text = getattr(response, "text", None)
    if not isinstance(text, str):
        logger.error(
            "openai.transcribe.invalid_payload",
            response_type=type(response).__name__,
        )
        return None
    return text


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
    api_key = resolve_openai_api_key()
    if not api_key:
        await reply(text="voice transcription requires OPENAI_API_KEY")
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
    filename = normalize_voice_filename(file_path, voice.mime_type)
    transcript = await transcribe_audio(
        audio_bytes,
        filename=filename,
        api_key=api_key,
        model=OPENAI_TRANSCRIPTION_MODEL,
    )
    if transcript is None:
        await reply(text="voice transcription failed")
        return None
    transcript = transcript.strip()
    if not transcript:
        await reply(text="voice transcription returned empty text")
        return None
    return transcript
