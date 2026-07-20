# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in
this repository.

## Project Overview

AI voice assistant that runs entirely on local, open-source infrastructure — no cloud
services. Monorepo of three independent uv-managed Python packages: `client/` (mic
capture + playback; Windows/macOS/Linux), `server/` (STT → LLM → TTS pipeline +
long-term memory; GPU machine), `setup/` (install wizard).

## The specification is canonical

**`docs/spec/` is the single source of truth** for behaviour, architecture, protocol,
data model, and vocabulary:

- [docs/spec/SPEC.md](docs/spec/SPEC.md) — how the spec ↔ test ↔ code alignment loop
  works (requirement IDs, test citations, drift rules). **Follow it**: any code change
  that alters observable behaviour, a format, a threshold, or an invariant updates the
  affected `FR-`/`TR-`/`INV-` requirement in the same commit; tests cite the IDs they
  verify in their docstrings.
- [docs/spec/GLOSSARY.md](docs/spec/GLOSSARY.md) — the ubiquitous language. Use these
  terms exactly; push back on drift (see the incubator CLAUDE.md's Ubiquitous Language
  rule).
- [docs/spec/FUNCTIONAL.md](docs/spec/FUNCTIONAL.md) /
  [docs/spec/TECHNICAL.md](docs/spec/TECHNICAL.md) — the requirements themselves.

Do not restate spec facts here or in other docs — link to them. Where any document
disagrees with the spec, the spec wins and the disagreement is a bug to fix now.

## Hard invariants (details in TECHNICAL.md §Invariants)

Reminders of the rules most likely to be violated accidentally — flag and reject, never
silently work around:

- **INV-1 Live/offline boundary**: the live conversation path writes only JSONL session
  logs; any DB write/extraction/embedding-for-storage bleeding into it must be rejected.
- **INV-3 Single user** — no auth, no concurrency model, by design.
- **INV-4 No barge-in** — the reply plays to completion; do not add interruption logic.
- **INV-5 Session logs kept forever** — no rotation/cleanup without explicit discussion.
- **INV-6 Opacity**: no generic code path may branch on `persona_state` or
  `AssistantPersona.settings` contents.
- **INV-9 Cascade delete** personas → concepts/procedures is load-bearing; do not
  change to SET NULL without explicit discussion.
- **FR-701 Voice-only config scope**: GA settings are DB-backed `User`/`AssistantPersona`
  attributes; `memai.toml` is bootstrap-only; install/download/restart concerns belong
  to the wizard, not conversation.

## Session start

If `docs/PLAN.md` exists, read it first — project status and open work. Update its task
markers (`[ ]`/`[~]`/`[x]`) as work progresses. The phase-by-phase history (findings,
verification records) is archived in `docs/archive/PLAN_phases_1-13.md`; the spec is
the present tense.

## Environment & running

Python 3.13+; each package has its own venv. **uv only** — never pip (see incubator
CLAUDE.md / memory: hard rule).

```bash
# Server (GPU machine)
cd server && uv sync
.venv/bin/memai-server            # Linux/macOS (.venv/Scripts/… on Windows)

# Client
cd client && uv sync
.venv/Scripts/memai-client        # Windows (current dev OS)

# Setup wizard / bundle install
cd setup && uv sync && uv run memai-setup
cd server && uv run memai-bundle install <bundle-dir>
```

Dev-environment quirk: this laptop's `server/` venv is frozen — always
`uv run --no-sync` here; integration tests run on the GPU workstation only (see
auto-memory `project_dev_environment_split`).

## Linting

Ruff at the monorepo root, `line-length = 120`, tests excluded. Not in any venv —
`uv tool install ruff`, then `ruff check .` / `ruff format .` from the root.

## Testing

- pytest; test pyramid (many unit / fewer integration / few E2E).
- Fakes over mocks for every port (see incubator CLAUDE.md).
- Unit tests: `cd server && uv run pytest tests/unit` (no GPU/DB). Full suite needs
  real Postgres + models (workstation).
- `server/tests/e2e/` — manual-only LLM quality gates against a real running server
  (real model output, not `FakeLLMService`), never picked up by a bare `pytest`/CI run
  (skip unless their `MEMAI_TEST_*` env vars are set). Report cards for a design/model
  pairing, not regression tests — see each module's docstring for setup.
- New/touched tests cite the spec IDs they verify: `"""Spec: INV-12, FR-602 — …"""`.

## LLM model guidance (operational)

Wizard default is `aya-expanse` (~8B multilingual), but it has a known bug: once its
`response_language` drifts, it never recovers for the rest of the session.
`llama3.1:8b` wobbles occasionally but self-corrects, and is this machine's actual
configured model as of 2026-07-18 (`memai.toml [llm] model`) — check that file rather
than assuming aya-expanse is what's actually running. Avoid ~70B-class models (VRAM
eviction, cold-reload stalls) and reasoning models (`<think>` blocks get spoken
aloud) — rationale in spec TR-952.
