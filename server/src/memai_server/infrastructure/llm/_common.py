# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from datetime import datetime
from uuid import UUID

from ...domain.model import Concept, Conversation, Episode, Language, Speaker
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


# Shared by the Ollama and OpenRouter worthiness evaluators so the criteria cannot
# drift between the two. The 2026-07-18 live-testing review found the LLM correctly
# judging a substantial-but-meta conversation (debugging a TTS voice-download bug) as
# "worth storing," then extraction turned that debugging session into a fabricated
# personal-event episode — a content-category failure, not a threshold one, so this
# prompt explicitly excludes conversation-about-itself/the-assistant's-own-operation
# from what counts as worth storing, rather than just raising a bar.
WORTHINESS_SYSTEM_PROMPT = (
    "Decide whether this conversation contains real content worth extracting into "
    "long-term memory: personal facts about the user, knowledge they engaged with, "
    "real tasks, or genuine events with a specific time or place. Not worth storing: "
    "small talk, greetings, trivial exchanges — and, importantly, discussion about the "
    "assistant itself or this session: bug reports, error messages, testing, debugging, "
    "questions about the assistant's own capabilities/configuration/model, or anything "
    "that only happened because this is a chat with an AI rather than something in the "
    "user's own life. Reply with exactly one word: YES or NO."
)


def _extraction_system_prompt(
    conversation: Conversation, primary_language: Language | None, extract_episodes: bool = True,
) -> str:
    """Shared by the Ollama and OpenRouter extractors so the extraction contract
    (JSON shape, episode-language rule) cannot drift between the two.

    `extract_episodes=False` (set by ConsolidateMemory for personas with a registered
    PersonaAssessmentPort — today, only the language tutor) omits the "episodes" array
    from the requested schema entirely, rather than asking then discarding: a language
    lesson's role-play/drills are not real events, and asking a small local model to
    judge genuine-story-vs-practiced-drill after the fact was tried and is not reliable —
    better not to ask at all. This function stays persona-agnostic either way: it only
    ever sees a plain bool, never anything tutor-specific.

    Procedures are never requested here, for any persona: how-to knowledge belongs to
    authoring expertise (bundles), not something live conversation organically produces
    (FR-307) — simplifies the LLM's job to episodes (when asked) and concepts only.
    """
    # Episodes are persona-independent and carry no language field — summaries are always
    # written in the user's primary language regardless of conversation language, so months
    # of tutoring don't turn the user's life story into target-language documents.
    episode_language_rule = (
        f" Write every episode summary in the language with IETF code '{primary_language.code}', "
        "regardless of the language the conversation was held in."
        if primary_language
        else ""
    )
    episodes_section = (
        '- "episodes": real personal events from the user\'s own life — NOT this '
        "conversation itself, and NOT anything about the assistant's own operation "
        "(bugs, errors, testing, debugging, capability or configuration questions). "
        "Only include an episode if it has a genuine, identifiable time or place "
        '(e.g. "yesterday", "last weekend", "at work", "in Paris") distinct from '
        "\"during this chat\" — if you can't name when or where it happened, don't "
        'extract it (each: {"summary": str, "happened_at": ISO8601 datetime of when it '
        'happened, or null only if genuinely unknown}).'
        f"{episode_language_rule}\n"
        if extract_episodes
        else ""
    )
    return (
        "Extract structured memories from this conversation. "
        f"The conversation took place around {conversation.started_at.isoformat()}.\n"
        "Return JSON with these arrays:\n"
        f"{episodes_section}"
        '- "concepts": facts or knowledge the user learned or discussed '
        '(each: {"name": str, "description": str, "language": IETF code, '
        '"category": short lowercase classification label or null})\n'
        "Be selective — only include what is genuinely informative. "
        "Leave arrays empty when nothing qualifies."
    )


def _parse_extraction(data: dict, conversation: Conversation, persona_id: UUID, lang: Language) -> ExtractionResult:
    origin_id = conversation.id or 0

    episodes = []
    for e in data.get("episodes", []):
        # No genuine time grounding (missing/unparseable happened_at) means the model
        # couldn't name a real when/where — treat as not a real episode rather than
        # silently backdating it to the conversation's own timestamp, which previously
        # let conversation-about-itself content masquerade as a dated personal event.
        happened_at_raw = e.get("happened_at")
        if not happened_at_raw:
            continue
        try:
            happened_at = datetime.fromisoformat(happened_at_raw)
        except ValueError:
            continue
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
            origin="organic",
        ))

    return ExtractionResult(episodes=episodes, concepts=concepts)
