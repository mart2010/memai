# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Phase 6 end-to-end test: two sessions, second session's LLM context contains the
first session's memory brief. Real Postgres throughout (User/Persona/MemoryBrief
repos, StartSession, the real _compose_working_context prompt assembly) — only the
LLM call inside GenerateMemoryBrief is a Fake, since what the LLM would actually say
is not what this test is verifying; the plumbing that gets its answer into the next
session's prompt is."""
from datetime import UTC, datetime
from uuid import uuid4

import psycopg

from memai_server.domain.model import AssistantPersona, Language, User
from memai_server.infrastructure.postgres import (
    PSMemoryBriefRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUserRepository,
)
from memai_server.services.memory import GenerateMemoryBrief
from memai_server.services.session import StartSession, _compose_working_context

from tests.fakes.fakes import FakeLLMService, FakeSessionLogReader

_NOW = datetime.now(UTC)


async def test_second_session_llm_context_contains_first_sessions_brief(pg_conn: psycopg.Connection) -> None:
    """Spec: FR-109, FR-308, TR-303"""
    persona = AssistantPersona.general_assistant(system_prompt="You are a helpful assistant.")
    PSPersonaRepository(pg_conn).save(persona)
    user = User(id=uuid4(), primary_language=Language("en"))
    PSUserRepository(pg_conn).save(user)

    # --- End of session 1: generate and persist the memory brief ---
    brief_content = "Martin enjoys hiking in the Alps and is building a local voice assistant."
    generate_brief = GenerateMemoryBrief(
        llm=FakeLLMService(response=brief_content),
        memory_brief_repo=PSMemoryBriefRepository(pg_conn),
    )
    await generate_brief.execute(generated_at=_NOW)

    # --- Session 2 starts: StartSession pulls the brief from real Postgres ---
    start_session = StartSession(
        user_repo=PSUserRepository(pg_conn),
        persona_repo=PSPersonaRepository(pg_conn),
        memory_brief_repo=PSMemoryBriefRepository(pg_conn),
        session_log_reader=FakeSessionLogReader(),
        memory_repo=PSMemoryRepository(pg_conn),
    )
    wm = start_session.execute(session_id=uuid4(), started_at=_NOW)

    assert wm.memory_brief is not None
    assert wm.memory_brief.content == brief_content

    # --- The literal claim under test: the brief actually reaches the LLM's context ---
    system_prompt, _messages = _compose_working_context(wm, recalled_memories=[])
    assert brief_content in system_prompt
