from __future__ import annotations

import os
from typing import Any
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..logging import get_logger
from .client import BotClient
from .types import TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = [
    "TelegramVoiceTranscriptionConfig",
    "transcribe_voice",
]

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
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


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    api_key: str,
    model: str,
    language: str | None = None,
    prompt: str | None = None,
    chunking_strategy: str | None = "auto",
    mime_type: str | None = None,
    timeout_s: float = 120,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    data: dict[str, Any] = {"model": model}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    if chunking_strategy:
        data["chunking_strategy"] = chunking_strategy

    files = {
        "file": (
            filename,
            audio_bytes,
            mime_type or "application/octet-stream",
        )
    }

    headers = {"Authorization": f"Bearer {api_key}"}
    close_client = False
    client = http_client
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
        close_client = True
    try:
        try:
            resp = await client.post(
                OPENAI_TRANSCRIBE_URL,
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            request_url = getattr(exc.request, "url", None)
            logger.error(
                "openai.transcribe.network_error",
                url=str(request_url) if request_url is not None else None,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "openai.transcribe.http_error",
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                body=resp.text,
            )
            return None
        try:
            payload = resp.json()
        except Exception as exc:
            logger.error(
                "openai.transcribe.bad_response",
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                error_type=exc.__class__.__name__,
                body=resp.text,
            )
            return None
    finally:
        if close_client:
            await client.aclose()

    text = payload.get("text")
    if not isinstance(text, str):
        logger.error(
            "openai.transcribe.invalid_payload",
            payload=payload,
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
        chunking_strategy=OPENAI_TRANSCRIPTION_CHUNKING,
        mime_type=voice.mime_type,
    )
    if transcript is None:
        await reply(text="voice transcription failed")
        return None
    transcript = transcript.strip()
    if not transcript:
        await reply(text="voice transcription returned empty text")
        return None
    return transcript
