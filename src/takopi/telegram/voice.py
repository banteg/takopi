from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .client import BotClient
from .transcribe import transcribe_audio
from .types import TelegramIncomingMessage

__all__ = [
    "TelegramVoiceTranscriptionConfig",
    "transcribe_voice",
]

OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
OPENAI_TRANSCRIPTION_CHUNKING = "auto"


@dataclass(frozen=True)
class TelegramVoiceTranscriptionConfig:
    enabled: bool = False


def resolve_openai_api_key() -> str | None:
    env_key = os.environ.get("OPENAI_API_KEY")
    if isinstance(env_key, str):
        env_key = env_key.strip()
        if env_key:
            return env_key
    return None


def normalize_voice_filename(file_path: str | None, mime_type: str | None) -> str:
    name = Path(file_path).name if file_path else ""
    if not name:
        if mime_type == "audio/ogg":
            return "voice.ogg"
        return "voice.dat"
    if name.endswith(".oga"):
        return f"{name[:-4]}.ogg"
    return name


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
        await reply(text="voice transcription is disabled.")
        return None
    api_key = resolve_openai_api_key()
    if not api_key:
        await reply(text="voice transcription requires OPENAI_API_KEY.")
        return None
    if voice.file_size is not None and voice.file_size > OPENAI_AUDIO_MAX_BYTES:
        await reply(text="voice message is too large to transcribe.")
        return None
    file_info = await bot.get_file(voice.file_id)
    if not isinstance(file_info, dict):
        await reply(text="failed to fetch voice file.")
        return None
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        await reply(text="failed to fetch voice file.")
        return None
    audio_bytes = await bot.download_file(file_path)
    if not audio_bytes:
        await reply(text="failed to download voice message.")
        return None
    if len(audio_bytes) > OPENAI_AUDIO_MAX_BYTES:
        await reply(text="voice message is too large to transcribe.")
        return None
    filename = normalize_voice_filename(file_path, voice.mime_type)
    transcript = await transcribe_audio(
        audio_bytes,
        filename=filename,
        api_key=api_key,
        model=OPENAI_TRANSCRIPTION_MODEL,
        chunking_strategy=OPENAI_TRANSCRIPTION_CHUNKING,
        mime_type=voice.mime_type,
    )
    if transcript is None:
        await reply(text="voice transcription failed.")
        return None
    transcript = transcript.strip()
    if not transcript:
        await reply(text="voice transcription returned empty text.")
        return None
    return transcript
