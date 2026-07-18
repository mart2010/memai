# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import json
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import truststore
import websockets

from .domain.model import (
    DEFAULT_VOICE_ROLE,
    SUPPORTED_LANGUAGES,
    Language,
    User,
    resolve_installed_languages,
)
from .infrastructure import postgres
from .infrastructure.config import load_config
from .infrastructure.embedding import SentenceTransformerEmbeddingService
from .infrastructure.language_detection import Py3LangidLanguageDetector
from .infrastructure.json_file import JSONLSessionLogReader, JSONLSessionReplayReader, JSONLTurnLogger
from .infrastructure.language_tutor import (
    LanguageTutorAssessmentStrategy,
    LanguageTutorEnrichmentStrategy,
    LanguageTutorRecallGate,
    LanguageTutorSelectionStrategy,
    OllamaClusterProposer,
    OllamaFocusInterpreter,
    OllamaPracticeJudge,
    STRATEGY_NAME as LANGUAGE_TUTOR,
)
from .infrastructure.llm import (
    OllamaConsolidationExtractor,
    OllamaDisambiguationEvaluator,
    OllamaLLMService,
    OllamaMemorySynthesizer,
    OllamaWorthinessEvaluator,
)
from .infrastructure.postgres import (
    PSConversationRepository,
    PSMemoryBriefRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)
from .infrastructure.recall_gate import DefaultRecallGate
from .infrastructure.stt import FasterWhisperSTTService
from .infrastructure.tts import KOKORO_DEFAULT_VOICES, KokoroTTSService
from .services.directives import PersonaDirectiveSync
from .services.memory import ConsolidateMemory, EnrichMemory, GenerateMemoryBrief
from .services.persona import EditPersona
from .services.ports import LLMService, PersonaAssessmentPort, PersonaEnrichmentPort, PersonaSelectionPort, RecallGate
from .services.replay import TurnLogReplayer
from .services.session import EndSession, ProcessTurn, StartSession
from .services.user import CompleteOnboarding

# ---------------------------------------------------------------------------
# Long-lived server context — one DB connection and one set of real adapters
# shared across connections (single-user, no concurrency model; see CLAUDE.md)
# ---------------------------------------------------------------------------


@dataclass
class ServerContext:
    conn: psycopg.Connection
    stt: FasterWhisperSTTService
    # Live conversation only (ProcessTurn) — OllamaLLMService by default, or
    # OpenAICompatibleLLMService when [llm].provider = "openai_compatible"
    # (FR-707/TR-955). Never used for the offline pipeline — see offline_llm.
    llm: LLMService
    tts: KokoroTTSService
    embedding_service: SentenceTransformerEmbeddingService
    language_detector: Py3LangidLanguageDetector
    log_dir: Path
    memory_merge_threshold: float
    memory_disambiguate_threshold: float
    # The local Ollama model/host — always used for the offline pipeline (extraction,
    # worthiness, disambiguation, synthesis, GenerateMemoryBrief via offline_llm) and
    # every Ollama-backed strategy helper (e.g. the tutor's focus interpreter),
    # regardless of what `llm` above is live-configured to. A GPU-less/CPU-only
    # offline run is fine, per design decision, just slower.
    llm_model: str
    llm_ollama_host: str | None
    # Dedicated instance for GenerateMemoryBrief (offline) — deliberately never `llm`
    # above: offline stays on local Ollama regardless of the live provider choice, so a
    # remote-LLM install doesn't silently start sending brief-generation prompts to a
    # third party too. Always Ollama, so always constructible even when `llm` isn't.
    offline_llm: OllamaLLMService
    # Wizard-installed languages (FR-705): the subset of SUPPORTED_LANGUAGES whose TTS
    # voices this installation actually pulled. Bounds onboarding selection (TR-103).
    installed_languages: list[Language]
    # [server].session_tail_turns (FR-109) — 0 disables tail injection entirely.
    session_tail_turns: int
    user_repo: PSUserRepository
    persona_repo: PSPersonaRepository
    memory_brief_repo: PSMemoryBriefRepository
    memory_repo: PSMemoryRepository
    conversation_repo: PSConversationRepository
    # Live, per-turn (ProcessTurn) recall policy (FR-309/TR-314) — persona-scoped;
    # unlike `llm` above, does not vary with [llm].provider at all (it's local
    # threshold logic, no LLM call). default_recall_gate covers GA and any persona
    # without a registered override.
    default_recall_gate: DefaultRecallGate
    worthiness_evaluator: OllamaWorthinessEvaluator
    disambiguator: OllamaDisambiguationEvaluator
    synthesizer: OllamaMemorySynthesizer
    extractor: OllamaConsolidationExtractor
    # Dedicated connection + repos for the background offline pipeline (TurnLogReplayer ->
    # ConsolidateMemory), run via asyncio.to_thread. Kept separate from `conn` above: a
    # Postgres connection is a single logical session, so a live per-connection query
    # (StartSession etc., run directly on the event loop thread) racing against the
    # background thread's open per-conversation transaction on the *same* connection
    # could execute as part of that transaction, or block the event loop waiting on it.
    # Two independent connections sidestep this entirely.
    offline_conn: psycopg.Connection
    offline_user_repo: PSUserRepository
    offline_persona_repo: PSPersonaRepository
    offline_memory_brief_repo: PSMemoryBriefRepository
    offline_memory_repo: PSMemoryRepository
    offline_conversation_repo: PSConversationRepository
    offline_unit_of_work: PSUnitOfWork
    idle_timer_task: asyncio.Task | None = None


