# PLAN.md — Memai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Where the project stands (2026-07-14)

Phases 1–13 are implemented and verified: domain + service layers, all infrastructure
adapters (Postgres+pgvector, faster-whisper, Ollama/OpenRouter, Kokoro, e5 embeddings),
the full WebSocket server, offline consolidation pipeline, MemoryBrief generation and
injection, the install wizard (incl. model downloads and re-run pre-fill), the config
placement / persona-lifecycle refactor, persona extension ports, the bundle format +
`InstallPersonaBundle`, the Italian language-tutor persona (selection / assessment /
enrichment strategies, two-teacher cast via per-segment language detection), and the
installed-languages contract + GA response-language mirroring.

The full task-by-task record — findings, live-verification notes, benchmark numbers,
rejected alternatives — is archived in
[docs/archive/PLAN_phases_1-13.md](archive/PLAN_phases_1-13.md); references elsewhere
in the repo (code comments, briefs, tests) to "PLAN.md Phase N" resolve there. The spec
([docs/spec/](spec/)) is the present-tense description of behaviour; this file only
tracks work still to do.

---

## Next up

- [ ] **Phase 13 live smoke on the workstation**: fresh onboarding shows only installed
      languages; speak a second installed language to the GA (reply mirrors, voice
      switches); speak an uninstalled language (primary-language reminder names
      memai-setup); tutor lesson — confirm `[lang:]` tags appear in the composed context
      (tutor-debug), the tutor treats a wrong-language attempt gently, and no tag is
      ever spoken; re-run memai-setup and confirm the existing-install banner +
      pre-checked languages. Workstation config catch-up: add `[languages] installed`
      to its memai.toml (or leave absent for all-supported behaviour).
- [ ] **Phase 12 live smoke with the real client (mic/speakers)**: the tutor flow is
      verified via the plain-text LLM quality gate
      (`server/tests/integration/test_tutor_llm_quality_gate.py`, 5/5 against
      `aya-expanse`) and scripted-WebSocket runs, but never with real audio hardware —
      confirm the persona switch, the two-teacher cast audibly switching voices,
      HVPT rotation across sessions, and the overall acoustic experience.

---

## Phase 9 — Live Voice-Command Wiring (not yet designed)

Placeholder for the next design session (per the Phase 8 close-out, 2026-07-07).
Nothing in this section is a committed decision.

- [ ] LLM tool-calling / intent detection so GA can actually trigger, mid-conversation:
      `UpdateIdleConsolidationMinutes`, `DeactivatePersona`/`ReactivatePersona`, and
      voice / `speaking_rate` / `voices`-map changes via `EditPersona`. Phase 8 built
      the data model and use cases; none are wired to live conversation yet.
      *New evidence since this was written (Phase 12, 2026-07-13)*: bracket-tag emission
      on `aya-expanse` has a real reliability ceiling — the front-loaded few-shot fixed
      `[PERSONA:]`/`[FOCUS:]` (8/8), and Ollama tool/function-calling (`aya-expanse`
      lists `tools` as a capability) remains an unexplored alternative signaling channel
      worth evaluating in this design.
