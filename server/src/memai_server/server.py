# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import json
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

import psycopg
import websockets

from .domain.model import DEFAULT_VOICE_ROLE, Language, SUPPORTED_LANGUAGES, User
from .infrastructure import postgres
from .infrastructure.config import load_config
from .infrastructure.embedding import SentenceTransformerEmbeddingService
from .infrastructure.json_file import JSONLSessionLogReader, JSONLSessionReplayReader, JSONLTurnLogger
from .infrastructure.llm import (
    OllamaConsolidationExtractor,
    OllamaDisambiguationEvaluator,
    OllamaLLMService,
    OllamaMemorySynthesizer,
    OllamaRecallIntentDetector,
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
from .infrastructure.stt import FasterWhisperSTTService
from .infrastructure.tts import KOKORO_DEFAULT_VOICES, KokoroTTSService
from .services.memory import ConsolidateMemory, GenerateMemoryBrief
from .services.persona import EditPersona
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
    llm: OllamaLLMService
    tts: KokoroTTSService
    embedding_service: SentenceTransformerEmbeddingService
    log_dir: Path
    memory_merge_threshold: float
    memory_disambiguate_threshold: float
    user_repo: PSUserRepository
    persona_repo: PSPersonaRepository
    memory_brief_repo: PSMemoryBriefRepository
    memory_repo: PSMemoryRepository
    conversation_repo: PSConversationRepository
    recall_detector: OllamaRecallIntentDetector
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
        # No assessment strategies registered yet — the first concrete one is the
        # language tutor's (Phase 12); GA assesses nothing.
        merge_threshold=ctx.memory_merge_threshold,
        disambiguate_threshold=ctx.memory_disambiguate_threshold,
    )
    processed = await asyncio.to_thread(consolidate.execute)
    if processed:
        brief_gen = GenerateMemoryBrief(llm=ctx.llm, memory_brief_repo=ctx.offline_memory_brief_repo)
        await brief_gen.execute(datetime.now(UTC))
    print(f"[offline] replayed={replayed} consolidated={processed}")


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
    )
    turn_logger = JSONLTurnLogger(ctx.log_dir)

    session = start_session.execute(session_id, started_at)

    process_turn = ProcessTurn(
        stt=ctx.stt,
        llm=ctx.llm,
        tts=ctx.tts,
        embedding_service=ctx.embedding_service,
        memory_repo=ctx.memory_repo,
        recall_detector=ctx.recall_detector,
        persona_repo=ctx.persona_repo,
        turn_logger=turn_logger,
    )
    end_session = EndSession(turn_logger=turn_logger)
    complete_onboarding = CompleteOnboarding(user_repo=ctx.user_repo)
    edit_persona = EditPersona(persona_repo=ctx.persona_repo)

    if session.needs_onboarding:
        supported = [lang.code for lang in SUPPORTED_LANGUAGES]
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
    cfg = load_config()

    print("Connecting to database…")
    conn = postgres.connect(cfg.database_url)
    user_repo = PSUserRepository(conn)
    _ensure_user_exists(user_repo)
    # Separate connection for the background offline pipeline — see ServerContext's
    # offline_* fields docstring for why this can't share `conn`.
    offline_conn = postgres.connect(cfg.database_url)

    print("Loading Whisper model…")
    stt = FasterWhisperSTTService(cfg.stt_model_path, device=cfg.stt_device, compute_type=cfg.stt_compute_type)
    llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host)
    tts = KokoroTTSService(device=cfg.tts_device)
    print("Loading embedding model…")
    embedding_service = SentenceTransformerEmbeddingService()
    print("Services ready.")

    ctx = ServerContext(
        conn=conn,
        stt=stt,
        llm=llm,
        tts=tts,
        embedding_service=embedding_service,
        log_dir=cfg.log_dir,
        memory_merge_threshold=cfg.memory_merge_threshold,
        memory_disambiguate_threshold=cfg.memory_disambiguate_threshold,
        user_repo=user_repo,
        persona_repo=PSPersonaRepository(conn),
        memory_brief_repo=PSMemoryBriefRepository(conn),
        memory_repo=PSMemoryRepository(conn),
        conversation_repo=PSConversationRepository(conn),
        recall_detector=OllamaRecallIntentDetector(model=cfg.llm_model, host=cfg.llm_ollama_host),
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

    async def _run() -> None:
        async def handler(ws):
            await _handle(ws, ctx)

        async with websockets.serve(handler, "0.0.0.0", cfg.ws_port, max_size=None):
            print(f"Server listening on :{cfg.ws_port}")
            await asyncio.Future()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
