# Contributing to Memai

Thanks for helping out. Memai is a personal project, so this is intentionally
light — no CLA, no bureaucracy — just a few conventions to keep things
consistent.

## Getting set up

See the [README](README.md#getting-started) for the two-component layout
(`client/`, `server/`) and prerequisites (Python 3.13+, CUDA GPU for the
server, PostgreSQL + pgvector).

```bash
# Server (GPU machine)
cd server && uv sync

# Client
cd client && uv sync
```

**Only `uv` is used to manage dependencies** — `uv sync`, `uv add`, `uv remove`,
`uv tool install`, `uv run`. Never `pip` or `uv pip`, even for editable
installs.

## Before you start

For anything beyond a small fix, start with the **specification** in
[docs/spec/](docs/spec/SPEC.md) — it is the canonical source for behaviour,
architecture, the data model, and the project vocabulary
([glossary](docs/spec/GLOSSARY.md)). Two rules follow from it:

- A change that alters observable behaviour, a format, a threshold, or an
  invariant updates the affected `FR-`/`TR-`/`INV-` requirement in the same
  PR — the spec never trails the code.
- Tests cite the requirement IDs they verify in their docstrings
  (`"""Spec: INV-12, FR-602 — …"""`).

If your change would cross one of the invariants (e.g. INV-1, the
live/offline boundary), raise it in the PR description before writing code,
not after. [CLAUDE.md](CLAUDE.md) carries the working rules;
`docs/PLAN.md` records where the project currently stands.

## Architecture conventions

The codebase follows **Clean Architecture** with **tactical DDD** patterns:

- **Entities** — domain objects, no external imports
- **Use Cases** — application logic, defines abstract ports/interfaces
- **Infrastructure / Interface Adapters** — concrete implementations
- **External World / Frameworks & Drivers** — DB, HTTP, CLI, external APIs

The dependency rule is strict: inner layers never import from outer ones.
Use the established vocabulary (Aggregate, Value Object, Repository, Domain
Event, etc.) rather than introducing new terms for existing concepts — if
you think a term in the domain model is imprecise, raise it in the PR rather
than working around it silently.

## Code style

Ruff isn't a dependency of any package venv (it lints across all three at
once from the repo root config) — install it once as a standalone tool:

```bash
uv tool install ruff
```

Then, from the repo root:

```bash
ruff check .
ruff format .
```

Line length is 120. Test files are excluded from linting.

## Testing

- **pytest**, following the test pyramid: mostly unit tests, fewer
  integration tests, very few end-to-end.
- New infrastructure dependencies (DB, queue, HTTP client, clock,
  filesystem, …) should get a `Fake*` in-memory implementation of their
  port rather than a mock — this keeps unit tests fast and dependency-free.
  Only reach for `unittest.mock` when a Fake genuinely doesn't fit, and say
  why in the PR.
- Unit tests exercise domain logic and use cases against Fakes; integration
  tests wire real adapters. Keep the two separate.

Run the relevant package's tests before opening a PR:

```bash
cd server && uv run pytest
cd client && uv run pytest
```

## Submitting a PR

1. Branch off `master`.
2. Keep the PR scoped to one change — no drive-by refactors bundled in.
3. Make sure `ruff check .` and the test suite pass.
4. Describe *why*, not just *what* — especially for anything touching the
   memory model, the live/offline boundary, or persona scoping, since those
   have non-obvious invariants documented in `CLAUDE.md`.

By submitting a PR, you agree your contribution is licensed under this
project's [AGPL-3.0 license](LICENSE).
