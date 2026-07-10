# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Phase 5 end-to-end test: disconnect → replay → consolidate → verify DB state.
Real Postgres, real JSONL files, real TurnLogReplayer/ConsolidateMemory/PSUnitOfWork —
only the LLM-dependent ports (extraction, worthiness, disambiguation, synthesis) are
Fakes, matching how the rest of this project draws the Fakes-vs-real-adapters line
(those decisions are already unit-tested at the service layer in isolation)."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg

from memai_server.domain.model import (
    GENERAL_ASSISTANT_ID,
    AssistantPersona,
    Concept,
    Episode,
    Language,
    MemoryType,
    Speaker,
    Turn,
    User,
)
from memai_server.infrastructure.json_file import JSONLSessionReplayReader, JSONLTurnLogger
from memai_server.infrastructure.postgres import (
    PSConversationRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
)
from memai_server.services.memory import ConsolidateMemory
from memai_server.services.ports import ExtractionResult
from memai_server.services.replay import TurnLogReplayer

from tests.fakes.fakes import (
    FakeConsolidationExtractor,
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemorySynthesizer,
    FakeUserRepository,
    FakeWorthinessEvaluator,
)

_NOW = datetime.now(UTC)
_EMBEDDING_DIM = 1024  # must match the schema's vector(1024) columns


def _seed_general_assistant(pg_conn: psycopg.Connection) -> AssistantPersona:
    persona = AssistantPersona.general_assistant(system_prompt="You are a helpful assistant.")
    PSPersonaRepository(pg_conn).save(persona)
    return persona


def _write_session(log_dir, session_id, persona_id) -> None:
    logger = JSONLTurnLogger(log_dir)
    logger.append(
        session_id,
        Turn(timestamp=_NOW, speaker=Speaker.USER, content="I love hiking in the Alps.", language=Language("en")),
    )
    logger.append(
        session_id,
        Turn(
            timestamp=_NOW + timedelta(seconds=1),
            speaker=Speaker.ASSISTANT,
            content="That sounds wonderful!",
            language=Language("en"),
        ),
        persona_id=persona_id,
    )
    logger.close(session_id, ended_at=_NOW + timedelta(seconds=2), clean_exit=True)


def test_disconnect_replay_consolidate_updates_db_state(pg_conn: psycopg.Connection, tmp_path) -> None:
    persona = _seed_general_assistant(pg_conn)
    conv_repo = PSConversationRepository(pg_conn)
    persona_repo = PSPersonaRepository(pg_conn)
    memory_repo = PSMemoryRepository(pg_conn)

    log_dir = tmp_path / "sessions"
    session_id = uuid4()
    _write_session(log_dir, session_id, persona.id)

    # --- Replay: JSONL session file -> unconsolidated Conversation in Postgres ---
    replayer = TurnLogReplayer(JSONLSessionReplayReader(log_dir), conv_repo, persona_repo)
    replayed = replayer.execute()
    assert replayed == 1

    [conversation] = conv_repo.get_unconsolidated()
    assert conversation.persona_id == GENERAL_ASSISTANT_ID
    assert len(conversation.turns) == 2

    # --- Consolidate: real DB, real transaction, Fake LLM-dependent ports ---
    extraction = ExtractionResult(
        episodes=[
            Episode(
                id=None,
                summary="User went hiking in the Alps.",
                happened_at=_NOW,
                origin_conversation_id=conversation.id,
            )
        ],
        concepts=[
            Concept(
                id=None,
                persona_id=GENERAL_ASSISTANT_ID,
                name="hiking",
                description="The user enjoys hiking in the Alps.",
                language=Language("en"),
            )
        ],
        procedures=[],
    )
    consolidate = ConsolidateMemory(
        conversation_repo=conv_repo,
        memory_repo=memory_repo,
        embedding_service=FakeEmbeddingService(vector=[0.1] * _EMBEDDING_DIM),
        extractor=FakeConsolidationExtractor(result=extraction),
        worthiness_evaluator=FakeWorthinessEvaluator(worthy=True),
        disambiguator=FakeDisambiguationEvaluator(),
        synthesizer=FakeMemorySynthesizer(),
        unit_of_work=PSUnitOfWork(pg_conn),
        user_repo=FakeUserRepository(User(id=uuid4(), primary_language=Language("en"))),
    )
    processed = consolidate.execute()
    assert processed == 1

    # --- Verify DB state: consolidated flag, worthiness, and actual memory rows ---
    assert conv_repo.get_unconsolidated() == []
    with pg_conn.cursor() as cur:
        cur.execute("SELECT consolidated, worthiness FROM conversations WHERE id = %s", (conversation.id,))
        consolidated, worthiness = cur.fetchone()
        assert consolidated is True
        assert worthiness is True

    [(_, found_episode)] = memory_repo.search(
        embedding=[0.1] * _EMBEDDING_DIM, memory_types=(MemoryType.EPISODE,), top_n=1
    )
    assert found_episode.summary == "User went hiking in the Alps."
    assert found_episode.origin_conversation_id == conversation.id

    [(_, found_concept)] = memory_repo.search(
        embedding=[0.1] * _EMBEDDING_DIM,
        memory_types=(MemoryType.CONCEPT,),
        top_n=1,
        persona_id=GENERAL_ASSISTANT_ID,
    )
    assert found_concept.name == "hiking"


def test_unworthy_conversation_still_extracts_concepts_but_no_episodes(
    pg_conn: psycopg.Connection, tmp_path
) -> None:
    """CLAUDE.md/PLAN.md: Concepts/Procedures are extracted unconditionally; Episodes
    require a worthy conversation. Real regression coverage for that split, against a
    real DB — the unit-tested version of this used Fakes throughout."""
    persona = _seed_general_assistant(pg_conn)
    conv_repo = PSConversationRepository(pg_conn)
    persona_repo = PSPersonaRepository(pg_conn)
    memory_repo = PSMemoryRepository(pg_conn)

    log_dir = tmp_path / "sessions"
    session_id = uuid4()
    _write_session(log_dir, session_id, persona.id)

    replayer = TurnLogReplayer(JSONLSessionReplayReader(log_dir), conv_repo, persona_repo)
    replayer.execute()
    [conversation] = conv_repo.get_unconsolidated()

    extraction = ExtractionResult(
        episodes=[Episode(id=None, summary="Trivial exchange.", happened_at=_NOW, origin_conversation_id=conversation.id)],
        concepts=[
            Concept(id=None, persona_id=GENERAL_ASSISTANT_ID, name="hiking", description="d", language=Language("en"))
        ],
        procedures=[],
    )
    consolidate = ConsolidateMemory(
        conversation_repo=conv_repo,
        memory_repo=memory_repo,
        embedding_service=FakeEmbeddingService(vector=[0.1] * _EMBEDDING_DIM),
        extractor=FakeConsolidationExtractor(result=extraction),
        worthiness_evaluator=FakeWorthinessEvaluator(worthy=False),
        disambiguator=FakeDisambiguationEvaluator(),
        synthesizer=FakeMemorySynthesizer(),
        unit_of_work=PSUnitOfWork(pg_conn),
        user_repo=FakeUserRepository(User(id=uuid4(), primary_language=Language("en"))),
    )
    consolidate.execute()

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM episodes")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM concepts")
        assert cur.fetchone()[0] == 1
