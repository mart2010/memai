# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import json

import openai

from ...domain.events import RecallSource, RecallTriggered
from ...domain.model import MemoryType
from ...services.ports import Message


# ---------------------------------------------------------------------------
# Live conversation path — any OpenAI-compatible HTTP endpoint (OpenRouter,
# OpenAI, a self-hosted vLLM/LM Studio server, ...). base_url/model are always
# explicit — there is no sensible generic default, unlike the OpenRouter
# adapters this was split out of (FR-707/TR-955: a fully local install never
# constructs these). api_key is optional: some self-hosted endpoints don't
# check it at all, but the openai client still wants some string in the
# Authorization header, so a missing key is coalesced to a placeholder rather
# than left unset.
# ---------------------------------------------------------------------------

class OpenAICompatibleLLMService:
    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key or "not-required", base_url=base_url)
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


class OpenAICompatibleRecallIntentDetector:
    """Live, per-turn recall-intent check (see ProcessTurn) — moves together
    with OpenAICompatibleLLMService rather than staying on local Ollama, so a
    GPU-less install doesn't pay a slow local-CPU inference cost on every
    single turn before the (now fast, remote) reply even starts."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        self._client = openai.OpenAI(api_key=api_key or "not-required", base_url=base_url)
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
