import pytest

from takopi import transports
from takopi.config import ConfigError


def test_transport_registry_lists_telegram() -> None:
    ids = transports.list_transports()
    assert "telegram" in ids


def test_transport_registry_gets_telegram() -> None:
    backend = transports.get_transport("telegram")
    assert backend.id == "telegram"


def test_transport_registry_unknown() -> None:
    with pytest.raises(ConfigError, match="Unknown transport"):
        transports.get_transport("nope")
