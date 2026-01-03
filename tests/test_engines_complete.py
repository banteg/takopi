from pathlib import Path

import pytest

from takopi.backends import EngineBackend, EngineConfig
from takopi.config import ConfigError
from takopi.engines import get_backend, list_backends, list_backend_ids, get_engine_config


def test_get_backend_known() -> None:
    backend = get_backend("codex")
    assert backend is not None
    assert isinstance(backend, EngineBackend)


def test_get_backend_unknown() -> None:
    with pytest.raises(ConfigError) as exc_info:
        get_backend("unknown_engine")

    assert "Unknown engine 'unknown_engine'" in str(exc_info.value)
    assert "Available:" in str(exc_info.value)


def test_list_backends() -> None:
    backends = list_backends()
    assert len(backends) > 0
    for backend in backends:
        assert isinstance(backend, EngineBackend)


def test_list_backend_ids() -> None:
    ids = list_backend_ids()
    assert len(ids) > 0
    assert "codex" in ids
    assert isinstance(ids, list)
    assert ids == sorted(ids)  # Should be sorted


def test_get_engine_config_valid_dict() -> None:
    config = {
        "codex": {"profile": "test"},
        "claude": {"model": "sonnet"},
    }

    result = get_engine_config(config, "codex", Path("/tmp/config.toml"))
    assert result == {"profile": "test"}


def test_get_engine_config_missing_section() -> None:
    config = {
        "claude": {"model": "sonnet"},
    }

    result = get_engine_config(config, "codex", Path("/tmp/config.toml"))
    assert result == {}


def test_get_engine_config_invalid_type() -> None:
    config = {
        "codex": "invalid",
    }

    with pytest.raises(ConfigError) as exc_info:
        get_engine_config(config, "codex", Path("/tmp/config.toml"))

    assert "Invalid `codex` config" in str(exc_info.value)
    assert "expected a table" in str(exc_info.value)
    assert "config.toml" in str(exc_info.value)
