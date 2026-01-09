from __future__ import annotations

from typing import Any

import httpx

from ..logging import get_logger

logger = get_logger(__name__)

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


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
