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

- [x] **Wizard: identify non-NVIDIA GPUs (2026-07-16)** — prerequisite for the public-LLM
      item below, done first per discussion: a real AMD Ryzen AI APU box (Linux) had
      Ollama accelerating the LLM perfectly well, but the wizard's `NvidiaSmiGPUDetector`
      (CUDA-only via `nvidia-smi`) reported a flat "no GPU detected," understating what was
      actually there. `GPUDetector` (renamed adapter: `SystemGPUDetector`,
      `infrastructure/gpu.py`) gained `detect_gpu() -> DetectedGPU | None`
      (`domain/model.py`), a Linux-only sysfs fallback (`/sys/class/drm/card*/device`:
      PCI `vendor` id → amd/intel/nvidia/unknown; amdgpu's own `mem_info_vram_total` +
      `mem_info_gtt_total` summed for a real memory estimate — the same sysfs fields
      Phase 12's archived findings used by hand to characterize this exact box's ~38.8 GB
      GPU-reachable memory), called only as a fallback when `detect_vram_gb()` (unchanged,
      still CUDA-only) finds nothing. Wired into two of the three steps that inject
      `GPUDetector`, deliberately not the third: `DetectComputeDevice`'s message now names
      the GPU instead of implying nothing is there (`compute_device` itself is unaffected —
      still `cpu`, no ROCm STT/TTS adapter exists); `SelectLLM`'s fit hints now use the
      identified GPU's memory when available, since Ollama can actually place the LLM on
      it; `ResolveSTTEngine` deliberately left untouched (Whisper runs on CPU regardless of
      GPU vendor, so a non-CUDA memory figure has no bearing on that model-size choice).
      9 new infra unit tests (`test_gpu.py`, fake sysfs trees via `tmp_path`) + 6 new/updated
      step tests; 83/85 setup unit tests green (the 2 failures are the pre-existing,
      already-tracked Windows chmod gap). Not yet live-verified against a real AMD box in
      this session (this laptop has none) — worth confirming on the Strix Halo workstation
      next time it's touched.
