# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Ollama-backed cluster proposer — one offline LLM call proposing the vocabulary
surrounding an interest cluster. Fails open to no proposals."""
import json
from collections.abc import Sequence

import ollama

from ...domain.model import Concept, Language
from .enrichment import ProposedItem


class OllamaClusterProposer:
    def __init__(self, model: str = "llama3.3", host: str | None = None) -> None:
        self._client = ollama.Client(host=host)
        self._model = model

    def propose(
        self, language: Language, cluster: Sequence[Concept], count: int
    ) -> Sequence[ProposedItem]:
        seed_lines = "\n".join(f"- {c.name}: {c.description}" for c in cluster)
        seed_names = {c.name.strip().lower() for c in cluster}
        try:
            response = self._client.chat(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "A language learner keeps bringing up the vocabulary theme "
                            "below on their own — a strong interest signal. Propose "
                            f"up to {count} NEW '{language.code}' vocabulary items "
                            "surrounding this theme (words or fixed expressions the "
                            "learner would plausibly want next). Do not repeat the "
                            "listed items. Respond with JSON: "
                            '{"items": [{"name": str, "description": str, '
                            '"category": short lowercase label or null}]}.\n'
                            "Each description: a tight explanation in the item's own "
                            "language, suitable for teaching (well under 300 words)."
                        ),
                    },
                    {"role": "user", "content": f"Theme items:\n{seed_lines}"},
                ],
                format="json",
            )
            data = json.loads(response.message.content)
        except (json.JSONDecodeError, AttributeError, ollama.ResponseError):
            return []

        proposals: list[ProposedItem] = []
        for entry in data.get("items", []):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            description = entry.get("description")
            if not (isinstance(name, str) and name.strip() and isinstance(description, str) and description.strip()):
                continue
            if name.strip().lower() in seed_names:
                continue
            category = entry.get("category")
            proposals.append(ProposedItem(
                name=name.strip(),
                description=description.strip(),
                category=category if isinstance(category, str) and category.strip() else None,
            ))
        return proposals[:count]
