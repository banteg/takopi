from collections.abc import Callable

import pytest

from takopi.telegram.bridge import TelegramBridgeConfig
from takopi.runners.mock import ScriptRunner
from tests.telegram_fakes import _FakeBot, _FakeTransport, _make_cfg


@pytest.fixture
def fake_transport() -> _FakeTransport:
    return _FakeTransport()


@pytest.fixture
def fake_bot() -> _FakeBot:
    return _FakeBot()


@pytest.fixture
def make_cfg() -> Callable[..., TelegramBridgeConfig]:
    def _factory(
        transport: _FakeTransport, runner: ScriptRunner | None = None
    ) -> TelegramBridgeConfig:
        return _make_cfg(transport, runner)

    return _factory
