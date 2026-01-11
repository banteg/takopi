from __future__ import annotations

import pytest

from takopi.telegram.api_models import File
from takopi.telegram.types import TelegramIncomingMessage, TelegramVoice
from takopi.telegram.voice import transcribe_voice


class _Bot:
    def __init__(self, *, file_info: File | None, audio: bytes | None) -> None:
        self._file_info = file_info
        self._audio = audio

    async def get_file(self, file_id: str) -> File | None:
        _ = file_id
        return self._file_info

    async def download_file(self, file_path: str) -> bytes | None:
        _ = file_path
        return self._audio


def _voice_message() -> TelegramIncomingMessage:
    voice = TelegramVoice(
        file_id="voice-id",
        mime_type="audio/ogg",
        file_size=123,
        duration=1,
        raw={},
    )
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=1,
        message_id=1,
        text="",
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        voice=voice,
        raw={},
    )


@pytest.mark.anyio
async def test_transcribe_voice_handles_missing_file() -> None:
    replies: list[str] = []

    async def reply(**kwargs) -> None:
        replies.append(kwargs["text"])

    bot = _Bot(file_info=None, audio=None)
    result = await transcribe_voice(
        bot=bot,
        msg=_voice_message(),
        enabled=True,
        reply=reply,
    )

    assert result is None
    assert replies[-1] == "failed to fetch voice file."


@pytest.mark.anyio
async def test_transcribe_voice_handles_missing_download() -> None:
    replies: list[str] = []

    async def reply(**kwargs) -> None:
        replies.append(kwargs["text"])

    bot = _Bot(file_info=File(file_path="voice.ogg"), audio=None)
    result = await transcribe_voice(
        bot=bot,
        msg=_voice_message(),
        enabled=True,
        reply=reply,
    )

    assert result is None
    assert replies[-1] == "failed to download voice file."
