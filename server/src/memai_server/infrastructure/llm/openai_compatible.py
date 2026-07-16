# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import openai

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
#
# Recall gating (FR-309/TR-314) no longer needs a provider-specific twin here —
# it moved from a per-turn LLM classification call to a persona-scoped, local
# threshold policy (RecallGate), so it no longer varies with [llm].provider at
# all. Only the main conversational completion above still does.
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
