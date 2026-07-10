# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""
PostgreSQL repository implementations (psycopg 3 + pgvector).
Call connect(dsn) to get a connection with the vector type already registered,
then pass it to each repository constructor.
"""
from __future__ import annotations

from datetime import datetime, UTC
from uuid import UUID

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from ..domain.model import (
    AssistantPersona,
    Concept,
    Conversation,
    EngagementLevel,
    Episode,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Speaker,
    Turn,
    User,
)
from ..services.ports import MemoryItem


def connect(dsn: str) -> psycopg.Connection:
    """Autocommit — single-user, single-connection process (see CLAUDE.md); no
    cross-statement transaction boundaries needed for most calls. Callers that need
    atomicity across several writes (e.g. ConsolidateMemory, one conversation at a
    time) use PSUnitOfWork, which wraps this same connection in `conn.transaction()`."""
    conn = psycopg.connect(dsn, autocommit=True)
    register_vector(conn)
    return conn


class PSUnitOfWork:
    """Wraps a single conversation's consolidation writes in one transaction on top of
    an autocommit connection — psycopg's `transaction()` block does this correctly even
    when autocommit is on, committing on clean exit and rolling back on exception."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._txn: psycopg.Transaction | None = None

    def __enter__(self) -> "PSUnitOfWork":
        self._txn = self._conn.transaction()
        self._txn.__enter__()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        assert self._txn is not None
        self._txn.__exit__(exc_type, exc_value, traceback)
        self._txn = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_persona(row: tuple) -> AssistantPersona:
    (id_, name, system_prompt, languages, response_language, voices, is_system,
     created_at, updated_at, speaking_rate, is_active) = row
    return AssistantPersona(
        id=id_,
        name=name,
        system_prompt=system_prompt,
        languages=[Language(c) for c in languages],
        response_language=Language(response_language),
        voices=voices,
        is_system=is_system,
        created_at=created_at,
        updated_at=updated_at,
        speaking_rate=speaking_rate,
        is_active=is_active,
    )


def _vec(v: list[float] | None) -> np.ndarray | None:
    return np.array(v, dtype=np.float32) if v is not None else None


def _list(v: np.ndarray | None) -> list[float] | None:
    return v.tolist() if v is not None else None


# ---------------------------------------------------------------------------
# PSUserRepository
# ---------------------------------------------------------------------------

class PSUserRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self) -> User | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, primary_language, secondary_languages, idle_consolidation_minutes FROM users LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            id_, primary_lang, secondary_langs, idle_consolidation_minutes = row
            return User(
                id=id_,
                primary_language=Language(primary_lang) if primary_lang else None,
                secondary_languages=[Language(c) for c in (secondary_langs or [])],
                idle_consolidation_minutes=idle_consolidation_minutes,
            )

    def save(self, user: User) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, primary_language, secondary_languages, idle_consolidation_minutes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    primary_language = EXCLUDED.primary_language,
                    secondary_languages = EXCLUDED.secondary_languages,
                    idle_consolidation_minutes = EXCLUDED.idle_consolidation_minutes
                """,
                (
                    user.id,
                    user.primary_language.code if user.primary_language else None,
                    [l.code for l in user.secondary_languages],
                    user.idle_consolidation_minutes,
                ),
            )


# ---------------------------------------------------------------------------
# PSPersonaRepository
# ---------------------------------------------------------------------------

class PSPersonaRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self, persona_id: UUID) -> AssistantPersona | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, system_prompt, languages, response_language, voices, is_system, "
                "created_at, updated_at, speaking_rate, is_active "
                "FROM personas WHERE id = %s",
                (persona_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_persona(row)

    def list_all(self) -> list[AssistantPersona]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, system_prompt, languages, response_language, voices, is_system, "
                "created_at, updated_at, speaking_rate, is_active FROM personas"
            )
            return [_row_to_persona(row) for row in cur.fetchall()]

    def save(self, persona: AssistantPersona) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personas
                    (id, name, system_prompt, languages, response_language, voices, is_system,
                     created_at, updated_at, speaking_rate, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    system_prompt = EXCLUDED.system_prompt,
                    languages = EXCLUDED.languages,
                    response_language = EXCLUDED.response_language,
                    voices = EXCLUDED.voices,
                    updated_at = EXCLUDED.updated_at,
                    speaking_rate = EXCLUDED.speaking_rate,
                    is_active = EXCLUDED.is_active
                """,
                (
                    persona.id,
                    persona.name,
                    persona.system_prompt,
                    [l.code for l in persona.languages],
                    persona.response_language.code,
                    Jsonb(persona.voices),
                    persona.is_system,
                    persona.created_at,
                    persona.updated_at,
                    persona.speaking_rate,
                    persona.is_active,
                ),
            )

    def delete(self, persona_id: UUID) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM personas WHERE id = %s", (persona_id,))


