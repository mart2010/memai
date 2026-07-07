# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import json

import openai

from ...domain.events import RecallSource, RecallTriggered
from ...domain.model import Concept, Conversation, Episode, MemoryType, Procedure
from ...services.ports import ExtractionResult, MemoryItem, Message
from ._common import _conversation_language, _format_conversation, _parse_extraction

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct"


# ---------------------------------------------------------------------------
# Live path — async streaming
# ---------------------------------------------------------------------------

class OpenRouterLLMService:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def complete(self, messages: list[Message], system_prompt: str):
        openai_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            openai_messages.append({"role": m.role, "content": m.content})
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=openai_messages,
            stream=True,
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content


# ---------------------------------------------------------------------------
# Offline path — synchronous one-shot calls
# ---------------------------------------------------------------------------

class OpenRouterWorthinessEvaluator:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def evaluate(self, conversation: Conversation) -> bool:
        transcript = _format_conversation(conversation)
        response = self._client.chat.completions.create(
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
        return "yes" in (response.choices[0].message.content or "").strip().lower()


class OpenRouterRecallIntentDetector:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def detect(self, text: str) -> RecallTriggered | None:
        response = self._client.chat.completions.create(
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
            response_format={"type": "json_object"},
        )
        try:
            data = json.loads(response.choices[0].message.content or "")
        except (json.JSONDecodeError, AttributeError):
            return None
        if not data.get("recall"):
            return None
        query = data.get("query", text)
        valid_values = {m.value for m in MemoryType}
        memory_types = tuple(
            MemoryType(t) for t in data.get("memory_types", []) if t in valid_values
        ) or tuple(MemoryType)
        return RecallTriggered(query=query, memory_types=memory_types, source=RecallSource.USER)


class OpenRouterMemorySynthesizer:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def _chat(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    def _chat_json(self, system: str, user: str) -> dict:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(response.choices[0].message.content or "")
        except (json.JSONDecodeError, AttributeError):
            return {}

    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str:
        return self._chat(
            system=(
                "Merge two summaries of the same event into one coherent, concise summary. "
                "Preserve all factual details from both. Output only the merged summary, nothing else."
            ),
            user=f"Summary A:\n{existing_summary}\n\nSummary B:\n{new_summary}",
        )

    def synthesize_concept(self, existing: Concept, new_description: str) -> str:
        return self._chat(
            system=(
                f"Synthesize two descriptions of the same concept into one (~300 words max). "
                f"Write in language '{existing.language.code}'. "
                "Absorb all details — do not append, synthesize. Output only the merged description."
            ),
            user=(
                f"Concept: {existing.name}\n\n"
                f"Existing description:\n{existing.description}\n\n"
                f"New information:\n{new_description}"
            ),
        )

    def synthesize_procedure(self, existing: Procedure, new_description: str, new_steps: list[str]) -> tuple[str, list[str]]:
        existing_steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(existing.steps)) or "(none)"
        new_steps_fmt = "\n".join(f"{i+1}. {s}" for i, s in enumerate(new_steps)) or "(none)"
        data = self._chat_json(
            system=(
                f"Synthesize two descriptions of the same procedure into one. "
                f"Write in language '{existing.language.code}'. "
                'Output JSON with "description" (string, ~300 words) and '
                '"steps" (array of strings, empty array if no clear sequential steps).'
            ),
            user=(
                f"Procedure: {existing.name}\n\n"
                f"Existing:\nDescription: {existing.description}\nSteps:\n{existing_steps}\n\n"
                f"New:\nDescription: {new_description}\nSteps:\n{new_steps_fmt}"
            ),
        )
        return data.get("description", new_description).strip(), data.get("steps", new_steps)


class OpenRouterDisambiguationEvaluator:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def is_same(self, existing: MemoryItem, candidate: MemoryItem) -> bool:
        def _fmt(item: MemoryItem) -> str:
            if isinstance(item, Episode):
                return f"Episode: {item.summary}"
            if isinstance(item, Concept):
                return f"Concept '{item.name}': {item.description}"
            return f"Procedure '{item.name}': {item.description}"

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Decide whether two memory records refer to the same real-world entity, event, or topic. "
                        "Reply with exactly one word: YES or NO."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Record A:\n{_fmt(existing)}\n\nRecord B:\n{_fmt(candidate)}",
                },
            ],
        )
        return "yes" in (response.choices[0].message.content or "").strip().lower()


class OpenRouterConsolidationExtractor:
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def extract(self, conversation: Conversation) -> ExtractionResult:
        transcript = _format_conversation(conversation)
        persona_id = conversation.persona_id
        lang = _conversation_language(conversation)

        response = self._client.chat.completions.create(
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
            response_format={"type": "json_object"},
        )

        try:
            data = json.loads(response.choices[0].message.content or "")
        except (json.JSONDecodeError, AttributeError):
            return ExtractionResult(episodes=[], concepts=[], procedures=[])

        return _parse_extraction(data, conversation, persona_id, lang)
