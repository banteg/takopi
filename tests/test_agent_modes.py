from pathlib import Path

from takopi.backends import AgentModeProbe
from takopi.agent_modes import AgentModeCapabilities
from takopi.config import ProjectsConfig
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.runners.opencode import discover_agent_modes as discover_opencode_modes
from takopi.transport_runtime import TransportRuntime


def _runtime(
    engine: str,
    *,
    probers: dict[str, AgentModeProbe] | None = None,
) -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine=engine)
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    return TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        config_path=Path("takopi.toml"),
        engine_mode_probers=probers,
    )


def test_runtime_discovers_modes_from_engine_probers() -> None:
    runtime = _runtime(
        "codex",
        probers={
            "codex": lambda _timeout: AgentModeCapabilities(
                supports_agent=True,
                known_modes=("build", "plan"),
                shortcut_modes=("build", "plan"),
            )
        },
    )

    discovered = runtime.discover_agent_modes(timeout_s=1.5)

    assert discovered.supports_agent == frozenset({"codex"})
    assert discovered.known_modes == {"codex": ("build", "plan")}
    assert discovered.shortcut_modes == ("build", "plan")


def test_runtime_discovers_modes_normalizes_shortcuts() -> None:
    runtime = _runtime(
        "codex",
        probers={
            "codex": lambda _timeout: AgentModeCapabilities(
                supports_agent=True,
                shortcut_modes=("Build", "build", " plan "),
            )
        },
    )

    discovered = runtime.discover_agent_modes(timeout_s=1.5)

    assert discovered.shortcut_modes == ("build", "plan")


def test_opencode_discover_agent_modes_soft_fallback(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise OSError("missing")

    monkeypatch.setattr("takopi.runners.opencode.subprocess.run", fake_run)
    discovered = discover_opencode_modes(3.0)

    assert discovered.supports_agent is True
    assert discovered.known_modes == ("build", "plan")
    assert discovered.shortcut_modes == ()


def test_opencode_discover_agent_modes_uses_list_output(monkeypatch) -> None:
    class _Result:
        returncode = 0
        stdout = "build (primary)\nplan (primary)\n"
        stderr = ""

    monkeypatch.setattr(
        "takopi.runners.opencode.subprocess.run",
        lambda *_args, **_kwargs: _Result(),
    )
    discovered = discover_opencode_modes(3.0)

    assert discovered.supports_agent is True
    assert discovered.known_modes == ("build", "plan")
    assert discovered.shortcut_modes == ("build", "plan")
