from pathlib import Path

import pytest

import takopi.runtime_loader as runtime_loader
from takopi.config import ConfigError
from takopi.settings import TakopiSettings


def test_build_runtime_spec_minimal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runtime_loader.shutil, "which", lambda _cmd: "/bin/echo")
    settings = TakopiSettings.model_validate({"transport": "telegram"})
    config_path = tmp_path / "takopi.toml"
    config_path.write_text('transport = "telegram"\n', encoding="utf-8")

    spec = runtime_loader.build_runtime_spec(
        settings=settings,
        config_path=config_path,
    )

    assert spec.router.default_engine == settings.default_engine
    runtime = spec.to_runtime(config_path=config_path)
    assert runtime.default_engine == settings.default_engine


def test_resolve_default_engine_unknown(tmp_path: Path) -> None:
    settings = TakopiSettings.model_validate({"transport": "telegram"})
    with pytest.raises(ConfigError, match="Unknown default engine"):
        runtime_loader.resolve_default_engine(
            override="unknown",
            settings=settings,
            config_path=tmp_path / "takopi.toml",
            engine_ids=["codex"],
        )