def _replay_sessions(
    log_dir: Path,
    conversation_repo: PSConversationRepository,
    persona_repo: PSPersonaRepository,
) -> int:
    replayer = TurnLogReplayer(
        session_reader=JSONLSessionReplayReader(log_dir),
        conversation_repo=conversation_repo,
        persona_repo=persona_repo,
    )
    return replayer.execute()


async def _run_offline_pipeline(ctx: ServerContext) -> None:
    """TurnLogReplayer -> ConsolidateMemory -> GenerateMemoryBrief. Triggered by the
    idle timer below; also doubles as crash recovery since replay runs on every connect.

    TurnLogReplayer and ConsolidateMemory are both fully synchronous (file/DB/LLM calls
    with no real await point) — dispatched via asyncio.to_thread so a long consolidation
    run doesn't block the event loop and delay an incoming reconnect. Both use the
    dedicated `offline_*` repos/connection, never the live `ctx.conn`. GenerateMemoryBrief
    stays awaited directly: it streams via ollama.AsyncClient and genuinely cooperates
    with the event loop (and only touches the DB via `offline_memory_brief_repo`, cheap
    enough not to need its own thread hop)."""
    replayed = await asyncio.to_thread(
        _replay_sessions, ctx.log_dir, ctx.offline_conversation_repo, ctx.offline_persona_repo,
    )
    consolidate = ConsolidateMemory(
        conversation_repo=ctx.offline_conversation_repo,
        memory_repo=ctx.offline_memory_repo,
        embedding_service=ctx.embedding_service,
        extractor=ctx.extractor,
        worthiness_evaluator=ctx.worthiness_evaluator,
        disambiguator=ctx.disambiguator,
        synthesizer=ctx.synthesizer,
        unit_of_work=ctx.offline_unit_of_work,
        user_repo=ctx.offline_user_repo,
        assessment_strategies=_build_assessment_strategies(ctx),
        merge_threshold=ctx.memory_merge_threshold,
        disambiguate_threshold=ctx.memory_disambiguate_threshold,
    )
    processed = await asyncio.to_thread(consolidate.execute)
    # Enrichment runs after consolidation so strategies see freshly written
    # persona_state; strategies decide internally whether anything qualifies.
    enrich = EnrichMemory(
        memory_repo=ctx.offline_memory_repo,
        embedding_service=ctx.embedding_service,
        disambiguator=ctx.disambiguator,
        synthesizer=ctx.synthesizer,
        unit_of_work=ctx.offline_unit_of_work,
        enrichment_strategies=_build_enrichment_strategies(ctx),
        merge_threshold=ctx.memory_merge_threshold,
        disambiguate_threshold=ctx.memory_disambiguate_threshold,
    )
    enriched = await asyncio.to_thread(enrich.execute)
    if processed:
        brief_gen = GenerateMemoryBrief(llm=ctx.offline_llm, memory_brief_repo=ctx.offline_memory_brief_repo)
        await brief_gen.execute(datetime.now(UTC))
    print(f"[offline] replayed={replayed} consolidated={processed} enriched={enriched}")


async def _run_offline_pipeline_after_idle(ctx: ServerContext, idle_consolidation_minutes: float) -> None:
    try:
        await asyncio.sleep(idle_consolidation_minutes * 60)
    except asyncio.CancelledError:
        return
    try:
        await _run_offline_pipeline(ctx)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Persona strategy registry