# ---------------------------------------------------------------------------
# PSConversationRepository
# ---------------------------------------------------------------------------

class PSConversationRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_new(self, conversation: Conversation, session_id: UUID) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversations (started_at, ended_at, persona_id, consolidated)
                VALUES (%s, %s, %s, FALSE)
                RETURNING id
                """,
                (conversation.started_at, conversation.ended_at, conversation.persona_id),
            )
            new_id = cur.fetchone()[0]
            for turn in conversation.turns:
                cur.execute(
                    """
                    INSERT INTO turns (conversation_id, session_id, timestamp, speaker, content, language)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_id,
                        session_id,
                        turn.timestamp,
                        turn.speaker.value,
                        turn.content,
                        turn.language.code if turn.language else None,
                    ),
                )
        return new_id

    def save_consolidation(self, conversation: Conversation) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE conversations
                SET worthiness = %s, summary = %s, consolidated = TRUE
                WHERE id = %s
                """,
                (conversation.worthiness, conversation.summary, conversation.id),
            )

    def is_session_persisted(self, session_id: UUID) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM turns WHERE session_id = %s LIMIT 1", (session_id,))
            return cur.fetchone() is not None

    def get_last_open_id(self) -> int | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM conversations WHERE NOT consolidated ORDER BY started_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row[0] if row else None

    def extend_conversation(
        self,
        conversation_id: int,
        session_id: UUID,
        turns: list[Turn],
        ended_at: datetime | None,
    ) -> None:
        with self._conn.cursor() as cur:
            for turn in turns:
                cur.execute(
                    """
                    INSERT INTO turns (conversation_id, session_id, timestamp, speaker, content, language)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        conversation_id,
                        session_id,
                        turn.timestamp,
                        turn.speaker.value,
                        turn.content,
                        turn.language.code if turn.language else None,
                    ),
                )
            cur.execute(
                "UPDATE conversations SET ended_at = %s WHERE id = %s",
                (ended_at, conversation_id),
            )

    def get_unconsolidated(self) -> list[Conversation]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.started_at, c.ended_at, c.persona_id,
                       c.worthiness, c.summary, c.consolidated,
                       t.timestamp, t.speaker, t.content, t.language
                FROM conversations c
                LEFT JOIN turns t ON t.conversation_id = c.id
                WHERE NOT c.consolidated
                ORDER BY c.started_at, t.timestamp
                """
            )
            rows = cur.fetchall()

        conversations: dict[int, Conversation] = {}
        for row in rows:
            (conv_id, started_at, ended_at, persona_id, worthiness, summary, consolidated,
             t_ts, t_speaker, t_content, t_lang) = row
            if conv_id not in conversations:
                conversations[conv_id] = Conversation(
                    id=conv_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    persona_id=persona_id,
                    worthiness=worthiness,
                    summary=summary,
                    consolidated=consolidated,
                )
            if t_ts is not None:
                conversations[conv_id].turns.append(Turn(
                    timestamp=t_ts,
                    speaker=Speaker(t_speaker),
                    content=t_content,
                    language=Language(t_lang) if t_lang else None,
                ))

        return sorted(conversations.values(), key=lambda c: c.started_at)


# ---------------------------------------------------------------------------
# PSMemoryRepository
# ---------------------------------------------------------------------------

class PSMemoryRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert_episode(self, episode: Episode) -> int:
        now = datetime.now(UTC)
        with self._conn.cursor() as cur:
            if episode.id is None:
                cur.execute(
                    """
                    INSERT INTO episodes (summary, happened_at, origin_conversation_id, created_at, updated_at, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (episode.summary, episode.happened_at, episode.origin_conversation_id, now, now, _vec(episode.embedding)),
                )
                return cur.fetchone()[0]
            else:
                cur.execute(
                    "UPDATE episodes SET summary = %s, happened_at = %s, updated_at = %s, embedding = %s WHERE id = %s",
                    (episode.summary, episode.happened_at, now, _vec(episode.embedding), episode.id),
                )
                return episode.id

    def upsert_concept(self, concept: Concept) -> int:
        # persona_state is deliberately absent from the UPDATE branch: upserts must never
        # clobber the owning persona's assessment state (single-writer contract) —
        # update_persona_state() below is the only write path to that column.
        now = datetime.now(UTC)
        with self._conn.cursor() as cur:
            if concept.id is None:
                cur.execute(
                    """
                    INSERT INTO concepts
                        (persona_id, name, description, language, category, persona_state,
                         engagement_level, created_at, updated_at, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        concept.persona_id,
                        concept.name,
                        concept.description,
                        concept.language.code,
                        concept.category,
                        Jsonb(concept.persona_state) if concept.persona_state is not None else None,
                        concept.engagement_level.name.lower(),
                        now,
                        now,
                        _vec(concept.embedding),
                    ),
                )
                return cur.fetchone()[0]
            else:
                cur.execute(
                    """
                    UPDATE concepts SET description = %s, category = %s, engagement_level = %s, updated_at = %s, embedding = %s
                    WHERE id = %s
                    """,
                    (
                        concept.description,
                        concept.category,
                        concept.engagement_level.name.lower(),
                        now,
                        _vec(concept.embedding),
                        concept.id,
                    ),
                )
                return concept.id

    def upsert_procedure(self, procedure: Procedure) -> int:
        now = datetime.now(UTC)
        with self._conn.cursor() as cur:
            if procedure.id is None:
                cur.execute(
                    """
                    INSERT INTO procedures
                        (persona_id, name, description, steps, language, category, persona_state,
                         engagement_level, created_at, updated_at, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        procedure.persona_id,
                        procedure.name,
                        procedure.description,
                        procedure.steps,
                        procedure.language.code,
                        procedure.category,
                        Jsonb(procedure.persona_state) if procedure.persona_state is not None else None,
                        procedure.engagement_level.name.lower(),
                        now,
                        now,
                        _vec(procedure.embedding),
                    ),
                )
                return cur.fetchone()[0]
            else:
                # Same single-writer rule as upsert_concept: persona_state never updated here.
                cur.execute(
                    """
                    UPDATE procedures
                    SET description = %s, steps = %s, category = %s, engagement_level = %s, updated_at = %s, embedding = %s
                    WHERE id = %s
                    """,
                    (
                        procedure.description,
                        procedure.steps,
                        procedure.category,
                        procedure.engagement_level.name.lower(),
                        now,
                        _vec(procedure.embedding),
                        procedure.id,
                    ),
                )
                return procedure.id

    def update_persona_state(self, memory_type: MemoryType, item_id: int, persona_state: dict) -> None:
        if memory_type == MemoryType.CONCEPT:
            table = "concepts"
        elif memory_type == MemoryType.PROCEDURE:
            table = "procedures"
        else:
            raise ValueError(f"persona_state does not exist on {memory_type.value} items")
        with self._conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET persona_state = %s, updated_at = %s WHERE id = %s",
                (Jsonb(persona_state), datetime.now(UTC), item_id),
            )

    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
        persona_id: UUID | None = None,
    ) -> list[MemoryItem]:
        vec = _vec(embedding)
        results: list[tuple[float, MemoryItem]] = []

        with self._conn.cursor() as cur:
            if MemoryType.EPISODE in memory_types:
                cur.execute(
                    """
                    SELECT id, summary, happened_at, origin_conversation_id, created_at, updated_at, embedding,
                           embedding <=> %s AS distance
                    FROM episodes
                    ORDER BY distance
                    LIMIT %s
                    """,
                    (vec, top_n),
                )
                for id_, summary, happened_at, origin_conv_id, created_at, updated_at, emb, distance in cur.fetchall():
                    results.append((distance, Episode(
                        id=id_,
                        summary=summary,
                        happened_at=happened_at,
                        origin_conversation_id=origin_conv_id,
                        created_at=created_at,
                        updated_at=updated_at,
                        embedding=_list(emb),
                    )))

            if MemoryType.CONCEPT in memory_types:
                if persona_id is not None:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, language, category, persona_state,
                               engagement_level, created_at, updated_at, embedding,
                               embedding <=> %s AS distance
                        FROM concepts
                        WHERE persona_id = %s
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, persona_id, top_n),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, language, category, persona_state,
                               engagement_level, created_at, updated_at, embedding,
                               embedding <=> %s AS distance
                        FROM concepts
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, top_n),
                    )
                for (id_, p_id, name, description, language, category, persona_state,
                     engagement_level, created_at, updated_at, emb, distance) in cur.fetchall():
                    results.append((distance, Concept(
                        id=id_,
                        persona_id=p_id,
                        name=name,
                        description=description,
                        language=Language(language),
                        category=category,
                        persona_state=persona_state,
                        engagement_level=EngagementLevel[engagement_level.upper()],
                        created_at=created_at,
                        updated_at=updated_at,
                        embedding=_list(emb),
                    )))

            if MemoryType.PROCEDURE in memory_types:
                if persona_id is not None:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, steps, language, category, persona_state,
                               engagement_level, created_at, updated_at, embedding,
                               embedding <=> %s AS distance
                        FROM procedures
                        WHERE persona_id = %s
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, persona_id, top_n),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, steps, language, category, persona_state,
                               engagement_level, created_at, updated_at, embedding,
                               embedding <=> %s AS distance
                        FROM procedures
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, top_n),
                    )
                for (id_, p_id, name, description, steps, language, category, persona_state,
                     engagement_level, created_at, updated_at, emb, distance) in cur.fetchall():
                    results.append((distance, Procedure(
                        id=id_,
                        persona_id=p_id,
                        name=name,
                        description=description,
                        steps=list(steps) if steps else [],
                        language=Language(language),
                        category=category,
                        persona_state=persona_state,
                        engagement_level=EngagementLevel[engagement_level.upper()],
                        created_at=created_at,
                        updated_at=updated_at,
                        embedding=_list(emb),
                    )))

        results.sort(key=lambda x: x[0])
        return [(1.0 - dist, item) for dist, item in results[:top_n]]


# ---------------------------------------------------------------------------
# PSMemoryBriefRepository
# ---------------------------------------------------------------------------

class PSMemoryBriefRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self) -> MemoryBrief | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT content, created_at, updated_at FROM memory_brief WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                return None
            return MemoryBrief(content=row[0], created_at=row[1], updated_at=row[2])

    def save(self, brief: MemoryBrief) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_brief (id, content, created_at, updated_at)
                VALUES (1, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    updated_at = EXCLUDED.updated_at
                """,
                (brief.content, brief.created_at, brief.updated_at),
            )
