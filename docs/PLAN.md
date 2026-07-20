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

- [x] **Recall gating replaces the per-turn LLM classification call (2026-07-16,
      FR-309/TR-314)** — prototyped per discussion: `RecallIntentDetector` (an LLM call
      classifying "remember when…" intent + extracting a query + memory types on
      *every* turn, local or remote) was judged fragile/over-designed and retired
      outright, not kept alongside the replacement. New `RecallGate` port
      (`services/ports.py`, in the persona-extension-port family) — `should_embed(text)`
      short-circuits trivial short utterances before any embedding is computed;
      `should_search(max_similarity_to_prior_searches)` skips a fresh DB round trip when
      the turn's embedding is nearly identical to *any* prior search this session for
      the active persona — not only the immediately preceding one — reusing that
      search's cached results instead of paying for another lookup. Persona-scoped like
      `PersonaSelectionPort` et al., but with a real default rather than a no-op
      fallback: `DefaultRecallGate` (`infrastructure/recall_gate.py`, `min_words=3`,
      `dedup_threshold=0.93` — the latter reuses the existing merge-threshold "same
      thing" bar rather than a new placeholder) covers GA and anything unregistered;
      `LanguageTutorRecallGate(DefaultRecallGate)` overrides only `should_embed` to
      always return `True` — a tutor's one-word vocabulary answers are exactly the
      content worth searching, unlike GA's throwaway "yes"/"ok" replies. New pure
      `domain.model.cosine_similarity` (general formula, doesn't assume L2-normalised
      input) compares the current turn's embedding against **every** entry in
      `WorkingMemory.recall_history[persona_id]` (persona-keyed list, oldest first, of
      every real search's embedding + results this session) entirely in-process — no DB
      round trip for this comparison, distinct from pgvector's own `<=>` operator. No
      more type-restricted search (`memory_types` classification) or extracted-query
      text — the turn's raw text is embedded directly and every `MemoryType` is
      searched, since the existing top-5-by-similarity ranking already sorts it out and
      the old classification step bought less precision than it looked like.
      **Design refinement caught before commit**: comparing only against the *last*
      search (the first cut) missed that nothing new can enter long-term memory
      mid-session (INV-1) — the searchable set is frozen for the whole conversation, so
      a repeat of *any* earlier query, not only the immediately preceding one, would
      deterministically return the same results again. Reworked to cache every real
      search this session (not just the latest) and take the max similarity across the
      whole history — same port signature, no interface change, just what the argument
      represents and what `ProcessTurn` computes before calling it.
      **Real side-effect, not a target of this change**: removing recall from the live
      path also simplified server.py's provider branch — recall no longer needs an
      OpenAI-compatible twin at all (it was never really an LLM call's business to vary
      with `[llm].provider`), so `OpenAICompatibleRecallIntentDetector` was deleted
      rather than kept. `RecallTriggered`/`RecallSource` (domain events) and
      `RecallIntentDetector` (domain protocol) removed entirely — nothing else
      constructed them once the mechanism was gone, and this project doesn't keep
      orphaned code around "just in case."
      265 server unit tests green (up from 245 — new `test_cosine_similarity.py`,
      `test_recall_gate.py`, `TestRecallGating` in `test_session.py` including a
      dedicated test proving an *older*, non-latest history entry is found and reused
      over a more recent unrelated one; the existing weak recall test was rewritten to
      actually assert recalled content reaches the LLM's system prompt, not just that
      some LLM call happened). `ruff check` clean on every touched file. Spec: FR-302
      reworded (no longer "explicit recall intent"), new FR-309; TR-302 reworded, new
      TR-314; TR-955 corrected (recall no longer varies with `[llm].provider`);
      GLOSSARY's `Recall` entry rewritten, new `Recall gate` entry.
      **Not yet live-verified**: word-count/dedup-threshold defaults (3 words, 0.93)
      are reasoned choices, not calibrated against real conversations — same
      calibration-pending posture as every other threshold in this project. Worth
      watching once real usage exists: does 3 words correctly separate "yes"/"no" from
      genuine short recalls; does 0.93 dedup correctly catch topic-repeats without
      suppressing a genuinely new question phrased similarly to a recent one; whether
      an unbounded per-persona `recall_history` list ever needs a cap in practice (not
      added speculatively — realistic session lengths keep it small).
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
- [x] **Native Windows `memai-server` support** (2026-07-17) — root-caused and fixed the
      numpy half of the earlier 2026-07-16 finding: the resolver was anchored to
      `numpy==1.26.4` (no cp313 Windows wheel) not by Kokoro's G2P chain as first
      suspected, but by the vestigial `tts` optional-dependency group (Coqui `TTS` →
      `gruut[de]==2.2.3` → `numpy<2.0.0`) — a group already dead weight (`TTS` requires
      `python<3.12`, the project requires `python>=3.13`; Kokoro was already its intended
      replacement per the removed comment). Deleted the `tts` extra from
      `server/pyproject.toml`, re-ran `uv lock --upgrade-package numpy --native-tls`:
      numpy now resolves to 2.5.1 with a native `win_amd64`/cp313 wheel; 253/253 unit
      tests still green (confirmed `TTS`/Coqui was never imported from source, only the
      unrelated Kokoro-based `infrastructure/tts.py` module which is unaffected).
      Confirmed via a real `uv sync --native-tls` on this laptop that this was the *only*
      Windows-unbuildable package removed — one real blocker remains and is expected,
      not a bug: `curated-tokenizers` (Cython/C++, pulled in via
      `kokoro`→`misaki[en]`→`spacy-curated-transformers`), which has no prebuilt wheel on
      any OS and needs a C/C++ compiler to build — exactly the role `build-essential`
      already plays on Linux. On Windows that means Microsoft C++ Build Tools, which
      needs an admin account this laptop doesn't have, so `uv sync` still can't complete
      *here* — but a normal Windows user with admin rights now can. Documented in
      `docs/INSTALLATION.md` as a first-class native-Windows install path (C++ Build
      Tools step, updated OS support table, Ollama/Docker Desktop Windows notes, remote
      LLM provider bullet for FR-707), with WSL2 demoted to the no-admin fallback.
      **Not yet live-verified**: no admin-rights Windows box available in this session to
      confirm `uv sync` completes end-to-end and STT/TTS actually work once Build Tools
      are installed — next real verification needs exactly that.
      **Follow-up (2026-07-17, same day)**: Postgres install docs were also fixed to stop
      documenting our own OS-specific install steps and instead point to PostgreSQL's and
      pgvector's own per-OS install guides, with Docker demoted to the explicit
      "sidesteps building pgvector on Windows" alternative. That surfaced a real gap:
      `ConfigureDatabaseConnection` (setup wizard step 3) unconditionally offered
      "peer" auth with no platform check at all, even though its own docstring already
      said peer is Linux/macOS-only (`getpeereid()`/`SO_PEERCRED`, confirmed absent on
      Windows via PostgreSQL's own docs) — a Windows user would have picked it, watched
      it fail, and gotten a `systemctl reload`-flavored hint that doesn't apply. Verified
      PostgreSQL's Windows equivalent, SSPI, is real and applicable here (works for a
      local non-domain account via NTLM fallback, not just Kerberos/AD; official Windows
      libpq/psycopg builds always compile in SSPI support, no extra dependency). Added a
      `sys.platform`-gated branch to the step (`sspi` choice + DSN
      `postgresql://memai@localhost:5432/memai` + a Windows-flavored pg_ident.conf/
      pg_hba.conf/`Restart-Service` hint on failure, mirroring the existing peer-auth
      path) and 4 new unit tests (platform-gated choice lists, SSPI success, SSPI failure
      hint, re-run "keep" dispatches to the SSPI hint too); 102/104 setup unit tests green
      (the 2 pre-existing Windows chmod failures, unrelated). Documented in
      `docs/INSTALLATION.md` as a third `<details>` block alongside peer and password
      auth. **Not yet live-verified**: no real Windows Postgres instance available this
      session to confirm the SSPI handshake actually completes end-to-end.
- [x] **Concept/Procedure offline-consolidation redesign** (2026-07-20,
      FR-307/FR-310/FR-407/FR-504, TR-606/609/703/705-708) — triggered by the
      2026-07-18 live-testing false-positive review (`project_extraction_false_positives_needs_review`
      memory): concepts/procedures bypassing worthiness entirely was too permissive,
      and a debugging session got fabricated into a personal-event episode despite the
      worthiness LLM correctly judging the conversation substantial. Four changes:
      1. Procedures are never extracted from live conversation, any persona —
      authoring-only (bundle install, persona enrichment); `ExtractionResult.procedures`
      removed, `_extraction_system_prompt` drops the schema section unconditionally.
      `bundle_install`/`EnrichMemory` procedure upserts now force
      `update_description=False` — an authored Procedure's content is immutable once
      installed, only engagement/`persona_state` move (previously only tutor-gated
      conversation extraction had this protection; bundle reinstall and
      cluster-proposal merges didn't).
      2. New `Concept.origin` field (`"authored"` vs `"organic"`, immutable like
      `language`; new `concepts.origin` DB column, `ALTER TABLE ... ADD COLUMN IF NOT
      EXISTS` idempotent-reapply pattern matching the existing `directive` column).
      `MemoryUpserter.upsert_concept` is origin-aware (TR-609): a live-extraction
      candidate landing within `authored_protection_threshold` (0.75) of an existing
      authored concept is a touch, never a rewrite, regardless of persona — replaces
      the old blanket `allow_insert=False` block for strategy personas (removed
      entirely, along with the now-dead `allow_insert` param on both `upsert_concept`/
      `upsert_procedure`). A genuinely new organic insert additionally needs
      `min_concept_engagement_turns` (2) of the conversation's own user turns
      topically similar (`concept_engagement_similarity`, 0.55, reusing TR-807's
      `interest_cluster_threshold` calibration) to it — an assistant-only mention no
      longer creates a permanent concept.
      3. FR-407/504 relaxed for concepts only: a tutor session can now produce a
      genuinely new organic concept if distinct from curriculum and
      engagement-gated (episodes stay fully blocked for strategy personas, as before —
      lesson drills aren't events).
      4. Cheap extraction floor ahead of any LLM call (TR-707): `min_user_turns=2`/
      `min_user_words=40` (user turns only, assistant chatter excluded) — below it,
      worthiness + extraction are skipped outright. Worthiness/extraction prompts
      (`WORTHINESS_SYSTEM_PROMPT`, now deduplicated between the Ollama/OpenRouter
      evaluators, which carried byte-identical strings before) explicitly exclude
      assistant-operational content and require genuine time/place grounding for an
      episode; `_parse_extraction` drops an episode with no real `happened_at` instead
      of silently backdating it to the conversation's own timestamp — was masking
      exactly the fabricated-episode failure mode above.
      289/289 server unit tests green, with new coverage for the floor/
      engagement-gate/authored-protection paths (`test_consolidation.py`,
      `test_enrichment.py`, `test_extraction_prompt.py`). **Not yet live-verified**:
      needs a real Postgres + real LLM run on the workstation to confirm the new gates
      actually suppress the 2026-07-18 noise pattern in practice, not just pass unit
      tests against fakes; `test_consolidation_pipeline.py`/`test_postgres.py`
      integration tests were updated for the schema/behavior change but not run here
      (no local Postgres).
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
- [ ] `authored_protection_threshold` (0.75), `concept_engagement_similarity` (0.55),
      `min_concept_engagement_turns` (2) — new FR-310 concept-origin gates
      (2026-07-20), untested against real usage.
- [ ] `min_user_turns`/`min_user_words` (2/40) — `ConsolidateMemory`'s extraction floor
      (FR-307/TR-707, 2026-07-20).

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
- **Laptop `server/` venv frozen**: `uv.lock` now pins numpy 2.5.1 (fixed 2026-07-17,
  see the Windows support item above), but `uv sync` still can't complete on this laptop
  — `curated-tokenizers` needs a C/C++ compiler and this laptop has no admin rights to
  install one. Always `uv run --no-sync` here regardless. Locally,
  `tests/unit/infrastructure/test_config.py` fails collection (`platformdirs` missing,
  never installed since `uv sync` has never completed on this laptop) — `--ignore` it;
  it runs on the workstation.
