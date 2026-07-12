# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Ollama-backed focus interpreter — maps the user's verbatim session wish to a
TutorFocus. A live LLM call, same standing as the recall-intent detector: it runs
in the gap after the [FOCUS: ...] response finished streaming, not on the hot path
to first audio. Fails open to the default mixed curriculum."""
import json
from collections.abc import Sequence

import ollama

from .selection import FOCUS_MODES, TutorFocus


class OllamaFocusInterpreter:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def interpret(self, focus: str, categories: Sequence[str]) -> TutorFocus:
        category_list = ", ".join(f'"{c}"' for c in categories) or "(none)"
        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "A language learner has said what they want from today's "
                            "practice session. Map their wish to JSON:\n"
                            '{"mode": "review"|"new"|"mixed", "category": str or null, '
                            '"topic": str or null}\n'
                            '- "review" = practise things they already know; "new" = '
                            'learn new material; "mixed" when unstated or both.\n'
                            f"- category must be one of: {category_list} — null unless "
                            "the wish clearly targets one of these.\n"
                            '- topic = a short theme phrase (e.g. "food", "travel") '
                            "only if the wish names a subject area, else null."
                        ),
                    },
                    {"role": "user", "content": focus},
                ],
                format="json",
            )
            data = json.loads(response.message.content)
        except (json.JSONDecodeError, AttributeError, ollama.ResponseError):
            return TutorFocus()

        mode = data.get("mode")
        category = data.get("category")
        topic = data.get("topic")
        return TutorFocus(
            mode=mode if mode in FOCUS_MODES else "mixed",
            category=category if category in categories else None,
            topic=topic if isinstance(topic, str) and topic.strip() else None,
        )