- [x] **Public/cloud LLM adapter as a wizard-selectable option (2026-07-16, FR-707/TR-955)**
      — live conversation can now use any OpenAI-compatible remote endpoint (OpenRouter,
      OpenAI, a self-hosted vLLM/LM Studio server, ...), toml-direct API-key storage per
      discussion (same precedent as `database.url`'s plaintext password, 0600-permissioned).
      **Split out `OpenAICompatibleLLMService`/`OpenAICompatibleRecallIntentDetector`**
      (new `infrastructure/llm/openai_compatible.py`) from `OpenRouterLLMService`/
      `OpenRouterRecallIntentDetector` — they were already a generic
      `openai.AsyncOpenAI(api_key=, base_url=)` wrapper defaulting to openrouter.ai, so
      the rename/move is mostly a naming-accuracy fix (CLAUDE.md's push-back-on-imprecise-
      naming rule), not new logic; `base_url`/`model` now required (no generic default
      makes sense), `api_key` optional (coalesced to a placeholder string — the openai
      client wants some value even against endpoints that don't check one). The offline
      OpenRouter family (worthiness/disambiguation/synthesis/extraction) is untouched,
      stays un-wired (TR-953).
      **Real design gap found and fixed before wiring**: `RecallIntentDetector.detect()`
      is ALSO a live, per-turn, blocking-before-reply LLM call (`ProcessTurn`, before the
      main completion) — moves together with `LLMService`, not left on local Ollama, or a
      remote-live install would still pay a CPU-inference tax every turn.
      **Second real gap found**: `GenerateMemoryBrief` (offline) was reusing the same
      `ctx.llm` object as live conversation — swapping `ctx.llm` to remote would have
      silently sent brief-generation prompts to the third party too. Fixed: new
      `ServerContext.offline_llm`, a dedicated always-Ollama `OllamaLLMService` instance,
      never aliased to the live `llm` (two instances constructed even when both are
      "ollama" — simpler/safer than special-casing one provider).
      **Config** (`infrastructure/config.py`): `[llm].provider` ("ollama" default |
      "openai_compatible"), `base_url`, `remote_model`, `api_key` — fail-fast
      `RuntimeError` at config-load time if `openai_compatible` is missing `base_url`/
      `remote_model`. `model`/`ollama_host` keep meaning "the local Ollama model," now
      always for the offline pipeline regardless of `provider` — fully backward
      compatible (absent `provider` ⇒ `"ollama"`, identical behaviour to before this
      existed). `server.py`'s composition root branches once on `cfg.llm_provider`;
      the `openai_compatible` import stays inside that branch (not a static top-level
      import) so a fully-local deployment's import graph doesn't touch `openai` at all,
      matching the existing lazy-reexport posture in `infrastructure/llm/__init__.py`.
      **Wizard**: new `ConfigureLLMProvider` step (before `SelectLLM`, now flow step 6 —
      every later step's docstring number shifted +1) asks local-vs-remote, collects
      `base_url`/`remote_model`/optional `api_key` when remote (blank key ⇒ `None`, not
      `""`); re-run pre-fill marks the current choice `(current)`, matching `SelectLLM`'s
      convention. `SelectLLM` (unchanged catalogue/pull logic) always still runs
      regardless of the live choice — its prompt text says so explicitly when provider is
      remote, so picking an Ollama model right after choosing "remote" doesn't read as
      contradictory. `ShowWelcome`'s stale "no cloud fallback" prerequisite bullet fixed;
      `cli.py`'s completion summary now reports both the live and offline model when
      remote. `TomlConfigWriter` omits `provider`/`base_url`/`remote_model`/`api_key`
      entirely for the common local case (minimal-diff-for-default posture, matching
      `tts_device` etc.) — the reader's own defaults have to be right for that to be
      correct, so the two were designed and tested together.
      **Spec**: FR-707 (FUNCTIONAL.md), TR-951/952/953 updated + new TR-955
      (TECHNICAL.md), GLOSSARY's `LLM` entry (TECHNICAL.md/GLOSSARY.md);
      `server/config/memai.example.toml` documents the new keys (also fixed a stale
      `docs/INSTALL_SERVER.md` cross-reference left over from the doc-consolidation
      commit). 245 server unit tests green (up from 233 — 12 new `test_config.py` cases;
      that module needed a local `platformdirs` shim to even collect on this laptop's
      frozen venv, same known gap as ever, not a new one) + 98 setup unit tests green (2
      pre-existing Windows chmod failures, unrelated). `ruff check` clean on every touched
      file in both packages.
      **Not yet live-verified**: no real OpenAI-compatible endpoint, no GPU-less box, and
      no `openai`/`psycopg` in this laptop's frozen venv to actually run `memai-server`
      end-to-end here — next real verification needs a workstation (or any box) with
      network access to a real endpoint, confirming: `uv sync` picks up `openai` cleanly,
      a live turn actually completes via the remote endpoint, recall detection round-trips
      correctly, and the offline pipeline still produces a memory brief via local Ollama
      while `provider = "openai_compatible"` is set.
- [ ] **Native Windows `memai-server` support** — found while consolidating the
      install docs (2026-07-16): `uv sync` in `server/` fails on Windows building
      `numpy==1.26.4` from source (`mesonpy.build_wheel` error, no C/C++ compiler found;
      reproduced on this laptop with `uv sync --native-tls`). Root cause: `numpy` 1.26.4
      predates Python 3.13 Windows wheels, and the resolver won't move it past 1.26.4 even
      via `uv lock --upgrade-package numpy` — Kokoro's English G2P chain
      (`kokoro[en]`→`misaki[en]`→`spacy`→`thinc==8.3.13`→`blis==1.3.3`) anchors it there,
      even though none of those packages' own PyPI metadata declares a `numpy<2` bound
      (checked `blis`/`thinc`/`spacy`/`ctranslate2`/`onnxruntime`/`numba` directly) —
      exact mechanism not fully root-caused, worth another look with `uv`'s resolver
      explain/verbose output. Not fixed here — out of scope for a docs cleanup, and any
      fix needs real testing on Windows with STT/TTS actually exercised, not just a clean
      `uv sync`. Documented as a known limitation in `docs/INSTALLATION.md` with WSL2 as
      the practical interim workaround (untested end-to-end).
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
