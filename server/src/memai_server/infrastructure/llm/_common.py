# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from uuid import UUID

from ...domain.model import Concept, Conversation, Episode, Language, Procedure, Speaker
from ...services.ports import ExtractionResult


def _format_conversation(conversation: Conversation) -> str:
    lines = []
    for turn in conversation.turns:
        role = "User" if turn.speaker == Speaker.USER else "Assistant"
        lines.append(f"{role}: {turn.content}")
    return "\n".join(lines)


def _conversation_language(conversation: Conversation) -> Language:
    for turn in conversation.turns:
        if turn.language:
            return turn.language
    return Language("en")


def _parse_extraction(data: dict, conversation: Conversation, persona_id: UUID, lang: Language) -> ExtractionResult:
    origin_id = conversation.id or 0

    episodes = []
    for e in data.get("episodes", []):
        try:
            happened_at = (
                datetime.fromisoformat(e["happened_at"])
                if e.get("happened_at")
                else conversation.started_at
            )
        except ValueError:
            happened_at = conversation.started_at
        episodes.append(Episode(
            id=None,
            summary=e["summary"],
            happened_at=happened_at,
            origin_conversation_id=origin_id,
        ))

    concepts = []
    for c in data.get("concepts", []):
        try:
            entry_lang = Language(c.get("language", lang.code))
        except ValueError:
            entry_lang = lang
        concepts.append(Concept(
            id=None,
            persona_id=persona_id,
            name=c["name"],
            description=c["description"],
            language=entry_lang,
        ))

    procedures = []
    for p in data.get("procedures", []):
        try:
            entry_lang = Language(p.get("language", lang.code))
        except ValueError:
            entry_lang = lang
        procedures.append(Procedure(
            id=None,
            persona_id=persona_id,
            name=p["name"],
            description=p["description"],
            steps=p.get("steps", []),
            language=entry_lang,
        ))

    return ExtractionResult(episodes=episodes, concepts=concepts, procedures=procedures)
