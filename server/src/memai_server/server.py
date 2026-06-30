# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import asyncio
import json
import uuid
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID

import websockets

from .domain.model import (
    GENERAL_ASSISTANT_ID,
    AssistantPersona,
    Language,
    MemoryBrief,
    SUPPORTED_LANGUAGES,
    User,
)
from .infrastructure.config import load_config, update_voice_config
from .infrastructure.json_file import JSONLSessionLogReader, JSONLTurnLogger
from .infrastructure.llm import OllamaLLMService
from .infrastructure.stt import FasterWhisperSTTService
from .infrastructure.tts import KOKORO_DEFAULT_VOICES, KokoroTTSService
from .services.ports import MemoryItem
from .services.session import EndSession, ProcessTurn, StartSession

# ---------------------------------------------------------------------------
# In-memory stubs (Pass 1 — no DB)
# ---------------------------------------------------------------------------


class _UserRepo:
    def __init__(self, language: Language | None) -> None:
        self._user = User(id=UUID("00000000-0000-0000-0000-000000000010"), primary_language=language)

    def get(self) -> User | None:
        return self._user

    def save(self, user: User) -> None:
        self._user = user


class _PersonaRepo:
    def __init__(self) -> None:
        self._persona = AssistantPersona.general_assistant(
            system_prompt=(
                "You are a helpful voice assistant. "
                "Keep responses concise and natural — one or two sentences. "
                "Your text is spoken aloud verbatim — never use markdown formatting "
                "(no **bold**, no _italics_, no bullet points, no headers, no code blocks). "
                "If the user asks what you can do, how to configure you, or asks to hear your "
                "introduction again, deliver the onboarding introduction."
            ),
        )

    def get(self, persona_id: UUID) -> AssistantPersona | None:
        return self._persona if persona_id == GENERAL_ASSISTANT_ID else None

    def list_all(self) -> list[AssistantPersona]:
        return [self._persona]

    def save(self, persona: AssistantPersona) -> None:
        self._persona = persona

    def delete(self, persona_id: UUID) -> None:
        pass


class _MemoryBriefRepo:
    def get(self) -> MemoryBrief | None:
        return None

    def save(self, brief: MemoryBrief) -> None:
        pass


class _MemoryRepo:
    def upsert_episode(self, episode) -> int:
        return 0

    def upsert_concept(self, concept) -> int:
        return 0

    def upsert_procedure(self, procedure) -> int:
        return 0

    def search(self, embedding, memory_types, top_n, persona_id=None) -> list[tuple[float, MemoryItem]]:
        return []


class _NullRecallDetector:
    def detect(self, text: str):
        return None


class _NullEmbeddingService:
    def embed(self, text: str) -> list[float]:
        return []


# ---------------------------------------------------------------------------
# WebSocket handler (one per connection)
# ---------------------------------------------------------------------------


async def _handle(
    ws,
    stt: FasterWhisperSTTService,
    llm: OllamaLLMService,
    tts: KokoroTTSService,
    log_dir: Path,
    primary_language: Language | None,
) -> None:
    print("Client connected")

    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)

    user_repo = _UserRepo(primary_language)
    persona_repo = _PersonaRepo()

    start_session = StartSession(
        user_repo=user_repo,
        persona_repo=persona_repo,
        memory_brief_repo=_MemoryBriefRepo(),
        session_log_reader=JSONLSessionLogReader(log_dir),
    )
    turn_logger = JSONLTurnLogger(log_dir)

    session = start_session.execute(session_id, started_at)

    process_turn = ProcessTurn(
        stt=stt,
        llm=llm,
        tts=tts,
        embedding_service=_NullEmbeddingService(),
        memory_repo=_MemoryRepo(),
        recall_detector=_NullRecallDetector(),
        persona_repo=persona_repo,
        turn_logger=turn_logger,
    )
    end_session = EndSession(turn_logger=turn_logger)

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
                update_voice_config("primary_language", lang_code)
                user_repo.save(User(id=session.user.id, primary_language=lang))
                session.user.primary_language = lang
                session.active_persona.tts_voice = voice
                session.active_persona.response_language = lang
                onboarding_done = True
                print(f"Language selected: {lang_code}, voice: {voice}")
                continue

            if msg_type == "end_utterance" and onboarding_done:
                if not audio_buffer:
                    continue
                audio = audio_buffer
                audio_buffer = b""
                result = await process_turn.execute(session, audio, datetime.now(UTC))
                if result is not None:
                    for chunk in result.audio_chunks:
                        await ws.send(chunk)
                await ws.send(json.dumps({"type": "speaking_end"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        end_session.execute(session, datetime.now(UTC))
        print("Client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    cfg = load_config()

    print("Loading Whisper model…")
    stt = FasterWhisperSTTService(cfg.stt_model_path, device=cfg.stt_device, compute_type=cfg.stt_compute_type)
    llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host)
    tts = KokoroTTSService()
    print("Services ready.")

    async def _run() -> None:
        async def handler(ws):
            await _handle(ws, stt, llm, tts, cfg.log_dir, cfg.primary_language)

        async with websockets.serve(handler, "0.0.0.0", cfg.ws_port):
            print(f"Server listening on :{cfg.ws_port}")
            await asyncio.Future()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
