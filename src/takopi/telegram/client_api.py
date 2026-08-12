from __future__ import annotations

import time
from typing import Any, Protocol, TypeVar

import httpx
import msgspec

from ..logging import get_logger
from .api_models import Chat, ChatMember, File, ForumTopic, Message, Update, User

logger = get_logger(__name__)

T = TypeVar("T")
_NETWORK_RETRY_AFTER_S = 2.0

# ── Startup getUpdates conflict (LOCAL PATCH) ────────────────────────────────
#
# Restarting the bridge races Telegram: the previous process's long-poll stays
# open server-side for the remainder of its timeout (up to 50 s) even after that
# process has exited and released the config lock. The new instance polls, sees
# two consumers and gets
#
#   409 Conflict: terminated by other getUpdates request; make sure that only
#   one bot instance is running
#
# which is self-limiting — it clears as soon as the old connection drains.
# Untreated it reached the generic error path: logged at ERROR as
# telegram.http_error and returned None, which made _drain_backlog give up
# ("startup.backlog.failed") so pre-restart messages were replayed as new
# instead of discarded.
#
# Inside the startup window it now raises TelegramRetryAfter, which
# BotClient._call_with_retry_after already retries transparently — no update is
# lost, because the offset is never advanced past an update that was not
# received, and Telegram holds undelivered updates for 24 h.
#
# The window is deliberately bounded: past it, a persistent 409 means a SECOND
# bridge really is running on this token, and that must stay a visible error
# rather than an infinite silent retry.
_STARTUP_CONFLICT_WINDOW_S = 60.0
_STARTUP_CONFLICT_BASE_BACKOFF_S = 1.0
_STARTUP_CONFLICT_MAX_BACKOFF_S = 8.0
_STARTUP_CONFLICT_MARKER = "terminated by other getupdates"

_process_start = time.monotonic()
_startup_conflict_attempts = 0


def _startup_conflict_backoff(description: str | None) -> float | None:
    """Backoff for a getUpdates conflict during startup, else None.

    None means "not this case" — the caller must fall through to its normal
    error handling.
    """
    global _startup_conflict_attempts
    if not description or _STARTUP_CONFLICT_MARKER not in description.lower():
        return None
    if time.monotonic() - _process_start > _STARTUP_CONFLICT_WINDOW_S:
        return None
    backoff = min(
        _STARTUP_CONFLICT_BASE_BACKOFF_S * (2**_startup_conflict_attempts),
        _STARTUP_CONFLICT_MAX_BACKOFF_S,
    )
    _startup_conflict_attempts += 1
    return backoff


def _clear_startup_conflicts() -> None:
    """Reset the backoff ladder once a poll succeeds."""
    global _startup_conflict_attempts
    _startup_conflict_attempts = 0


class RetryAfter(Exception):
    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


class TelegramRetryAfter(RetryAfter):
    pass


def retry_after_from_payload(payload: dict[str, Any]) -> float | None:
    params = payload.get("parameters")
    if isinstance(params, dict):
        retry_after = params.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
    return None


class BotClient(Protocol):
    async def close(self) -> None: ...

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None: ...

    async def get_file(self, file_id: str) -> File | None: ...

    async def download_file(self, file_path: str) -> bytes | None: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        message_thread_id: int | None = None,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> Message | None: ...

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> Message | None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        wait: bool = True,
    ) -> Message | None: ...

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(self) -> User | None: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool: ...

    async def get_chat(self, chat_id: int) -> Chat | None: ...

    async def get_chat_member(
        self, chat_id: int, user_id: int
    ) -> ChatMember | None: ...

    async def create_forum_topic(
        self,
        chat_id: int,
        name: str,
    ) -> ForumTopic | None: ...

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str,
    ) -> bool: ...


class HttpBotClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_s: float = 120,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ValueError("Telegram token is empty")
        self._base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_http_client = http_client is None

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    def _parse_telegram_envelope(
        self,
        *,
        method: str,
        resp: httpx.Response,
        payload: Any,
    ) -> Any | None:
        if not isinstance(payload, dict):
            logger.error(
                "telegram.invalid_payload",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        if not payload.get("ok"):
            if payload.get("error_code") == 429:
                retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
                    "telegram.rate_limited",
                    method=method,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after)
            # LOCAL PATCH: expected, self-clearing restart race — retry quietly
            # instead of reporting it as a failure. See the note at the top.
            if payload.get("error_code") == 409:
                description = payload.get("description")
                backoff = _startup_conflict_backoff(
                    description if isinstance(description, str) else None
                )
                if backoff is not None:
                    logger.warning(
                        "telegram.startup_conflict",
                        method=method,
                        url=str(resp.request.url),
                        retry_after=backoff,
                        description=description,
                    )
                    raise TelegramRetryAfter(
                        backoff, description if isinstance(description, str) else None
                    )
            logger.error(
                "telegram.api_error",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        _clear_startup_conflicts()
        logger.debug("telegram.response", method=method, payload=payload)
        return payload.get("result")

    async def _request(
        self,
        method: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any | None:
        request_payload = json if json is not None else data
        logger.debug("telegram.request", method=method, payload=request_payload)
        try:
            if json is not None:
                resp = await self._http_client.post(f"{self._base}/{method}", json=json)
            else:
                resp = await self._http_client.post(
                    f"{self._base}/{method}", data=data, files=files
                )
        except httpx.HTTPError as exc:
            url = getattr(exc.request, "url", None)
            error = str(exc) or repr(exc)
            logger.error(
                "telegram.network_error",
                method=method,
                url=str(url) if url is not None else None,
                error=error,
                error_type=exc.__class__.__name__,
            )
            raise TelegramRetryAfter(_NETWORK_RETRY_AFTER_S, error) from exc

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if resp.status_code == 429:
                retry_after: float | None = None
                try:
                    response_payload = resp.json()
                except Exception:  # noqa: BLE001
                    response_payload = None
                if isinstance(response_payload, dict):
                    retry_after = retry_after_from_payload(response_payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
                    "telegram.rate_limited",
                    method=method,
                    status=resp.status_code,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after) from exc
            body = resp.text
            # LOCAL PATCH: Telegram answers the restart race with HTTP 409, so the
            # conflict arrives here rather than through the ok:false envelope.
            if resp.status_code == 409:
                backoff = _startup_conflict_backoff(body)
                if backoff is not None:
                    logger.warning(
                        "telegram.startup_conflict",
                        method=method,
                        status=resp.status_code,
                        url=str(resp.request.url),
                        retry_after=backoff,
                    )
                    raise TelegramRetryAfter(backoff, body) from exc
            logger.error(
                "telegram.http_error",
                method=method,
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                body=body,
            )
            return None

        try:
            response_payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            body = resp.text
            logger.error(
                "telegram.bad_response",
                method=method,
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                error_type=exc.__class__.__name__,
                body=body,
            )
            return None

        return self._parse_telegram_envelope(
            method=method,
            resp=resp,
            payload=response_payload,
        )

    def _decode_result(
        self,
        *,
        method: str,
        payload: Any,
        model: type[T],
    ) -> T | None:
        if payload is None:
            return None
        try:
            return msgspec.convert(payload, type=model)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "telegram.decode_error",
                method=method,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def _post(self, method: str, json_data: dict[str, Any]) -> Any | None:
        return await self._request(method, json=json_data)

    async def _post_form(
        self,
        method: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> Any | None:
        return await self._request(method, data=data, files=files)

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None:
        params: dict[str, Any] = {"timeout": timeout_s}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates
        result = await self._post("getUpdates", params)
        if result is None or not isinstance(result, list):
            return None
        try:
            return msgspec.convert(result, type=list[Update])
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "telegram.decode_error",
                method="getUpdates",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def get_file(self, file_id: str) -> File | None:
        result = await self._post("getFile", {"file_id": file_id})
        return self._decode_result(method="getFile", payload=result, model=File)

    async def download_file(self, file_path: str) -> bytes | None:
        url = f"{self._file_base}/{file_path}"
        try:
            resp = await self._http_client.get(url)
        except httpx.HTTPError as exc:
            request_url = getattr(exc.request, "url", None)
            error = str(exc) or repr(exc)
            logger.error(
                "telegram.file_network_error",
                url=str(request_url) if request_url is not None else None,
                error=error,
                error_type=exc.__class__.__name__,
            )
            raise TelegramRetryAfter(_NETWORK_RETRY_AFTER_S, error) from exc
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if resp.status_code == 429:
                retry_after: float | None = None
                try:
                    response_payload = resp.json()
                except Exception:  # noqa: BLE001
                    response_payload = None
                if isinstance(response_payload, dict):
                    retry_after = retry_after_from_payload(response_payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
                    "telegram.rate_limited",
                    method="download_file",
                    status=resp.status_code,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after) from exc

            logger.error(
                "telegram.file_http_error",
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(exc),
                body=resp.text,
            )
            return None
        return resp.content

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        message_thread_id: int | None = None,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> Message | None:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if disable_notification is not None:
            params["disable_notification"] = disable_notification
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        if entities is not None:
            params["entities"] = entities
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        params["link_preview_options"] = {"is_disabled": True}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        result = await self._post("sendMessage", params)
        return self._decode_result(method="sendMessage", payload=result, model=Message)

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> Message | None:
        params: dict[str, Any] = {"chat_id": chat_id}
        if disable_notification is not None:
            params["disable_notification"] = disable_notification
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        if caption is not None:
            params["caption"] = caption
        result = await self._post_form(
            "sendDocument",
            params,
            files={"document": (filename, content)},
        )
        return self._decode_result(method="sendDocument", payload=result, model=Message)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        wait: bool = True,
    ) -> Message | None:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if entities is not None:
            params["entities"] = entities
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        params["link_preview_options"] = {"is_disabled": True}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        result = await self._post("editMessageText", params)
        return self._decode_result(
            method="editMessageText",
            payload=result,
            model=Message,
        )

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        result = await self._post(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
        return bool(result)

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        params: dict[str, Any] = {"commands": commands}
        if scope is not None:
            params["scope"] = scope
        if language_code is not None:
            params["language_code"] = language_code
        result = await self._post("setMyCommands", params)
        return bool(result)

    async def get_me(self) -> User | None:
        result = await self._post("getMe", {})
        return self._decode_result(method="getMe", payload=result, model=User)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            params["text"] = text
        if show_alert is not None:
            params["show_alert"] = show_alert
        result = await self._post("answerCallbackQuery", params)
        return bool(result)

    async def get_chat(self, chat_id: int) -> Chat | None:
        result = await self._post("getChat", {"chat_id": chat_id})
        return self._decode_result(method="getChat", payload=result, model=Chat)

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMember | None:
        result = await self._post(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )
        return self._decode_result(
            method="getChatMember",
            payload=result,
            model=ChatMember,
        )

    async def create_forum_topic(self, chat_id: int, name: str) -> ForumTopic | None:
        result = await self._post(
            "createForumTopic", {"chat_id": chat_id, "name": name}
        )
        return self._decode_result(
            method="createForumTopic",
            payload=result,
            model=ForumTopic,
        )

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str,
    ) -> bool:
        result = await self._post(
            "editForumTopic",
            {
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "name": name,
            },
        )
        return bool(result)