# ---------------------------------------------------------------------------

# AssistantPersona.strategy name -> selection-strategy factory. Binding personas to
# concrete strategy implementations is a composition-root concern, so the registry lives
# here. One entry serves every persona of that class (e.g. all language tutors, any
# target language) — new bundles bind without code changes.
SELECTION_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaSelectionPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorSelectionStrategy(
        memory_repo=ctx.memory_repo,
        persona_repo=ctx.persona_repo,
        embedding_service=ctx.embedding_service,
        focus_interpreter=OllamaFocusInterpreter(model=ctx.llm_model, host=ctx.llm_ollama_host),
    ),
}

# Live, same registry shape (FR-309/TR-314) — but unlike the three above, a persona
# without an entry here does NOT get a no-op: ProcessTurn falls back to
# ctx.default_recall_gate, since recall gating applies to ordinary conversation too,
# not just advanced personas opting in.
RECALL_GATE_FACTORIES: dict[str, Callable[["ServerContext"], RecallGate]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorRecallGate(),
}


# Same registry, offline half: assessment strategies run inside the consolidation
# pipeline and are constructed against the offline_* repos/connection.
ASSESSMENT_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaAssessmentPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorAssessmentStrategy(
        memory_repo=ctx.offline_memory_repo,
        persona_repo=ctx.offline_persona_repo,
        user_repo=ctx.offline_user_repo,
        judge=OllamaPracticeJudge(model=ctx.llm_model, host=ctx.llm_ollama_host),
    ),
}

# Offline half, enrichment: dispatched by EnrichMemory after consolidation, so the
# strategies see freshly written persona_state (e.g. user_initiated salience).
ENRICHMENT_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaEnrichmentPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorEnrichmentStrategy(
        memory_repo=ctx.offline_memory_repo,
        persona_repo=ctx.offline_persona_repo,
        proposer=OllamaClusterProposer(model=ctx.llm_model, host=ctx.llm_ollama_host),
    ),
}


def _build_strategies(ctx: "ServerContext", factories: dict, persona_repo) -> dict[UUID, object]:
    """Resolve each persona's declared strategy name against a registry. Built per
    connection / per offline run so a bundle installed between sessions binds without
    a server restart. Unknown names degrade gracefully: warn, bind nothing."""
    strategies: dict[UUID, object] = {}
    for persona in persona_repo.list_all():
        if persona.strategy is None:
            continue
        factory = factories.get(persona.strategy)
        if factory is None:
            print(
                f"[strategy] persona '{persona.name}' declares unknown strategy "
                f"'{persona.strategy}' — nothing bound from this registry"
            )
            continue
        strategies[persona.id] = factory(ctx)
    return strategies


def _build_selection_strategies(ctx: "ServerContext") -> dict[UUID, PersonaSelectionPort]:
    return _build_strategies(ctx, SELECTION_STRATEGY_FACTORIES, ctx.persona_repo)


def _build_recall_gates(ctx: "ServerContext") -> dict[UUID, RecallGate]:
    return _build_strategies(ctx, RECALL_GATE_FACTORIES, ctx.persona_repo)


def _build_assessment_strategies(ctx: "ServerContext") -> dict[UUID, PersonaAssessmentPort]:
    return _build_strategies(ctx, ASSESSMENT_STRATEGY_FACTORIES, ctx.offline_persona_repo)


def _build_enrichment_strategies(ctx: "ServerContext") -> dict[UUID, PersonaEnrichmentPort]:
    return _build_strategies(ctx, ENRICHMENT_STRATEGY_FACTORIES, ctx.offline_persona_repo)


# ---------------------------------------------------------------------------
# WebSocket handler (one per connection)
# ---------------------------------------------------------------------------


