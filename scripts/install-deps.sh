#!/usr/bin/env bash
set -euo pipefail

if [[ -f uv.lock ]]; then
  echo "[deps] uv lockfile detected -> uv sync --group dev"
  exec uv sync --group dev "$@"
elif [[ -f bun.lock || -f bun.lockb ]]; then
  echo "[deps] bun lockfile detected -> bun install --frozen-lockfile"
  exec bun install --frozen-lockfile "$@"
elif [[ -f pnpm-lock.yaml ]]; then
  echo "[deps] pnpm lockfile detected -> corepack pnpm install --frozen-lockfile"
  corepack enable >/dev/null 2>&1 || true
  exec corepack pnpm install --frozen-lockfile "$@"
elif [[ -f package-lock.json || -f npm-shrinkwrap.json ]]; then
  echo "[deps] npm lockfile detected -> npm ci"
  exec npm ci "$@"
elif [[ -f yarn.lock ]]; then
  echo "[deps] yarn lockfile detected -> corepack yarn install --frozen-lockfile"
  corepack enable >/dev/null 2>&1 || true
  exec corepack yarn install --frozen-lockfile "$@"
else
  echo "[deps] no known lockfile found -> uv sync --group dev"
  exec uv sync --group dev "$@"
fi
