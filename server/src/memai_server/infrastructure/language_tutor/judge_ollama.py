# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Ollama-backed practice judge — one offline LLM call per conversation judging how
each touched item was practised. Fails open to no judgments (assessment then records
exposure only: the day anchor and session count move, retrievals/errors don't)."""
import json
from collections.abc import Sequence

import ollama

from ...domain.model import Conversation, Speaker
from ...services.ports import MemoryItem
from .assessment import PracticeJudgment


def _transcript(conversation: Conversation) -> str:
    return "\n".join(
        f"{'User' if t.speaker == Speaker.USER else 'Assistant'}: {t.content}"
        for t in conversation.turns
    )


class OllamaPracticeJudge:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def judge(
        self, conversation: Conversation, items: Sequence[MemoryItem]
    ) -> Sequence[PracticeJudgment]:
        names = [item.name for item in items]
        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You evaluate a language-practice conversation. For each "
                            "listed item, judge how the LEARNER practised it. Respond "
                            'with JSON: {"items": [{"name": str, "retrievals": int, '
                            '"errors": int, "user_initiated": bool}]}.\n'
                            "- retrievals: how many times the learner SUCCESSFULLY "
                            "produced or used the item themselves. Hearing or reading "
                            "it counts for nothing — production only.\n"
                            "- errors: how many times they used it incorrectly or "
                            "failed to recall it when prompted.\n"
                            "- user_initiated: true if the learner brought the item up "
                            "themselves rather than being prompted.\n"
                            f"Items to judge: {json.dumps(names, ensure_ascii=False)}\n"
                            "Include every listed item; use zeros when it was only "
                            "mentioned by the assistant."
                        ),
                    },
                    {"role": "user", "content": _transcript(conversation)},
                ],
                format="json",
            )
            data = json.loads(response.message.content)
        except (json.JSONDecodeError, AttributeError, ollama.ResponseError):
            return []

        valid_names = set(names)
        judgments = []
        for entry in data.get("items", []):
            if not isinstance(entry, dict) or entry.get("name") not in valid_names:
                continue
            try:
                judgments.append(PracticeJudgment(
                    name=entry["name"],
                    retrievals=max(int(entry.get("retrievals", 0)), 0),
                    errors=max(int(entry.get("errors", 0)), 0),
                    user_initiated=bool(entry.get("user_initiated", False)),
                ))
            except (TypeError, ValueError):
                continue
        return judgments
