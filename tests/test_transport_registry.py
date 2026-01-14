import pytest

from takopi import plugins, transports
from takopi.config import ConfigError
from tests.plugin_fixtures import FakeEntryPoint, install_entrypoints


class DummyTransport:
    id = "telegram"
    description = "Telegram"

    def check_setup(self, *args, **kwargs):
        raise NotImplementedError

    async def interactive_setup(self, *, force: bool) -> bool:
        raise NotImplementedError

    def lock_token(self, *, transport_config: object, _config_path):
        _ = transport_config, _config_path
        raise NotImplementedError

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path,
        runtime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        _ = (
            transport_config,
            config_path,
            runtime,
            final_notify,
            default_engine_override,
        )
        raise NotImplementedError


@pytest.fixture
def transport_entrypoints(monkeypatch):
    entrypoints = [
        FakeEntryPoint(
            "telegram",
            "takopi.telegram.backend:telegram_backend",
            plugins.TRANSPORT_GROUP,
            loader=DummyTransport,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)
    return entrypoints


def test_transport_registry_lists_telegram(transport_entrypoints) -> None:
    ids = transports.list_transports()
    assert "telegram" in ids


def test_transport_registry_gets_telegram(transport_entrypoints) -> None:
    backend = transports.get_transport("telegram")
    assert backend.id == "telegram"


def test_transport_registry_unknown(transport_entrypoints) -> None:
    with pytest.raises(ConfigError, match="Unknown transport"):
        transports.get_transport("nope")
