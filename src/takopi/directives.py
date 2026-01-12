from __future__ import annotations

from dataclasses import dataclass

from .config import ProjectsConfig
from .context import RunContext
from .model import EngineId


@dataclass(frozen=True, slots=True)
class ParsedDirectives:
    prompt: str
    engine: EngineId | None
    project: str | None
    branch: str | None


class DirectiveError(RuntimeError):
    pass


def parse_directives(
    text: str,
    *,
    engine_ids: tuple[EngineId, ...],
    projects: ProjectsConfig,
) -> ParsedDirectives:
    if not text:
        return ParsedDirectives(prompt="", engine=None, project=None, branch=None)

    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    line = lines[idx].lstrip()
    tokens = line.split()
    if not tokens:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    engine_map = {engine.lower(): engine for engine in engine_ids}
    project_map = {alias.lower(): alias for alias in projects.projects}

    engine: EngineId | None = None
    project: str | None = None
    branch: str | None = None
    consumed = 0

    for token in tokens:
        if token.startswith("/"):
            name = token[1:]
            if "@" in name:
                name = name.split("@", 1)[0]
            if not name:
                break
            key = name.lower()
            engine_candidate = engine_map.get(key)
            project_candidate = project_map.get(key)
            if engine_candidate is not None:
                if engine is not None:
                    raise DirectiveError("multiple engine directives")
                engine = engine_candidate
                consumed += 1
                continue
            if project_candidate is not None:
                if project is not None:
                    raise DirectiveError("multiple project directives")
                project = project_candidate
                consumed += 1
                continue
            break
        if token.startswith("@"):
            value = token[1:]
            if not value:
                break
            if branch is not None:
                raise DirectiveError("multiple @branch directives")
            branch = value
            consumed += 1
            continue
        break

    if consumed == 0:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    if consumed < len(tokens):
        remainder = " ".join(tokens[consumed:])
        lines[idx] = remainder
    else:
        lines.pop(idx)

    prompt = "\n".join(lines).strip()
    return ParsedDirectives(
        prompt=prompt, engine=engine, project=project, branch=branch
    )


def parse_context_line(
    text: str | None, *, projects: ProjectsConfig
) -> RunContext | None:
    if not text:
        return None
    ctx: RunContext | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 1:
            stripped = stripped[1:-1].strip()
        elif stripped.startswith("`"):
            stripped = stripped[1:].strip()
        elif stripped.endswith("`"):
            stripped = stripped[:-1].strip()
        if not stripped.lower().startswith("ctx:"):
            continue
        content = stripped.split(":", 1)[1].strip()
        if not content:
            continue
        tokens = content.split()
        if not tokens:
            continue
        project = tokens[0]
        branch = None
        if len(tokens) >= 2:
            if tokens[1] == "@" and len(tokens) >= 3:
                branch = tokens[2]
            elif tokens[1].startswith("@"):
                branch = tokens[1][1:]
        project_key = project.lower()
        if project_key not in projects.projects:
            raise DirectiveError(
                f"unknown project {project!r} in ctx line; start a new thread or "
                "add it back to your config"
            )
        ctx = RunContext(project=project_key, branch=branch)
    return ctx


def format_context_line(
    context: RunContext | None, *, projects: ProjectsConfig
) -> str | None:
    if context is None or context.project is None:
        return None
    project_cfg = projects.projects.get(context.project)
    alias = project_cfg.alias if project_cfg is not None else context.project
    if context.branch:
        return f"`ctx: {alias} @{context.branch}`"
    return f"`ctx: {alias}`"
