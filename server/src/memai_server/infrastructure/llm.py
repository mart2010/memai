# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import json
from datetime import datetime, UTC

import ollama

from ..domain.events import RecallTriggered
from ..domain.model import (
    Concept,
    Conversation,
    Episode,
    Language,
    MemoryType,
    Procedure,
    Speaker,
)
from ..services.ports import ConsolidationExtractor, ExtractionResult, Message


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


# ---------------------------------------------------------------------------
# Live path — async streaming
# ---------------------------------------------------------------------------

class OllamaLLMService:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.AsyncClient(host=host)
        self._model = model

    async def complete(self, messages: list[Message], system_prompt: str):
        ollama_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            ollama_messages.append({"role": m.role, "content": m.content})
        async for part in self._client.chat(
            model=self._model,
            messages=ollama_messages,
            stream=True,
        ):
            if part.message.content:
                yield part.message.content


# ---------------------------------------------------------------------------
# Offline path — synchronous one-shot calls
# ---------------------------------------------------------------------------

class OllamaWorthinessEvaluator:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def evaluate(self, conversation: Conversation) -> bool:
        transcript = _format_conversation(conversation)
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Decide whether a conversation is worth storing as a long-term memory. "
                        "Worth storing: personal facts about the user, knowledge learned, tasks worked on, meaningful events. "
                        "Not worth storing: small talk, greetings, trivial exchanges. "
                        "Reply with exactly one word: YES or NO."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
        )
        return "yes" in response.message.content.strip().lower()


class OllamaRecallIntentDetector:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def detect(self, text: str) -> RecallTriggered | None:
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Detect if the user wants to recall past memories. "
                        'If yes, respond with JSON: {"recall": true, "query": "<search query>", '
                        '"memory_types": ["episode"|"concept"|"procedure", ...]}. '
                        "Include only the relevant memory type(s). "
                        'If no recall intent, respond with {"recall": false}.'
                    ),
                },
                {"role": "user", "content": text},
            ],
            format="json",
        )
        try:
            data = json.loads(response.message.content)
        except (json.JSONDecodeError, AttributeError):
            return None
        if not data.get("recall"):
            return None
        query = data.get("query", text)
        valid_values = {m.value for m in MemoryType}
        memory_types = tuple(
            MemoryType(t) for t in data.get("memory_types", []) if t in valid_values
        ) or tuple(MemoryType)
        return RecallTriggered(query=query, memory_types=memory_types)


class OllamaConsolidationExtractor:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def extract(self, conversation: Conversation) -> ExtractionResult:
        transcript = _format_conversation(conversation)
        persona_id = conversation.persona_snapshot.id
        lang = _conversation_language(conversation)

        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract structured memories from this conversation. "
                        f"The conversation took place around {conversation.started_at.isoformat()}.\n"
                        "Return JSON with three arrays:\n"
                        '- "episodes": personal events or experiences the user mentioned '
                        '(each: {"summary": str, "happened_at": ISO8601 datetime or null})\n'
                        '- "concepts": facts or knowledge the user learned or discussed '
                        '(each: {"name": str, "description": str, "language": IETF code})\n'
                        '- "procedures": how-to knowledge '
                        '(each: {"name": str, "description": str, "steps": [str], "language": IETF code})\n'
                        "Be selective — only include what is genuinely informative. "
                        "Leave arrays empty when nothing qualifies."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            format="json",
        )

        try:
            data = json.loads(response.message.content)
        except (json.JSONDecodeError, AttributeError):
            return ExtractionResult(episodes=[], concepts=[], procedures=[])

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