async def _handle(ws, ctx: ServerContext) -> None:
    print("Client connected")

    # A new connection means the previous disconnect's idle window is moot.
    if ctx.idle_timer_task is not None:
        ctx.idle_timer_task.cancel()
        ctx.idle_timer_task = None

    replayed = _replay_sessions(ctx.log_dir, ctx.conversation_repo, ctx.persona_repo)
    if replayed:
        print(f"Replayed {replayed} unprocessed session(s) into the database")

    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)

    start_session = StartSession(
        user_repo=ctx.user_repo,
        persona_repo=ctx.persona_repo,
        memory_brief_repo=ctx.memory_brief_repo,
        session_log_reader=JSONLSessionLogReader(ctx.log_dir),
        memory_repo=ctx.memory_repo,
        session_tail_turns=ctx.session_tail_turns,
    )
    turn_logger = JSONLTurnLogger(ctx.log_dir)

    session = start_session.execute(session_id, started_at)

    process_turn = ProcessTurn(
        stt=ctx.stt,
        llm=ctx.llm,
        tts=ctx.tts,
        embedding_service=ctx.embedding_service,
        memory_repo=ctx.memory_repo,
        default_recall_gate=ctx.default_recall_gate,
        persona_repo=ctx.persona_repo,
        turn_logger=turn_logger,
        language_detector=ctx.language_detector,
        selection_strategies=_build_selection_strategies(ctx),
        recall_gates=_build_recall_gates(ctx),
    )
    end_session = EndSession(turn_logger=turn_logger)
    complete_onboarding = CompleteOnboarding(user_repo=ctx.user_repo)
    edit_persona = EditPersona(persona_repo=ctx.persona_repo)

    if session.needs_onboarding:
        # Only installed languages are offered (FR-002/FR-705) — the primary language
        # must be one whose TTS voice actually exists on this machine.
        supported = [lang.code for lang in ctx.installed_languages]
        await ws.send(json.dumps({"type": "select_language", "supported": supported}))

    audio_buffer = b""
    onboarding_done = not session.needs_onboarding

    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                if onboarding_done:
                    audio_buffer += msg
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")

            if msg_type == "language_selected" and not onboarding_done:
                lang_code = data.get("language", "en")
                lang = Language(lang_code)
                voice = KOKORO_DEFAULT_VOICES.get(lang_code, "af_heart")
                complete_onboarding.execute(session.user, lang)
                session.active_persona = edit_persona.execute(
                    persona_id=session.active_persona.id,
                    now=datetime.now(UTC),
                    voices={DEFAULT_VOICE_ROLE: voice},
                    response_language=lang,
                )
                onboarding_done = True
                print(f"Language selected: {lang_code}, voice: {voice}")
                continue

            if msg_type == "end_utterance" and onboarding_done:
                if not audio_buffer:
                    continue
                audio = audio_buffer
                audio_buffer = b""
                try:
                    result = await process_turn.execute(session, audio, datetime.now(UTC))
                except Exception:
                    # A single turn failing (e.g. TTS error) must not take down the
                    # whole connection/server — log it and let the session continue.
                    traceback.print_exc()
                    result = None
                if result is not None:
                    for chunk in result.audio_chunks:
                        await ws.send(chunk)
                await ws.send(json.dumps({"type": "speaking_end"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        end_session.execute(session, datetime.now(UTC))
        ctx.idle_timer_task = asyncio.create_task(
            _run_offline_pipeline_after_idle(ctx, session.user.idle_consolidation_minutes)
        )
        print("Client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _ensure_user_exists(user_repo: PSUserRepository) -> None:
    """Single-user system with no auth — bootstrap the one User row on first run
    instead of requiring a manual SQL insert before first connect."""
    if user_repo.get() is None:
        user_repo.save(User(id=uuid4()))


def main() -> None:
    # Same fix as the setup wizard (memai_setup.cli): patches ssl.SSLContext to verify
    # against the OS's native trust store instead of certifi's bundled CA list. The live
    # server itself shouldn't need network for model loading (see embedding.py's
    # HF_HUB_OFFLINE guard and the wizard's pre-download steps), but this is cheap
    # insurance against any adapter that still touches the network — e.g. a model the
    # wizard didn't have a chance to pre-download — behind a TLS-inspecting proxy.
    truststore.inject_into_ssl()

    cfg = load_config()

    # Installed languages (FR-705): [languages].installed ∩ SUPPORTED_LANGUAGES, in
    # SUPPORTED_LANGUAGES order. Key absent (config predates it) → everything supported,
    # preserving pre-existing installs' behaviour. A code Memai can't support (e.g. a
    # future wizard offering more than Kokoro covers) is ignored with a warning rather
    # than failing startup.
    installed_languages = resolve_installed_languages(cfg.installed_languages)
    if cfg.installed_languages:
        unsupported = sorted(set(cfg.installed_languages) - {lang.code for lang in SUPPORTED_LANGUAGES})
        if unsupported:
            print(f"[config] ignoring installed languages Memai does not support: {', '.join(unsupported)}")
        if not installed_languages:
            raise RuntimeError(
                "[languages].installed contains no language Memai supports "
                f"(supported: {', '.join(lang.code for lang in SUPPORTED_LANGUAGES)}) — re-run memai-setup"
            )

    print("Connecting to database…")
    conn = postgres.connect(cfg.database_url)
    user_repo = PSUserRepository(conn)
    _ensure_user_exists(user_repo)
    # Separate connection for the background offline pipeline — see ServerContext's
    # offline_* fields docstring for why this can't share `conn`.
    offline_conn = postgres.connect(cfg.database_url)

    print("Loading Whisper model…")
    stt = FasterWhisperSTTService(cfg.stt_model_path, device=cfg.stt_device, compute_type=cfg.stt_compute_type)
    # Live conversation (FR-707/TR-955): local Ollama by default, or any OpenAI-compatible
    # remote endpoint when configured. offline_llm is always a separate, always-Ollama
    # instance (see ServerContext's docstring on it) — even when provider == "ollama",
    # where the two happen to be equivalent, keeping construction uniform is simpler and
    # safer than special-casing "reuse `llm`" for one provider only. Recall gating
    # (FR-309/TR-314) does not branch on provider at all — it's local threshold logic,
    # not an LLM call, so default_recall_gate below is built the same way regardless.
    if cfg.llm_provider == "openai_compatible":
        from .infrastructure.llm import OpenAICompatibleLLMService

        llm: LLMService = OpenAICompatibleLLMService(
            base_url=cfg.llm_base_url, model=cfg.llm_remote_model, api_key=cfg.llm_api_key,
        )
    else:
        llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host)
    offline_llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host)
    default_recall_gate = DefaultRecallGate()
    tts = KokoroTTSService(device=cfg.tts_device)
    print("Loading embedding model…")
    embedding_service = SentenceTransformerEmbeddingService()
    language_detector = Py3LangidLanguageDetector()
    print("Services ready.")

    ctx = ServerContext(
        conn=conn,
        stt=stt,
        llm=llm,
        tts=tts,
        embedding_service=embedding_service,
        language_detector=language_detector,
        log_dir=cfg.log_dir,
        memory_merge_threshold=cfg.memory_merge_threshold,
        memory_disambiguate_threshold=cfg.memory_disambiguate_threshold,
        llm_model=cfg.llm_model,
        llm_ollama_host=cfg.llm_ollama_host,
        offline_llm=offline_llm,
        installed_languages=installed_languages,
        session_tail_turns=cfg.session_tail_turns,
        user_repo=user_repo,
        persona_repo=PSPersonaRepository(conn),
        memory_brief_repo=PSMemoryBriefRepository(conn),
        memory_repo=PSMemoryRepository(conn),
        conversation_repo=PSConversationRepository(conn),
        default_recall_gate=default_recall_gate,
        worthiness_evaluator=OllamaWorthinessEvaluator(model=cfg.llm_model, host=cfg.llm_ollama_host),
        disambiguator=OllamaDisambiguationEvaluator(model=cfg.llm_model, host=cfg.llm_ollama_host),
        synthesizer=OllamaMemorySynthesizer(model=cfg.llm_model, host=cfg.llm_ollama_host),
        extractor=OllamaConsolidationExtractor(model=cfg.llm_model, host=cfg.llm_ollama_host),
        offline_conn=offline_conn,
        offline_user_repo=PSUserRepository(offline_conn),
        offline_persona_repo=PSPersonaRepository(offline_conn),
        offline_memory_brief_repo=PSMemoryBriefRepository(offline_conn),
        offline_memory_repo=PSMemoryRepository(offline_conn),
        offline_conversation_repo=PSConversationRepository(offline_conn),
        offline_unit_of_work=PSUnitOfWork(offline_conn),
    )
    # Idempotent (FR-207) — the "switch back to the general assistant" Directive must
    # exist regardless of which personas have been created; safe to re-run on every
    # startup.
    PersonaDirectiveSync(ctx.memory_repo, ctx.embedding_service).ensure_return_to_general_assistant()

    async def _run() -> None:
        async def handler(ws):
            await _handle(ws, ctx)

        async with websockets.serve(handler, "0.0.0.0", cfg.ws_port, max_size=None):
            print(f"Server listening on :{cfg.ws_port}")
            await asyncio.Future()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
