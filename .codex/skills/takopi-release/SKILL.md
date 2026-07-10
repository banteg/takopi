---
name: takopi-release
description: Prepare and ship a Takopi release. Use when asked to cut a release, bump release versions, update changelog/spec/readme, tag v<major.minor.patch>, or trigger the GitHub release workflow.
---

# Takopi Release

## Overview

Prepare a tagged release that matches the GitHub Actions release workflow. The workflow requires the tag version to match `pyproject.toml`. If `src/takopi/__init__.py` contains a literal `__version__ = "..."`, that literal must also match; current Takopi reads `__version__` from package metadata instead.

## Workflow

### 1) Choose version + date

Pick the release version (major.minor.patch) and the release date (YYYY-MM-DD) for changelog/spec headers.
If the current version has a `.dev` suffix, assume the target release version is the same version without the suffix, as long as that tag does not already exist.

### 2) Update changelog

Update `changelog.md` by adding a new top section. Before writing it, study the diff between the previous tag and the new release to rank changes; put user-facing changes first.

- `## v<major.minor.patch> (YYYY-MM-DD)`
- Include subsections like `changes`, `fixes`, `breaking`, `docs` as needed.
- Keep entries short and include PR links when available (match existing style).

### 3) Bump versions

Update version strings to match the release tag:

- `pyproject.toml`: `project.version = "<major.minor.patch>"`
- `src/takopi/__init__.py`: only update this if it contains a literal `__version__ = "<major.minor.patch>"`; no change is needed when it uses `importlib.metadata.version("takopi")`
- `uv.lock`: refresh so the root package version matches (run `uv lock` or `uv sync`).

### 4) Update spec + docs

Update `docs/specification.md` to match the release:

- Header: `# Takopi Specification v<major.minor.patch> [YYYY-MM-DD]`
- Replace `Takopi v<old>` and `Out of scope for v<old>` lines with the new version.
- Add a changelog entry like `- No normative changes; align spec version with the v<major.minor.patch> release.` unless the spec itself changed.

If the release highlights new features, update `readme.md` accordingly (see v0.9.0 release).

### 5) Run checks

Run the standard checks before committing:

- `just check` (ruff/ty/pytest)

### 6) Commit + tag

Commit the release using conventional commits:

- Commit message: `chore(release): v<major.minor.patch>`
- Tag: `git tag v<major.minor.patch>`

Push the tag to trigger `.github/workflows/release.yml` (build, PyPI publish, GitHub release).

### 7) Optional post-release bump

If you keep a dev version between releases, bump the minor version (reset patch to 0) and commit (`chore: bump version to ...`).

## Notes

- The release workflow checks that the tag matches `pyproject.toml`, and also checks `src/takopi/__init__.py` only when it contains a literal `__version__` string.
- Keep dates consistent across `changelog.md` and `docs/specification.md`.
