from pathlib import Path

from takopi.config import ProjectsConfig
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.agent_modes import (
    _parse_opencode_agents,
    discover_engine_modes,
)
from takopi.transport_runtime import TransportRuntime


def _runtime(engine: str) -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine=engine)
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    return TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        config_path=Path("takopi.toml"),
    )


def test_parse_opencode_agents() -> None:
    output = """build (primary)
plan (primary)
explore (subagent)
"""
    assert _parse_opencode_agents(output) == ("build", "plan", "explore")


def test_discover_engine_modes_opencode_soft_fallback(monkeypatch) -> None:
    runtime = _runtime("opencode")

    def fake_run(*args, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr("takopi.telegram.agent_modes.subprocess.run", fake_run)
    discovered = discover_engine_modes(runtime)

    assert "opencode" in discovered.supports_agent
    assert discovered.known_modes["opencode"] == ("build", "plan")
    assert discovered.shortcut_modes == ()
