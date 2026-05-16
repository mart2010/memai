"""
PostgreSQL repository implementations (psycopg 3 + pgvector).
Call connect(dsn) to get a connection with the vector type already registered,
then pass it to each repository constructor.
"""
from __future__ import annotations

from datetime import datetime
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
    conn = psycopg.connect(dsn)
    register_vector(conn)
    return conn


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _persona_to_jsonb(persona: AssistantPersona) -> Jsonb:
    return Jsonb({
        "id": str(persona.id),
        "name": persona.name,
        "system_prompt": persona.system_prompt,
        "languages": [l.code for l in persona.languages],
        "is_system": persona.is_system,
        "created_at": persona.created_at.isoformat(),
        "updated_at": persona.updated_at.isoformat(),
    })


def _jsonb_to_persona(data: dict) -> AssistantPersona:
    return AssistantPersona(
        id=UUID(data["id"]),
        name=data["name"],
        system_prompt=data["system_prompt"],
        languages=[Language(c) for c in data["languages"]],
        is_system=data["is_system"],
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
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
            cur.execute("SELECT id, primary_language, secondary_languages FROM users LIMIT 1")
            row = cur.fetchone()
            if row is None:
                return None
            id_, primary_lang, secondary_langs = row
            return User(
                id=id_,
                primary_language=Language(primary_lang) if primary_lang else None,
                secondary_languages=[Language(c) for c in (secondary_langs or [])],
            )

    def save(self, user: User) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, primary_language, secondary_languages)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    primary_language = EXCLUDED.primary_language,
                    secondary_languages = EXCLUDED.secondary_languages
                """,
                (
                    user.id,
                    user.primary_language.code if user.primary_language else None,
                    [l.code for l in user.secondary_languages],
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
                "SELECT id, name, system_prompt, languages, is_system, created_at, updated_at "
                "FROM personas WHERE id = %s",
                (persona_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            id_, name, system_prompt, languages, is_system, created_at, updated_at = row
            return AssistantPersona(
                id=id_,
                name=name,
                system_prompt=system_prompt,
                languages=[Language(c) for c in languages],
                is_system=is_system,
                created_at=created_at,
                updated_at=updated_at,
            )

    def list_all(self) -> list[AssistantPersona]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, system_prompt, languages, is_system, created_at, updated_at FROM personas"
            )
            return [
                AssistantPersona(
                    id=id_,
                    name=name,
                    system_prompt=system_prompt,
                    languages=[Language(c) for c in languages],
                    is_system=is_system,
                    created_at=created_at,
                    updated_at=updated_at,
                )
                for id_, name, system_prompt, languages, is_system, created_at, updated_at in cur.fetchall()
            ]

    def save(self, persona: AssistantPersona) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO personas (id, name, system_prompt, languages, is_system, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    system_prompt = EXCLUDED.system_prompt,
                    languages = EXCLUDED.languages,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    persona.id,
                    persona.name,
                    persona.system_prompt,
                    [l.code for l in persona.languages],
                    persona.is_system,
                    persona.created_at,
                    persona.updated_at,
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

    def save(self, conversation: Conversation) -> None:
        persona_jsonb = _persona_to_jsonb(conversation.persona_snapshot)
        with self._conn.cursor() as cur:
            if conversation.id is None:
                cur.execute(
                    """
                    INSERT INTO conversations
                        (started_at, ended_at, persona_snapshot, worthiness, summary, consolidated)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        conversation.started_at,
                        conversation.ended_at,
                        persona_jsonb,
                        conversation.worthiness,
                        conversation.summary,
                        conversation.consolidated,
                    ),
                )
                conversation.id = cur.fetchone()[0]
            else:
                cur.execute(
                    """
                    UPDATE conversations
                    SET ended_at = %s, worthiness = %s, summary = %s, consolidated = %s
                    WHERE id = %s
                    """,
                    (
                        conversation.ended_at,
                        conversation.worthiness,
                        conversation.summary,
                        conversation.consolidated,
                        conversation.id,
                    ),
                )

            for turn in conversation.turns:
                cur.execute(
                    """
                    INSERT INTO turns (conversation_id, timestamp, speaker, content, language)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (conversation_id, timestamp) DO NOTHING
                    """,
                    (
                        conversation.id,
                        turn.timestamp,
                        turn.speaker.value,
                        turn.content,
                        turn.language.code if turn.language else None,
                    ),
                )

    def get_unconsolidated(self) -> list[Conversation]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.started_at, c.ended_at, c.persona_snapshot,
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
            (conv_id, started_at, ended_at, persona_data, worthiness, summary, consolidated,
             t_ts, t_speaker, t_content, t_lang) = row
            if conv_id not in conversations:
                conversations[conv_id] = Conversation(
                    id=conv_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    persona_snapshot=_jsonb_to_persona(persona_data),
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

    def upsert_episode(self, episode: Episode) -> None:
        with self._conn.cursor() as cur:
            if episode.id is None:
                cur.execute(
                    """
                    INSERT INTO episodes (summary, happened_at, origin_conversation_id, embedding)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (episode.summary, episode.happened_at, episode.origin_conversation_id, _vec(episode.embedding)),
                )
                episode.id = cur.fetchone()[0]
            else:
                cur.execute(
                    "UPDATE episodes SET summary = %s, happened_at = %s, embedding = %s WHERE id = %s",
                    (episode.summary, episode.happened_at, _vec(episode.embedding), episode.id),
                )

    def upsert_concept(self, concept: Concept) -> None:
        with self._conn.cursor() as cur:
            if concept.id is None:
                cur.execute(
                    """
                    INSERT INTO concepts (persona_id, name, description, language, engagement_level, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        concept.persona_id,
                        concept.name,
                        concept.description,
                        concept.language.code,
                        concept.engagement_level.value,
                        _vec(concept.embedding),
                    ),
                )
                concept.id = cur.fetchone()[0]
            else:
                cur.execute(
                    """
                    UPDATE concepts SET description = %s, engagement_level = %s, embedding = %s
                    WHERE id = %s
                    """,
                    (concept.description, concept.engagement_level.value, _vec(concept.embedding), concept.id),
                )

    def upsert_procedure(self, procedure: Procedure) -> None:
        with self._conn.cursor() as cur:
            if procedure.id is None:
                cur.execute(
                    """
                    INSERT INTO procedures
                        (persona_id, name, description, steps, language, engagement_level, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        procedure.persona_id,
                        procedure.name,
                        procedure.description,
                        procedure.steps,
                        procedure.language.code,
                        procedure.engagement_level.value,
                        _vec(procedure.embedding),
                    ),
                )
                procedure.id = cur.fetchone()[0]
            else:
                cur.execute(
                    """
                    UPDATE procedures
                    SET description = %s, steps = %s, engagement_level = %s, embedding = %s
                    WHERE id = %s
                    """,
                    (
                        procedure.description,
                        procedure.steps,
                        procedure.engagement_level.value,
                        _vec(procedure.embedding),
                        procedure.id,
                    ),
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
                    SELECT id, summary, happened_at, origin_conversation_id, embedding,
                           embedding <=> %s AS distance
                    FROM episodes
                    ORDER BY distance
                    LIMIT %s
                    """,
                    (vec, top_n),
                )
                for id_, summary, happened_at, origin_conv_id, emb, distance in cur.fetchall():
                    results.append((distance, Episode(
                        id=id_,
                        summary=summary,
                        happened_at=happened_at,
                        origin_conversation_id=origin_conv_id,
                        embedding=_list(emb),
                    )))

            if MemoryType.CONCEPT in memory_types:
                if persona_id is not None:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, language, engagement_level, embedding,
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
                        SELECT id, persona_id, name, description, language, engagement_level, embedding,
                               embedding <=> %s AS distance
                        FROM concepts
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, top_n),
                    )
                for id_, p_id, name, description, language, engagement_level, emb, distance in cur.fetchall():
                    results.append((distance, Concept(
                        id=id_,
                        persona_id=p_id,
                        name=name,
                        description=description,
                        language=Language(language),
                        engagement_level=EngagementLevel(engagement_level),
                        embedding=_list(emb),
                    )))

            if MemoryType.PROCEDURE in memory_types:
                if persona_id is not None:
                    cur.execute(
                        """
                        SELECT id, persona_id, name, description, steps, language, engagement_level, embedding,
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
                        SELECT id, persona_id, name, description, steps, language, engagement_level, embedding,
                               embedding <=> %s AS distance
                        FROM procedures
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vec, top_n),
                    )
                for id_, p_id, name, description, steps, language, engagement_level, emb, distance in cur.fetchall():
                    results.append((distance, Procedure(
                        id=id_,
                        persona_id=p_id,
                        name=name,
                        description=description,
                        steps=list(steps) if steps else [],
                        language=Language(language),
                        engagement_level=EngagementLevel(engagement_level),
                        embedding=_list(emb),
                    )))

        results.sort(key=lambda x: x[0])
        return [item for _, item in results[:top_n]]


# ---------------------------------------------------------------------------
# PSMemoryBriefRepository
# ---------------------------------------------------------------------------

class PSMemoryBriefRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self) -> MemoryBrief | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT content, generated_at FROM memory_brief WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                return None
            return MemoryBrief(content=row[0], generated_at=row[1])

    def save(self, brief: MemoryBrief) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_brief (id, content, generated_at)
                VALUES (1, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    generated_at = EXCLUDED.generated_at
                """,
                (brief.content, brief.generated_at),
            )
