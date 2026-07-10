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


def _extraction_system_prompt(conversation: Conversation, primary_language: Language | None) -> str:
    """Shared by the Ollama and OpenRouter extractors so the extraction contract
    (JSON shape, episode-language rule) cannot drift between the two."""
    # Episodes are persona-independent and carry no language field — summaries are always
    # written in the user's primary language regardless of conversation language, so months
    # of tutoring don't turn the user's life story into target-language documents.
    episode_language_rule = (
        f" Write every episode summary in the language with IETF code '{primary_language.code}', "
        "regardless of the language the conversation was held in."
        if primary_language
        else ""
    )
    return (
        "Extract structured memories from this conversation. "
        f"The conversation took place around {conversation.started_at.isoformat()}.\n"
        "Return JSON with three arrays:\n"
        '- "episodes": personal events or experiences the user mentioned '
        '(each: {"summary": str, "happened_at": ISO8601 datetime or null}).'
        f"{episode_language_rule}\n"
        '- "concepts": facts or knowledge the user learned or discussed '
        '(each: {"name": str, "description": str, "language": IETF code, '
        '"category": short lowercase classification label or null})\n'
        '- "procedures": how-to knowledge '
        '(each: {"name": str, "description": str, "steps": [str], "language": IETF code, '
        '"category": short lowercase classification label or null})\n'
        "Be selective — only include what is genuinely informative. "
        "Leave arrays empty when nothing qualifies."
    )


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
            category=c.get("category") or None,
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
            category=p.get("category") or None,
        ))

    return ExtractionResult(episodes=episodes, concepts=concepts, procedures=procedures)
