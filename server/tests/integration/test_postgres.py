# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Integration tests against a real Postgres + pgvector instance. Requires the `pg_conn`
fixture (see conftest.py) — skipped automatically when Postgres isn't reachable."""
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from memai_server.domain.model import (
    AssistantPersona,
    Concept,
    Conversation,
    Episode,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Speaker,
    Turn,
    User,
)
from memai_server.infrastructure.postgres import (
    PSConversationRepository,
    PSMemoryBriefRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)

_NOW = datetime.now(UTC)
_DIM = 1024


def _vec(seed: float) -> list[float]:
    """A deterministic 1024-dim vector, mostly zero with one distinguishing component —
    enough to exercise pgvector storage/ordering without needing real embeddings."""
    v = [0.0] * _DIM
    v[0] = seed
    v[1] = 1.0 - abs(seed)
    return v


def _persona(**overrides) -> AssistantPersona:
    defaults = dict(
        id=uuid4(),
        name="Test Persona",
        system_prompt="You are a test persona.",
        languages=[Language("en")],
        response_language=Language("en"),
        voices={"default": "af_heart"},
        is_system=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return AssistantPersona(**defaults)


class TestPSUserRepository:
    def test_get_returns_none_when_no_user(self, pg_conn: psycopg.Connection) -> None:
        assert PSUserRepository(pg_conn).get() is None

    def test_save_and_get_round_trip(self, pg_conn: psycopg.Connection) -> None:
        repo = PSUserRepository(pg_conn)
        user = User(
            id=uuid4(),
            primary_language=Language("fr"),
            secondary_languages=[Language("en")],
            idle_consolidation_minutes=7.5,
        )
        repo.save(user)
        loaded = repo.get()
        assert loaded.id == user.id
        assert loaded.primary_language == Language("fr")
        assert loaded.secondary_languages == [Language("en")]
        assert loaded.idle_consolidation_minutes == 7.5

    def test_save_upserts_existing_user(self, pg_conn: psycopg.Connection) -> None:
        repo = PSUserRepository(pg_conn)
        user = User(id=uuid4(), primary_language=Language("en"))
        repo.save(user)
        user.update_idle_consolidation_minutes(2.0)
        repo.save(user)
        assert repo.get().idle_consolidation_minutes == 2.0


class TestPSPersonaRepository:
    def test_save_and_get_round_trip(self, pg_conn: psycopg.Connection) -> None:
        repo = PSPersonaRepository(pg_conn)
        persona = _persona(speaking_rate=0.8, is_active=True)
        repo.save(persona)
        loaded = repo.get(persona.id)
        assert loaded.name == persona.name
        assert loaded.speaking_rate == 0.8
        assert loaded.is_active is True

    def test_list_all_returns_all_personas(self, pg_conn: psycopg.Connection) -> None:
        repo = PSPersonaRepository(pg_conn)
        repo.save(_persona())
        repo.save(_persona())
        assert len(repo.list_all()) == 2

    def test_deactivate_reactivate_round_trip(self, pg_conn: psycopg.Connection) -> None:
        repo = PSPersonaRepository(pg_conn)
        persona = _persona()
        repo.save(persona)
        persona.deactivate(updated_at=_NOW)
        repo.save(persona)
        assert repo.get(persona.id).is_active is False
        persona.reactivate(updated_at=_NOW)
        repo.save(persona)
        assert repo.get(persona.id).is_active is True

    def test_delete_removes_persona_with_no_history(self, pg_conn: psycopg.Connection) -> None:
        repo = PSPersonaRepository(pg_conn)
        persona = _persona()
        repo.save(persona)
        repo.delete(persona.id)
        assert repo.get(persona.id) is None

    def test_voices_map_round_trip(self, pg_conn: psycopg.Connection) -> None:
        """Phase 10: voices is a JSONB speaker-role map (two-teacher cast is Phase 12,
        but the multi-entry shape must round-trip already)."""
        repo = PSPersonaRepository(pg_conn)
        persona = _persona(voices={"default": "ff_siwis", "target_teacher": "ef_dora"})
        repo.save(persona)
        loaded = repo.get(persona.id)
        assert loaded.voices == {"default": "ff_siwis", "target_teacher": "ef_dora"}
        assert loaded.default_voice == "ff_siwis"


class TestPSConversationRepository:
    def test_save_new_persists_conversation_and_turns(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)

        conversation = Conversation(
            id=None,
            started_at=_NOW,
            persona_id=persona.id,
            turns=[Turn(timestamp=_NOW, speaker=Speaker.USER, content="hello", language=Language("en"))],
            ended_at=_NOW,
        )
        conv_id = conv_repo.save_new(conversation, session_id=uuid4())

        [loaded] = conv_repo.get_unconsolidated()
        assert loaded.id == conv_id
        assert loaded.persona_id == persona.id
        assert len(loaded.turns) == 1
        assert loaded.turns[0].content == "hello"

    def test_get_unconsolidated_orders_oldest_first(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)

        older = Conversation(id=None, started_at=datetime(2026, 1, 1, tzinfo=UTC), persona_id=persona.id)
        newer = Conversation(id=None, started_at=datetime(2026, 6, 1, tzinfo=UTC), persona_id=persona.id)
        newer_id = conv_repo.save_new(newer, session_id=uuid4())
        older_id = conv_repo.save_new(older, session_id=uuid4())

        ordered_ids = [c.id for c in conv_repo.get_unconsolidated()]
        assert ordered_ids == [older_id, newer_id]

    def test_save_consolidation_sets_flag_and_excludes_from_unconsolidated(
        self, pg_conn: psycopg.Connection
    ) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        turn = Turn(timestamp=_NOW, speaker=Speaker.USER, content="hi", language=Language("en"))
        conversation = Conversation(id=None, started_at=_NOW, persona_id=persona.id, turns=[turn], ended_at=_NOW)
        conv_id = conv_repo.save_new(conversation, session_id=uuid4())

        conversation.id = conv_id
        conversation.mark_consolidated(worthiness=True, summary="a summary")
        conv_repo.save_consolidation(conversation)

        assert conv_repo.get_unconsolidated() == []

    def test_is_session_persisted(self, pg_conn: psycopg.Connection) -> None:
        """is_session_persisted checks the `turns` table (TurnLogReplayer's idempotency
        check), so a conversation needs at least one turn to register a session_id."""
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        session_id = uuid4()
        assert conv_repo.is_session_persisted(session_id) is False
        turn = Turn(timestamp=_NOW, speaker=Speaker.USER, content="hi", language=Language("en"))
        conv_repo.save_new(Conversation(id=None, started_at=_NOW, persona_id=persona.id, turns=[turn]), session_id=session_id)
        assert conv_repo.is_session_persisted(session_id) is True

    def test_extend_conversation_appends_turns_and_updates_ended_at(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        conv_id = conv_repo.save_new(Conversation(id=None, started_at=_NOW, persona_id=persona.id), session_id=uuid4())

        new_turn = Turn(timestamp=_NOW, speaker=Speaker.ASSISTANT, content="continuing", language=Language("en"))
        conv_repo.extend_conversation(conv_id, session_id=uuid4(), turns=[new_turn], ended_at=_NOW)

        [loaded] = conv_repo.get_unconsolidated()
        assert loaded.ended_at == _NOW
        assert len(loaded.turns) == 1

    def test_get_last_open_id_returns_most_recent_unconsolidated(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        conv_repo.save_new(Conversation(id=None, started_at=datetime(2026, 1, 1, tzinfo=UTC), persona_id=persona.id), session_id=uuid4())
        newest_id = conv_repo.save_new(
            Conversation(id=None, started_at=datetime(2026, 6, 1, tzinfo=UTC), persona_id=persona.id), session_id=uuid4()
        )
        assert conv_repo.get_last_open_id() == newest_id

    def test_persona_delete_restricted_once_referenced_by_a_conversation(self, pg_conn: psycopg.Connection) -> None:
        """CLAUDE.md/PLAN.md Phase 8: conversations.persona_id is ON DELETE RESTRICT —
        session logs are kept forever, so a persona with real history can't be hard-deleted,
        only deactivated (see DeactivatePersona)."""
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        conv_repo.save_new(Conversation(id=None, started_at=_NOW, persona_id=persona.id), session_id=uuid4())

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            persona_repo.delete(persona.id)


class TestPSMemoryRepository:
    def test_upsert_episode_insert_then_update(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        conv_repo = PSConversationRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        conv_id = conv_repo.save_new(Conversation(id=None, started_at=_NOW, persona_id=persona.id), session_id=uuid4())

        episode = Episode(id=None, summary="Went hiking", happened_at=_NOW, origin_conversation_id=conv_id, embedding=_vec(0.5))
        episode_id = memory_repo.upsert_episode(episode)
        assert episode_id is not None

        episode.id = episode_id
        episode.summary = "Went hiking in the Alps"
        memory_repo.upsert_episode(episode)

        [(_, found)] = memory_repo.search(embedding=_vec(0.5), memory_types=(MemoryType.EPISODE,), top_n=1)
        assert found.summary == "Went hiking in the Alps"

    def test_upsert_concept_and_persona_scoped_search(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona_a = _persona()
        persona_b = _persona()
        persona_repo.save(persona_a)
        persona_repo.save(persona_b)

        # Same name, different persona context — CLAUDE.md's "big bang" astronomy-vs-pop-culture example.
        memory_repo.upsert_concept(
            Concept(id=None, persona_id=persona_a.id, name="big bang", description="cosmology", language=Language("en"), embedding=_vec(0.9))
        )
        memory_repo.upsert_concept(
            Concept(id=None, persona_id=persona_b.id, name="big bang", description="sitcom", language=Language("en"), embedding=_vec(0.9))
        )

        results = memory_repo.search(embedding=_vec(0.9), memory_types=(MemoryType.CONCEPT,), top_n=10, persona_id=persona_a.id)
        assert len(results) == 1
        assert results[0][1].description == "cosmology"

    def test_upsert_procedure_round_trip(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)

        procedure = Procedure(
            id=None, persona_id=persona.id, name="make tea", description="Boil water, steep.",
            language=Language("en"), steps=["Boil water", "Add tea", "Steep 3 min"], embedding=_vec(0.2),
        )
        proc_id = memory_repo.upsert_procedure(procedure)
        [(_, found)] = memory_repo.search(embedding=_vec(0.2), memory_types=(MemoryType.PROCEDURE,), top_n=1, persona_id=persona.id)
        assert found.id == proc_id
        assert found.steps == ["Boil water", "Add tea", "Steep 3 min"]

    def test_search_top_n_limits_results(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        for i in range(5):
            memory_repo.upsert_concept(
                Concept(id=None, persona_id=persona.id, name=f"c{i}", description="d", language=Language("en"), embedding=_vec(i / 10))
            )
        results = memory_repo.search(embedding=_vec(0.0), memory_types=(MemoryType.CONCEPT,), top_n=2, persona_id=persona.id)
        assert len(results) == 2

    def test_search_identical_vector_has_similarity_near_one(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        memory_repo.upsert_concept(
            Concept(id=None, persona_id=persona.id, name="c", description="d", language=Language("en"), embedding=_vec(0.7))
        )
        [(similarity, _)] = memory_repo.search(embedding=_vec(0.7), memory_types=(MemoryType.CONCEPT,), top_n=1, persona_id=persona.id)
        assert similarity == pytest.approx(1.0, abs=1e-4)

    def test_category_and_persona_state_round_trip(self, pg_conn: psycopg.Connection) -> None:
        """Phase 10: category round-trips through upsert INSERT/UPDATE; persona_state is
        written only via update_persona_state and never clobbered by a later upsert."""
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)

        concept = Concept(
            id=None, persona_id=persona.id, name="ser vs estar", description="d",
            language=Language("es"), category="contrast_pair", embedding=_vec(0.3),
        )
        concept_id = memory_repo.upsert_concept(concept)

        state = {"last_practiced_at": "2026-07-10", "half_life_days": 3.5, "retrievals": 2}
        memory_repo.update_persona_state(MemoryType.CONCEPT, concept_id, state)

        [(_, found)] = memory_repo.search(embedding=_vec(0.3), memory_types=(MemoryType.CONCEPT,), top_n=1, persona_id=persona.id)
        assert found.category == "contrast_pair"
        assert found.persona_state == state

        # A subsequent upsert (merge path: extraction drafts carry persona_state=None)
        # must not wipe the assessment strategy's state.
        concept.id = concept_id
        concept.description = "enriched description"
        concept.persona_state = None
        memory_repo.upsert_concept(concept)

        [(_, found)] = memory_repo.search(embedding=_vec(0.3), memory_types=(MemoryType.CONCEPT,), top_n=1, persona_id=persona.id)
        assert found.description == "enriched description"
        assert found.persona_state == state

    def test_update_persona_state_rejects_episodes(self, pg_conn: psycopg.Connection) -> None:
        memory_repo = PSMemoryRepository(pg_conn)
        with pytest.raises(ValueError, match="episode"):
            memory_repo.update_persona_state(MemoryType.EPISODE, 1, {"x": 1})

    def test_procedure_category_and_persona_state_round_trip(self, pg_conn: psycopg.Connection) -> None:
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)

        procedure = Procedure(
            id=None, persona_id=persona.id, name="-er conjugation", description="d",
            language=Language("fr"), category="morphological_pattern", embedding=_vec(0.4),
        )
        proc_id = memory_repo.upsert_procedure(procedure)
        memory_repo.update_persona_state(MemoryType.PROCEDURE, proc_id, {"errors": 1})

        [(_, found)] = memory_repo.search(embedding=_vec(0.4), memory_types=(MemoryType.PROCEDURE,), top_n=1, persona_id=persona.id)
        assert found.category == "morphological_pattern"
        assert found.persona_state == {"errors": 1}

    def test_persona_delete_cascades_to_concepts_and_procedures(self, pg_conn: psycopg.Connection) -> None:
        """CLAUDE.md: cascade delete is intentional for Concept/Procedure — deleting a
        persona removes all its concepts/procedures (unlike conversations, which RESTRICT)."""
        persona_repo = PSPersonaRepository(pg_conn)
        memory_repo = PSMemoryRepository(pg_conn)
        persona = _persona()
        persona_repo.save(persona)
        memory_repo.upsert_concept(
            Concept(id=None, persona_id=persona.id, name="c", description="d", language=Language("en"), embedding=_vec(0.1))
        )
        memory_repo.upsert_procedure(
            Procedure(id=None, persona_id=persona.id, name="p", description="d", language=Language("en"), embedding=_vec(0.1))
        )

        persona_repo.delete(persona.id)

        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM concepts")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM procedures")
            assert cur.fetchone()[0] == 0


class TestPSMemoryBriefRepository:
    def test_get_returns_none_when_absent(self, pg_conn: psycopg.Connection) -> None:
        assert PSMemoryBriefRepository(pg_conn).get() is None

    def test_save_then_overwrite_singleton(self, pg_conn: psycopg.Connection) -> None:
        repo = PSMemoryBriefRepository(pg_conn)
        repo.save(MemoryBrief(content="first", created_at=_NOW, updated_at=_NOW))
        repo.save(MemoryBrief(content="second", created_at=_NOW, updated_at=_NOW))
        assert repo.get().content == "second"
        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memory_brief")
            assert cur.fetchone()[0] == 1


class TestPSUnitOfWork:
    def test_commits_writes_on_clean_exit(self, pg_conn: psycopg.Connection) -> None:
        persona = _persona()
        with PSUnitOfWork(pg_conn):
            PSPersonaRepository(pg_conn).save(persona)
        assert PSPersonaRepository(pg_conn).get(persona.id) is not None

    def test_rolls_back_writes_on_exception(self, pg_conn: psycopg.Connection) -> None:
        persona = _persona()
        with pytest.raises(RuntimeError):
            with PSUnitOfWork(pg_conn):
                PSPersonaRepository(pg_conn).save(persona)
                raise RuntimeError("simulated failure mid-transaction")
        assert PSPersonaRepository(pg_conn).get(persona.id) is None
