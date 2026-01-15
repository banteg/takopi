"""Tests for the hooks module."""

from __future__ import annotations

import asyncio
import json

import pytest

from takopi import hooks
from takopi.context import RunContext
from takopi.session import (
    HooksManager,
    OnErrorContext,
    PostSessionContext,
    PreSessionContext,
    SessionIdentity,
)
from takopi.settings import HooksSettings
from takopi.telegram.hooks_integration import (
    TelegramHooksManager,
    create_post_session_context,
    create_pre_session_context,
    create_telegram_identity,
)
from tests.plugin_fixtures import FakeEntryPoint, FakeEntryPoints


# -----------------------------------------------------------------------------
# Session Identity Tests
# -----------------------------------------------------------------------------


class TestSessionIdentity:
    def test_create_basic(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        assert identity.transport == "telegram"
        assert identity.user_id == "123"
        assert identity.channel_id == "456"
        assert identity.thread_id == "789"

    def test_create_without_thread(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        assert identity.thread_id is None

    def test_create_without_user(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id=None,
            channel_id="456",
        )
        assert identity.user_id is None


class TestCreateTelegramIdentity:
    def test_full_identity(self) -> None:
        identity = create_telegram_identity(
            sender_id=123,
            chat_id=456,
            thread_id=789,
        )
        assert identity.transport == "telegram"
        assert identity.user_id == "123"
        assert identity.channel_id == "456"
        assert identity.thread_id == "789"

    def test_no_sender(self) -> None:
        identity = create_telegram_identity(
            sender_id=None,
            chat_id=456,
            thread_id=None,
        )
        assert identity.user_id is None
        assert identity.thread_id is None


# -----------------------------------------------------------------------------
# Hook Context Tests (New Session Module)
# -----------------------------------------------------------------------------


class TestPreSessionContext:
    def test_create_basic(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="hello",
            engine="codex",
            project="myproject",
        )
        assert ctx.identity.user_id == "123"
        assert ctx.identity.channel_id == "456"
        assert ctx.identity.thread_id == "789"
        assert ctx.message_text == "hello"
        assert ctx.engine == "codex"
        assert ctx.project == "myproject"
        assert ctx.raw_message == {}

    def test_backwards_compat_properties(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="hello",
            engine="codex",
            project="myproject",
        )
        # Test backwards compatibility properties
        assert ctx.sender_id == 123
        assert ctx.chat_id == 456
        assert ctx.thread_id == 789

    def test_backwards_compat_none_values(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id=None,
            channel_id="456",
            thread_id=None,
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="test",
            engine=None,
            project=None,
        )
        assert ctx.sender_id is None
        assert ctx.thread_id is None

    def test_create_with_raw_message(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        raw = {"from": {"id": 123, "first_name": "Test"}}
        ctx = PreSessionContext(
            identity=identity,
            message_text="test",
            engine=None,
            project=None,
            raw_message=raw,
        )
        assert ctx.raw_message == raw


class TestPreSessionResult:
    def test_allow_default(self) -> None:
        result = hooks.PreSessionResult(allow=True)
        assert result.allow is True
        assert result.reason is None
        assert result.silent is False
        assert result.metadata == {}

    def test_deny_with_reason(self) -> None:
        result = hooks.PreSessionResult(
            allow=False,
            reason="unauthorized user",
            silent=True,
            metadata={"user_id": 123},
        )
        assert result.allow is False
        assert result.reason == "unauthorized user"
        assert result.silent is True
        assert result.metadata == {"user_id": 123}


class TestPostSessionContext:
    def test_create_success(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        ctx = PostSessionContext(
            identity=identity,
            engine="codex",
            project="myproject",
            duration_ms=1500,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
        )
        assert ctx.status == "success"
        assert ctx.error is None
        assert ctx.duration_ms == 1500

    def test_create_error(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        ctx = PostSessionContext(
            identity=identity,
            engine="claude",
            project=None,
            duration_ms=500,
            tokens_in=50,
            tokens_out=0,
            status="error",
            error="Connection failed",
            pre_session_metadata={"request_id": "abc123"},
        )
        assert ctx.status == "error"
        assert ctx.error == "Connection failed"
        assert ctx.pre_session_metadata == {"request_id": "abc123"}

    def test_create_with_message_and_response(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        ctx = PostSessionContext(
            identity=identity,
            engine="codex",
            project="myproject",
            duration_ms=2000,
            tokens_in=150,
            tokens_out=300,
            status="success",
            error=None,
            message_text="What is 2+2?",
            response_text="The answer is 4.",
        )
        assert ctx.message_text == "What is 2+2?"
        assert ctx.response_text == "The answer is 4."


class TestOnErrorContext:
    def test_create_basic(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        ctx = OnErrorContext(
            identity=identity,
            engine="codex",
            project="myproject",
            error_type="ValueError",
            error_message="Something went wrong",
            traceback="Traceback...",
        )
        assert ctx.sender_id == 123
        assert ctx.error_type == "ValueError"
        assert ctx.error_message == "Something went wrong"
        assert ctx.traceback == "Traceback..."
        assert ctx.pre_session_metadata == {}


# -----------------------------------------------------------------------------
# Legacy Hook Context Tests (from hooks.py)
# -----------------------------------------------------------------------------


class TestLegacyPreSessionContext:
    def test_create_basic(self) -> None:
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=789,
            message_text="hello",
            engine="codex",
            project="myproject",
        )
        assert ctx.sender_id == 123
        assert ctx.chat_id == 456
        assert ctx.thread_id == 789
        assert ctx.message_text == "hello"
        assert ctx.engine == "codex"
        assert ctx.project == "myproject"
        assert ctx.raw_message == {}


class TestLegacyPostSessionContext:
    def test_create_success(self) -> None:
        ctx = hooks.PostSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=789,
            engine="codex",
            project="myproject",
            duration_ms=1500,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
        )
        assert ctx.status == "success"
        assert ctx.duration_ms == 1500


# -----------------------------------------------------------------------------
# Hook Configuration Tests
# -----------------------------------------------------------------------------


class TestHooksConfig:
    def test_from_dict_empty(self) -> None:
        cfg = hooks.HooksConfig.from_dict({})
        assert cfg.hooks == []
        assert cfg.pre_session_timeout_ms == 1000
        assert cfg.post_session_timeout_ms == 5000
        assert cfg.on_error_timeout_ms == 5000
        assert cfg.fail_closed is False
        assert cfg.config == {}

    def test_from_dict_full(self) -> None:
        cfg = hooks.HooksConfig.from_dict(
            {
                "hooks": ["auth", "rate_limiter", "logger"],
                "pre_session_timeout_ms": 2000,
                "post_session_timeout_ms": 10000,
                "on_error_timeout_ms": 8000,
                "fail_closed": True,
                "config": {
                    "auth": {"allowed_users": [123, 456]},
                    "rate_limiter": {"max_requests": 10},
                },
            }
        )
        assert cfg.hooks == ["auth", "rate_limiter", "logger"]
        assert cfg.pre_session_timeout_ms == 2000
        assert cfg.post_session_timeout_ms == 10000
        assert cfg.on_error_timeout_ms == 8000
        assert cfg.fail_closed is True
        assert cfg.config["auth"]["allowed_users"] == [123, 456]

    def test_from_dict_single_string(self) -> None:
        cfg = hooks.HooksConfig.from_dict(
            {
                "hooks": "single_hook",
            }
        )
        assert cfg.hooks == ["single_hook"]


# -----------------------------------------------------------------------------
# Hook Parsing Tests
# -----------------------------------------------------------------------------


class TestHookParsing:
    def test_is_shell_command_with_spaces(self) -> None:
        assert hooks._is_shell_command("python script.py arg") is True

    def test_is_shell_command_absolute_path(self) -> None:
        assert hooks._is_shell_command("/usr/bin/python") is True

    def test_is_shell_command_relative_path(self) -> None:
        assert hooks._is_shell_command("./hook.sh") is True

    def test_is_shell_command_plugin_ref(self) -> None:
        assert hooks._is_shell_command("auth") is False

    def test_is_shell_command_simple_plugin(self) -> None:
        assert hooks._is_shell_command("auth_plugin") is False


# -----------------------------------------------------------------------------
# Hook Registry Tests
# -----------------------------------------------------------------------------


def _install_hook_entrypoints(monkeypatch, entrypoints: list[FakeEntryPoint]) -> None:
    """Install fake hook entrypoints."""

    def _entry_points() -> FakeEntryPoints:
        return FakeEntryPoints(entrypoints)

    monkeypatch.setattr(hooks, "entry_points", _entry_points)


class TestHookRegistry:
    def test_load_shell_command(self) -> None:
        registry = hooks.HookRegistry()
        loaded = registry.load_hook("/usr/bin/echo test")
        assert loaded.is_shell is True
        assert loaded.hook_obj is None
        assert loaded.plugin_id is None
        assert loaded.ref == "/usr/bin/echo test"

    def test_load_shell_command_cached(self) -> None:
        registry = hooks.HookRegistry()
        loaded1 = registry.load_hook("./hook.sh")
        loaded2 = registry.load_hook("./hook.sh")
        assert loaded1 is loaded2

    def test_load_entrypoint_hook(self, monkeypatch) -> None:
        class MyHook:
            def pre_session(self, ctx, config):
                return hooks.PreSessionResult(allow=True)

        entrypoints = [
            FakeEntryPoint(
                "auth",
                "some.module:MyHook",
                hooks.HOOK_GROUP,
                loader=MyHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        loaded = registry.load_hook("auth")
        assert loaded.is_shell is False
        assert loaded.hook_obj is not None
        assert loaded.plugin_id == "auth"

    def test_load_entrypoint_hook_not_found(self, monkeypatch) -> None:
        _install_hook_entrypoints(monkeypatch, [])

        registry = hooks.HookRegistry()
        with pytest.raises(LookupError, match="not found"):
            registry.load_hook("nonexistent")

    def test_load_errors_tracked(self, monkeypatch) -> None:
        def bad_loader():
            raise RuntimeError("load failed")

        entrypoints = [
            FakeEntryPoint(
                "broken",
                "some.module:broken",
                hooks.HOOK_GROUP,
                loader=bad_loader,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        with pytest.raises(hooks.PluginLoadFailed):
            registry.load_hook("broken")

        errors = registry.get_load_errors()
        assert len(errors) == 1
        assert "load failed" in errors[0].error


class TestLoadedHook:
    def test_shell_hook_has_all_methods(self) -> None:
        hook = hooks.LoadedHook(
            ref="./hook.sh",
            hook_obj=None,
            is_shell=True,
            plugin_id=None,
        )
        assert hook.has_pre_session() is True
        assert hook.has_post_session() is True
        assert hook.has_on_error() is True

    def test_python_hook_with_pre_session_only(self) -> None:
        class PreSessionOnlyHook:
            def pre_session(self, ctx, config):
                return hooks.PreSessionResult(allow=True)

        hook = hooks.LoadedHook(
            ref="auth",
            hook_obj=PreSessionOnlyHook(),
            is_shell=False,
            plugin_id="auth",
        )
        assert hook.has_pre_session() is True
        assert hook.has_post_session() is False
        assert hook.has_on_error() is False

    def test_python_hook_with_all_methods(self) -> None:
        class FullHook:
            def pre_session(self, ctx, config):
                return hooks.PreSessionResult(allow=True)

            def post_session(self, ctx, config):
                pass

            def on_error(self, ctx, config):
                pass

        hook = hooks.LoadedHook(
            ref="full",
            hook_obj=FullHook(),
            is_shell=False,
            plugin_id="full",
        )
        assert hook.has_pre_session() is True
        assert hook.has_post_session() is True
        assert hook.has_on_error() is True


# -----------------------------------------------------------------------------
# Context Serialization Tests
# -----------------------------------------------------------------------------


class TestContextSerialization:
    def test_pre_session_to_json_legacy(self) -> None:
        """Test legacy context serialization (no identity)."""
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=789,
            message_text="hello world",
            engine="codex",
            project="myproject",
            raw_message={"key": "value"},
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "pre_session"
        assert data["sender_id"] == 123
        assert data["chat_id"] == 456
        assert data["thread_id"] == 789
        assert data["message_text"] == "hello world"
        assert data["engine"] == "codex"
        assert data["project"] == "myproject"
        assert data["raw_message"] == {"key": "value"}
        # No identity for legacy contexts
        assert "identity" not in data

    def test_pre_session_to_json_with_identity(self) -> None:
        """Test new context serialization (with SessionIdentity)."""
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
            thread_id="789",
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="hello world",
            engine="codex",
            project="myproject",
            raw_message={"key": "value"},
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "pre_session"
        # Backwards compat flat fields
        assert data["sender_id"] == 123
        assert data["chat_id"] == 456
        assert data["thread_id"] == 789
        # New identity object
        assert data["identity"]["transport"] == "telegram"
        assert data["identity"]["user_id"] == "123"
        assert data["identity"]["channel_id"] == "456"
        assert data["identity"]["thread_id"] == "789"

    def test_post_session_to_json_legacy(self) -> None:
        ctx = hooks.PostSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="claude",
            project=None,
            duration_ms=1500,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
            pre_session_metadata={"request_id": "abc"},
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "post_session"
        assert data["sender_id"] == 123
        assert data["status"] == "success"
        assert data["duration_ms"] == 1500
        assert data["pre_session_metadata"] == {"request_id": "abc"}

    def test_post_session_to_json_with_identity(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        ctx = PostSessionContext(
            identity=identity,
            engine="codex",
            project="myproject",
            duration_ms=2000,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
            message_text="Hello world",
            response_text="Hello! How can I help?",
            pre_session_metadata={},
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "post_session"
        assert data["message_text"] == "Hello world"
        assert data["response_text"] == "Hello! How can I help?"
        assert data["identity"]["transport"] == "telegram"

    def test_on_error_to_json_legacy(self) -> None:
        ctx = hooks.OnErrorContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project="myproject",
            error_type="ValueError",
            error_message="Something failed",
            traceback="Traceback...",
            pre_session_metadata={"request_id": "abc"},
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "on_error"
        assert data["error_type"] == "ValueError"
        assert data["error_message"] == "Something failed"
        assert data["traceback"] == "Traceback..."

    def test_on_error_to_json_with_identity(self) -> None:
        identity = SessionIdentity(
            transport="telegram",
            user_id="123",
            channel_id="456",
        )
        ctx = OnErrorContext(
            identity=identity,
            engine="codex",
            project="myproject",
            error_type="RuntimeError",
            error_message="Something went wrong",
            traceback="Traceback...",
        )
        json_str = hooks._context_to_json(ctx)
        data = json.loads(json_str)
        assert data["type"] == "on_error"
        assert data["identity"]["transport"] == "telegram"


class TestPreSessionResultParsing:
    def test_parse_allow(self) -> None:
        result = hooks._parse_pre_session_result('{"allow": true}')
        assert result.allow is True

    def test_parse_deny_with_reason(self) -> None:
        result = hooks._parse_pre_session_result(
            '{"allow": false, "reason": "not authorized", "silent": true}'
        )
        assert result.allow is False
        assert result.reason == "not authorized"
        assert result.silent is True

    def test_parse_with_metadata(self) -> None:
        result = hooks._parse_pre_session_result(
            '{"allow": true, "metadata": {"key": "value"}}'
        )
        assert result.allow is True
        assert result.metadata == {"key": "value"}

    def test_parse_invalid_json(self) -> None:
        result = hooks._parse_pre_session_result("not json")
        assert result.allow is True  # Default to allow on error

    def test_parse_missing_allow(self) -> None:
        result = hooks._parse_pre_session_result("{}")
        assert result.allow is True  # Default


# -----------------------------------------------------------------------------
# Shell Hook Execution Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
class TestShellHookExecution:
    async def test_run_shell_hook_success(self) -> None:
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        # Use echo to return JSON
        output = await hooks._run_shell_hook(
            "echo '{\"allow\": true}'",
            ctx,
            timeout_ms=5000,
        )
        assert output is not None
        data = json.loads(output.strip())
        assert data["allow"] is True

    async def test_run_shell_hook_timeout(self) -> None:
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        output = await hooks._run_shell_hook(
            "sleep 10",
            ctx,
            timeout_ms=100,
        )
        assert output is None

    async def test_run_shell_hook_failure(self) -> None:
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        output = await hooks._run_shell_hook(
            "exit 1",
            ctx,
            timeout_ms=5000,
        )
        assert output is None

    async def test_run_shell_hook_receives_input(self) -> None:
        ctx = hooks.PreSessionContext(
            sender_id=999,
            chat_id=456,
            thread_id=None,
            message_text="test message",
            engine="codex",
            project=None,
        )
        # Use cat to echo back the input
        output = await hooks._run_shell_hook(
            "cat",
            ctx,
            timeout_ms=5000,
        )
        assert output is not None
        data = json.loads(output)
        assert data["sender_id"] == 999
        assert data["message_text"] == "test message"

    async def test_run_shell_hook_with_session_context(self) -> None:
        """Test shell hook with new session context (with identity)."""
        identity = SessionIdentity(
            transport="telegram",
            user_id="999",
            channel_id="456",
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="test message",
            engine="codex",
            project=None,
        )
        output = await hooks._run_shell_hook(
            "cat",
            ctx,
            timeout_ms=5000,
        )
        assert output is not None
        data = json.loads(output)
        # Should have both flat fields and identity
        assert data["sender_id"] == 999
        assert data["identity"]["transport"] == "telegram"
        assert data["identity"]["user_id"] == "999"


# -----------------------------------------------------------------------------
# Pre-Session Hook Execution Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
class TestPreSessionHookExecution:
    async def test_run_pre_session_hooks_empty(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({})
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is True

    async def test_run_pre_session_hooks_shell_allow(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict(
            {
                "hooks": ["echo '{\"allow\": true}'"],
            }
        )
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is True

    async def test_run_pre_session_hooks_shell_deny(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict(
            {
                "hooks": ['echo \'{"allow": false, "reason": "denied"}\''],
            }
        )
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is False
        assert result.reason == "denied"

    async def test_run_pre_session_hooks_first_rejection_wins(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict(
            {
                "hooks": [
                    'echo \'{"allow": false, "reason": "first"}\'',
                    'echo \'{"allow": false, "reason": "second"}\'',
                ],
            }
        )
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is False
        assert result.reason == "first"

    async def test_run_pre_session_hooks_metadata_merged(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict(
            {
                "hooks": [
                    'echo \'{"allow": true, "metadata": {"a": 1}}\'',
                    'echo \'{"allow": true, "metadata": {"b": 2}}\'',
                ],
            }
        )
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is True
        assert result.metadata == {"a": 1, "b": 2}

    async def test_run_pre_session_python_hook(self, monkeypatch) -> None:
        class AuthHook:
            def pre_session(self, ctx, config):
                if ctx.sender_id == 999:
                    return hooks.PreSessionResult(allow=False, reason="blocked")
                return hooks.PreSessionResult(allow=True)

        entrypoints = [
            FakeEntryPoint(
                "auth",
                "some.module:AuthHook",
                hooks.HOOK_GROUP,
                loader=AuthHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({"hooks": ["auth"]})

        # Allowed user
        ctx = hooks.PreSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is True

        # Blocked user
        ctx_blocked = hooks.PreSessionContext(
            sender_id=999,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result_blocked = await hooks.run_pre_session_hooks(
            registry, config, ctx_blocked
        )
        assert result_blocked.allow is False
        assert result_blocked.reason == "blocked"

    async def test_run_pre_session_async_python_hook(self, monkeypatch) -> None:
        class AsyncAuthHook:
            async def pre_session(self, ctx, config):
                await asyncio.sleep(0.01)
                if ctx.sender_id == 999:
                    return hooks.PreSessionResult(allow=False, reason="async blocked")
                return hooks.PreSessionResult(allow=True)

        entrypoints = [
            FakeEntryPoint(
                "async_auth",
                "some.module:AsyncAuthHook",
                hooks.HOOK_GROUP,
                loader=AsyncAuthHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({"hooks": ["async_auth"]})

        ctx = hooks.PreSessionContext(
            sender_id=999,
            chat_id=456,
            thread_id=None,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is False
        assert result.reason == "async blocked"

    async def test_run_pre_session_with_session_context(self, monkeypatch) -> None:
        """Test Python hook works with new session context."""

        class AuthHook:
            def pre_session(self, ctx, config):
                # Should work with both legacy and session contexts via property
                if ctx.sender_id == 999:
                    return hooks.PreSessionResult(allow=False, reason="blocked")
                return hooks.PreSessionResult(allow=True)

        entrypoints = [
            FakeEntryPoint(
                "auth",
                "some.module:AuthHook",
                hooks.HOOK_GROUP,
                loader=AuthHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({"hooks": ["auth"]})

        # Use new session context
        identity = SessionIdentity(
            transport="telegram",
            user_id="999",
            channel_id="456",
        )
        ctx = PreSessionContext(
            identity=identity,
            message_text="test",
            engine="codex",
            project=None,
        )
        result = await hooks.run_pre_session_hooks(registry, config, ctx)
        assert result.allow is False
        assert result.reason == "blocked"


# -----------------------------------------------------------------------------
# Post-Session Hook Execution Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
class TestPostSessionHookExecution:
    async def test_run_post_session_hooks_empty(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({})
        ctx = hooks.PostSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project=None,
            duration_ms=1000,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
        )
        # Should complete without error
        await hooks.run_post_session_hooks(registry, config, ctx)

    async def test_run_post_session_hooks_shell(self) -> None:
        registry = hooks.HookRegistry()
        # Use true command which just exits 0
        config = hooks.HooksConfig.from_dict(
            {
                "hooks": ["true"],
            }
        )
        ctx = hooks.PostSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project=None,
            duration_ms=1000,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
        )
        # Should complete without error
        await hooks.run_post_session_hooks(registry, config, ctx)

    async def test_run_post_session_python_hook(self, monkeypatch) -> None:
        calls = []

        class LoggerHook:
            def post_session(self, ctx, config):
                calls.append(ctx.status)

        entrypoints = [
            FakeEntryPoint(
                "logger",
                "some.module:LoggerHook",
                hooks.HOOK_GROUP,
                loader=LoggerHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({"hooks": ["logger"]})

        ctx = hooks.PostSessionContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project=None,
            duration_ms=1000,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
        )
        await hooks.run_post_session_hooks(registry, config, ctx)
        assert calls == ["success"]


# -----------------------------------------------------------------------------
# On-Error Hook Execution Tests
# -----------------------------------------------------------------------------


@pytest.mark.anyio
class TestOnErrorHookExecution:
    async def test_run_on_error_hooks_empty(self) -> None:
        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({})
        ctx = hooks.OnErrorContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project=None,
            error_type="ValueError",
            error_message="test error",
        )
        # Should complete without error
        await hooks.run_on_error_hooks(registry, config, ctx)

    async def test_run_on_error_python_hook(self, monkeypatch) -> None:
        calls = []

        class ErrorHook:
            def on_error(self, ctx, config):
                calls.append((ctx.error_type, ctx.error_message))

        entrypoints = [
            FakeEntryPoint(
                "error_handler",
                "some.module:ErrorHook",
                hooks.HOOK_GROUP,
                loader=ErrorHook,
            ),
        ]
        _install_hook_entrypoints(monkeypatch, entrypoints)

        registry = hooks.HookRegistry()
        config = hooks.HooksConfig.from_dict({"hooks": ["error_handler"]})

        ctx = hooks.OnErrorContext(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            engine="codex",
            project=None,
            error_type="RuntimeError",
            error_message="Something failed",
        )
        await hooks.run_on_error_hooks(registry, config, ctx)
        assert calls == [("RuntimeError", "Something failed")]


# -----------------------------------------------------------------------------
# Hooks Manager Tests (Transport-agnostic)
# -----------------------------------------------------------------------------


class TestHooksManager:
    def test_no_hooks_configured(self) -> None:
        manager = HooksManager(None)
        assert manager.has_hooks is False

    def test_with_empty_settings(self) -> None:
        settings = HooksSettings()
        manager = HooksManager(settings)
        assert manager.has_hooks is False

    def test_with_hooks_configured(self) -> None:
        settings = HooksSettings(hooks=["auth", "logger"])
        manager = HooksManager(settings)
        assert manager.has_hooks is True


# -----------------------------------------------------------------------------
# Telegram Hooks Integration Tests
# -----------------------------------------------------------------------------


class TestTelegramHooksManager:
    def test_no_hooks_configured(self) -> None:
        settings = HooksSettings()
        manager = TelegramHooksManager(settings)
        assert manager.has_hooks is False

    def test_with_hooks_configured(self) -> None:
        settings = HooksSettings(hooks=["auth", "logger"])
        manager = TelegramHooksManager(settings)
        assert manager.has_hooks is True

    def test_with_single_hook_string(self) -> None:
        settings = HooksSettings(hooks="auth")
        manager = TelegramHooksManager(settings)
        assert manager.has_hooks is True


class TestContextHelpers:
    def test_create_pre_session_context(self) -> None:
        run_context = RunContext(project="myproject", branch="main")
        ctx = create_pre_session_context(
            sender_id=123,
            chat_id=456,
            thread_id=789,
            message_text="hello",
            engine="codex",
            context=run_context,
            raw_message={"key": "value"},
        )
        # Uses identity now
        assert ctx.identity.transport == "telegram"
        assert ctx.identity.user_id == "123"
        assert ctx.identity.channel_id == "456"
        assert ctx.identity.thread_id == "789"
        # Backwards compat properties
        assert ctx.sender_id == 123
        assert ctx.chat_id == 456
        assert ctx.thread_id == 789
        assert ctx.message_text == "hello"
        assert ctx.engine == "codex"
        assert ctx.project == "myproject"
        assert ctx.raw_message == {"key": "value"}

    def test_create_pre_session_context_no_context(self) -> None:
        ctx = create_pre_session_context(
            sender_id=123,
            chat_id=456,
            thread_id=None,
            message_text="hello",
            engine="codex",
            context=None,
        )
        assert ctx.project is None

    def test_create_post_session_context(self) -> None:
        run_context = RunContext(project="myproject", branch="main")
        ctx = create_post_session_context(
            sender_id=123,
            chat_id=456,
            thread_id=789,
            engine="codex",
            context=run_context,
            duration_ms=1500,
            tokens_in=100,
            tokens_out=200,
            status="success",
            error=None,
            pre_session_metadata={"request_id": "abc"},
        )
        assert ctx.identity.transport == "telegram"
        assert ctx.sender_id == 123
        assert ctx.duration_ms == 1500
        assert ctx.status == "success"
        assert ctx.project == "myproject"
        assert ctx.pre_session_metadata == {"request_id": "abc"}


# -----------------------------------------------------------------------------
# Settings Tests
# -----------------------------------------------------------------------------


class TestHooksSettings:
    def test_default_values(self) -> None:
        settings = HooksSettings()
        assert settings.hooks == []
        assert settings.pre_session_timeout_ms == 1000
        assert settings.post_session_timeout_ms == 5000
        assert settings.on_error_timeout_ms == 5000
        assert settings.fail_closed is False

    def test_string_coerced_to_list(self) -> None:
        settings = HooksSettings(hooks="single_hook")  # type: ignore
        assert settings.hooks == ["single_hook"]

    def test_multiple_hooks(self) -> None:
        settings = HooksSettings(
            hooks=["auth", "rate_limiter", "logger"],
        )
        assert settings.hooks == ["auth", "rate_limiter", "logger"]

    def test_get_hook_config(self) -> None:
        # Test hook config retrieval from model_extra
        settings = HooksSettings.model_validate(
            {
                "hooks": ["auth"],
                "config": {
                    "auth": {"allowed_users": [123, 456]},
                },
            }
        )
        assert settings.get_hook_config("auth") == {"allowed_users": [123, 456]}
        assert settings.get_hook_config("nonexistent") == {}
