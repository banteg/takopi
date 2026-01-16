from collections.abc import Iterator

import pytest

from takopi import plugins
from tests.plugin_fixtures import FakeEntryPoint, install_entrypoints


@pytest.fixture(autouse=True)
def _reset_plugin_state() -> Iterator[None]:
    plugins.reset_plugin_state()
    yield
    plugins.reset_plugin_state()


def test_list_ids_does_not_load_entrypoints(monkeypatch) -> None:
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return object()

    entrypoints = [
        FakeEntryPoint(
            "codex",
            "takopi.runners.codex:BACKEND",
            plugins.ENGINE_GROUP,
            loader=loader,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    ids = plugins.list_ids(plugins.ENGINE_GROUP)
    assert ids == ["codex"]
    assert calls["count"] == 0


def test_load_entrypoint_records_errors(monkeypatch) -> None:
    def loader():
        raise RuntimeError("boom")

    entrypoints = [
        FakeEntryPoint(
            "broken",
            "takopi.runners.broken:BACKEND",
            plugins.ENGINE_GROUP,
            loader=loader,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    with pytest.raises(plugins.PluginLoadFailed):
        plugins.load_entrypoint(plugins.ENGINE_GROUP, "broken")

    errors = plugins.get_load_errors()
    assert errors
    assert errors[0].name == "broken"
    assert "boom" in errors[0].error


def test_duplicate_entrypoints_are_rejected(monkeypatch) -> None:
    entrypoints = [
        FakeEntryPoint(
            "dup",
            "takopi.runners.one:BACKEND",
            plugins.ENGINE_GROUP,
            dist_name="one",
        ),
        FakeEntryPoint(
            "dup",
            "takopi.runners.two:BACKEND",
            plugins.ENGINE_GROUP,
            dist_name="two",
        ),
    ]
    install_entrypoints(monkeypatch, entrypoints)

    ids = plugins.list_ids(plugins.ENGINE_GROUP)
    assert ids == []

    with pytest.raises(plugins.PluginLoadFailed):
        plugins.load_entrypoint(plugins.ENGINE_GROUP, "dup")

    errors = plugins.get_load_errors()
    assert any("duplicate plugin id" in err.error for err in errors)


def test_allowlist_filters_by_distribution(monkeypatch) -> None:
    entrypoints = [
        FakeEntryPoint(
            "codex",
            "takopi.runners.codex:BACKEND",
            plugins.ENGINE_GROUP,
            dist_name="takopi",
        ),
        FakeEntryPoint(
            "thirdparty",
            "takopi_thirdparty.backend:BACKEND",
            plugins.ENGINE_GROUP,
            dist_name="takopi-thirdparty",
        ),
    ]
    install_entrypoints(monkeypatch, entrypoints)

    ids = plugins.list_ids(plugins.ENGINE_GROUP, allowlist=["takopi"])
    assert ids == ["codex"]


def test_allowlist_canonicalizes_distribution_names(monkeypatch) -> None:
    entrypoints = [
        FakeEntryPoint(
            "slack",
            "takopi.transport.slack:BACKEND",
            plugins.TRANSPORT_GROUP,
            dist_name="takopi-transport-slack",
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    ids = plugins.list_ids(
        plugins.TRANSPORT_GROUP, allowlist=["takopi_transport.slack"]
    )
    assert ids == ["slack"]


def test_validator_errors_are_captured(monkeypatch) -> None:
    entrypoints = [
        FakeEntryPoint(
            "bad",
            "takopi.runners.bad:BACKEND",
            plugins.ENGINE_GROUP,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    def validator(obj, ep):
        raise TypeError("not valid")

    with pytest.raises(plugins.PluginLoadFailed):
        plugins.load_entrypoint(plugins.ENGINE_GROUP, "bad", validator=validator)

    errors = plugins.get_load_errors()
    assert any("not valid" in err.error for err in errors)


def test_reset_plugin_state_clears_cache(monkeypatch) -> None:
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return object()

    entrypoints = [
        FakeEntryPoint(
            "codex",
            "takopi.runners.codex:BACKEND",
            plugins.ENGINE_GROUP,
            loader=loader,
        )
    ]
    install_entrypoints(monkeypatch, entrypoints)

    plugins.load_entrypoint(plugins.ENGINE_GROUP, "codex")
    plugins.load_entrypoint(plugins.ENGINE_GROUP, "codex")
    assert calls["count"] == 1

    plugins.reset_plugin_state()
    plugins.load_entrypoint(plugins.ENGINE_GROUP, "codex")
    assert calls["count"] == 2


def test_clear_load_errors_filters(monkeypatch) -> None:
    def loader():
        raise RuntimeError("boom")

    entrypoints = [
        FakeEntryPoint(
            "broken_engine",
            "takopi.runners.broken:BACKEND",
            plugins.ENGINE_GROUP,
            loader=loader,
            dist_name="engine-dist",
        ),
        FakeEntryPoint(
            "broken_transport",
            "takopi.transports.broken:BACKEND",
            plugins.TRANSPORT_GROUP,
            loader=loader,
            dist_name="transport-dist",
        ),
    ]
    install_entrypoints(monkeypatch, entrypoints)

    with pytest.raises(plugins.PluginLoadFailed):
        plugins.load_entrypoint(plugins.ENGINE_GROUP, "broken_engine")
    with pytest.raises(plugins.PluginLoadFailed):
        plugins.load_entrypoint(plugins.TRANSPORT_GROUP, "broken_transport")

    errors = plugins.get_load_errors()
    assert {err.group for err in errors} == {
        plugins.ENGINE_GROUP,
        plugins.TRANSPORT_GROUP,
    }

    plugins.clear_load_errors(group=plugins.ENGINE_GROUP)
    errors = plugins.get_load_errors()
    assert {err.group for err in errors} == {plugins.TRANSPORT_GROUP}

    plugins.clear_load_errors(name="broken_transport")
    assert plugins.get_load_errors() == ()
