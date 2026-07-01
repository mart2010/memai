# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import questionary

from ..services.ports import PromptChoice


class QuestionaryPrompter:
    def select(self, message: str, choices: list[PromptChoice]) -> str:
        return questionary.select(
            message, choices=[questionary.Choice(c.label, value=c.value) for c in choices]
        ).ask()

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]:
        return questionary.checkbox(
            message, choices=[questionary.Choice(c.label, value=c.value) for c in choices]
        ).ask()

    def confirm(self, message: str, default: bool = True) -> bool:
        return questionary.confirm(message, default=default).ask()

    def text(self, message: str, default: str = "") -> str:
        return questionary.text(message, default=default).ask()

    def info(self, message: str) -> None:
        print(message)
