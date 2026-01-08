import pytest

from takopi import commands, plugins
from takopi.config import ConfigError
from tests.plugin_fixtures import FakeEntryPoint, install_entrypoints


class DummyCommand:
    id = "hello"
    description = "Hello command"

    async def handle(self, ctx):
        _ = ctx
        return None


@pytest.fixture
def command_entrypoints(monkeypatch):
    entrypoints = [
        FakeEntryPoint(
            "hello",
            "takopi.commands.hello:BACKEND",
            plugins.COMMAND_GROUP,
            loader=DummyCommand,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)
    return entrypoints


def test_command_registry_lists_ids(command_entrypoints) -> None:
    ids = commands.list_command_ids()
    assert "hello" in ids


def test_command_registry_gets_command(command_entrypoints) -> None:
    backend = commands.get_command("hello")
    assert backend.id == "hello"


def test_command_registry_unknown(command_entrypoints) -> None:
    with pytest.raises(ConfigError, match="Unknown command"):
        commands.get_command("nope")


def test_command_registry_optional_missing(command_entrypoints) -> None:
    assert commands.get_command("nope", required=False) is None