- [ ] **Prerequisite**: discovery/registry for "what's voice-configurable" — a small
      declarative registry (e.g. a `VoiceConfigurableField` descriptor: entity, field,
      type, validator, use case to invoke) that both `ONBOARDING_SCRIPT` and the
      tool-calling layer read from, replacing hand-written prose that is already stale
      (`idle_consolidation_minutes`/`speaking_rate` exist but aren't mentioned there).
      Likely a prerequisite for the item above, needs its own design session — see
      `project_memai_open_questions` item 15.
- [ ] `StartSession`'s two hardcoded, unwired constructor defaults
      (`session_tail_turns=10`, `session_continuation_threshold_hours=24.0` in
      `services/session.py`) — candidate promotion to `User` fields under the same
      DB-attribute placement rule (open-questions item 14).
- [ ] VAD silence-frame threshold voice-configurability — client-side; needs a new
      server→client WS message, since the client is fully stateless.

---

## Wizard (Phase 7) leftovers

- [ ] `--client` flow (`cli.py` still raises `NotImplementedError`)
- [ ] `--uninstall` flow (same)
- [ ] Kokoro voice-pack pre-download: the wizard now downloads Whisper models, Piper
      voices, and the embedding model (`steps.py`), but Kokoro voice packs are still
      lazily fetched from HF on first live use — fails on an offline/locked-down server
      (`HF_HUB_OFFLINE=1`), and the standing cause of the 5 skipped integration tests
      (es/it/pt/ja/zh-cn voice packs).
- [ ] Corporate-proxy / `SSL_CERT_FILE` handling for wizard downloads should be
      automatic (or at least an actionable error), not a manual env-var/`--system-certs`
      ritual — the raw failure is a misleading httpx traceback (details in the archive
      and `project_gpu_workstation_environment` memory).
- [ ] Install-time CUDA-compat verification: `CheckPrerequisites`/`RunHealthChecks`
      should catch CUDA library mismatches (the `nvidia-cublas-cu12` vs CUDA-13
      `nvidia-cublas` class of bug) rather than relying on the pyproject pin never
      drifting.
- [ ] `ServerWebSocketHealthCheck` only checks "something is listening on the port",
      not "memai-server actually started with working STT/TTS" — a subprocess-launch
      check needs the server's own venv, deferred.
- [ ] 2 chmod-related setup tests fail on Windows (unfixable there; run on Linux).

*Stale gap closed, for the record*: the old "no wizard step collects Postgres
connection details" gap is gone — `ConfigureDatabaseConnection` exists and pre-fills
on re-runs (FR-706).

---

## Calibration — blocked on real usage data

All instrumented and shippable with placeholder values; revisit once real sessions
accumulate:

- [ ] Merge/disambiguate upsert thresholds (0.93 / 0.75) — the hypernym vs.
      same-name-different-domain cases sit uncomfortably close (0.86–0.87); real
      embedding-calibration numbers are in the archived Phase 3 notes.
- [ ] Tutor retention ranking + half-life parameters — assessment writes
      `persona_state` from day one; selection keeps ranking by `engagement_level`
      until data justifies flipping `settings["ranking"] = "retention"`.
- [ ] `_PREFIX_SCAN_WINDOW_CHARS` (200) — latency-vs-recognition tradeoff for
      `[PERSONA:]`/`[FOCUS:]` lead-in prose.
- [ ] `episode_anchor_threshold` (0.6) and `elicitation_cap` (2).
- [ ] Threshold promotion to persona-scoped, voice-configurable fields (Phase 9
      candidate) — blocked on the same calibration data.

## Deferred by explicit decision — waiting for real tutoring sessions

- [ ] Pronunciation-signal mining: faster-whisper's per-segment `avg_logprob` /
      `no_speech_prob` / language-probability distribution, currently discarded.
- [ ] Any constrained-STT experiment.
- [ ] `User.secondary_languages` dead-column removal (never populated; superseded by
      installed languages — glossary already marks it "removal pending").

## Known gaps / accepted limitations (revisit when relevant)

- **Localized FOCUS marker**: the tutor spontaneously emitted `[Focalizza: …]`;
  `_extract_tag` matches only the literal `[FOCUS:` so it fell through as spoken
  bracket text. Harmless, flagged 2026-07-13, not fixed.
- **TPRS cross-session story recap** no longer persists (the consolidation gates for
  strategy personas removed episode extraction); no replacement mechanism —
  deliberate open gap.
- **Replay idempotency race** (Phase 5): `TurnLogReplayer`'s check-then-insert isn't
  atomic across the live + offline DB connections; a reconnect landing in a narrow
  window could duplicate one session's rows. Fix sketch if it ever bites: a
  `replayed_sessions(session_id UUID PRIMARY KEY)` claim table with
  `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
- **Acoustic echo** on the client — no echo cancellation; mic muting during playback
  is the current mitigation (INV-4 no-barge-in makes this tolerable).
- **Markdown stripping** is pattern-based and still incomplete for exotic LLM output.
- **Coqui TTS licence conflict** (MPL-2.0 Exhibit B vs AGPL-3.0) — deferred; Piper and
  Kokoro are the working engines.
- **Korean unsupported**: Kokoro has no Korean pipeline; MeloTTS rejected on dependency
  footprint. Revisit if a lighter Korean TTS appears.
- **Client-side first-launch onboarding proposal** (fold server address, port, and
  language into one client-side questionary wizard instead of the server-driven
  `select_language` flow) — archived Phase 4 "revisit" note, still undecided.
- **Laptop `server/` venv frozen**: `uv.lock` pins numpy 1.26.4 (unbuildable on
  Windows/3.13) — always `uv run --no-sync` on the laptop; consider
  `uv lock --upgrade-package numpy` next time the lock is touched on the workstation.
  Locally, `tests/unit/infrastructure/test_config.py` fails collection
  (`platformdirs` missing) — `--ignore` it; it runs on the workstation.
