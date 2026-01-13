# Invariants

These are the “don’t break this” rules that keep Takopi reliable.

## Runner contract

The runner contract is enforced by `tests/test_runner_contract.py`:

- Exactly one `StartedEvent`
- Exactly one `CompletedEvent`
- `CompletedEvent` is last
- `CompletedEvent.resume == StartedEvent.resume`

See also the [Plugin API](../plugin-api.md) runner contract section.

## Per-thread serialization

At most one active run may operate on the same thread/session at a time.
This is enforced both by scheduling and by per-resume-token runner locks.

Normative details live in the [Specification](../specification.md) (§5.2).

## Resume lines

Resume lines embedded in chat are the engine’s canonical resume command (e.g. `claude --resume <id>`).

- The runner is authoritative for formatting and extraction.
- Transports/rendering must preserve the resume line reliably (even when trimming/splitting).

Normative details live in the [Specification](../specification.md) (§3).

## Local contribution hygiene

- Run `just check` before code commits.

