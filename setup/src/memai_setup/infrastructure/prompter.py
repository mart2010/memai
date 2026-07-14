# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import questionary

from ..services.ports import PromptChoice


class QuestionaryPrompter:
    def select(self, message: str, choices: list[PromptChoice], default: str | None = None) -> str:
        qchoices = [questionary.Choice(c.label, value=c.value) for c in choices]
        # questionary wants the Choice object (or exact title) as default — resolve
        # from the value, ignoring a default that isn't among the choices.
        default_choice = next((qc for qc in qchoices if qc.value == default), None)
        return questionary.select(message, choices=qchoices, default=default_choice).ask()

    def select_many(self, message: str, choices: list[PromptChoice]) -> list[str]:
        return questionary.checkbox(
            message, choices=[questionary.Choice(c.label, value=c.value, checked=c.checked) for c in choices]
        ).ask()

    def confirm(self, message: str, default: bool = True) -> bool:
        return questionary.confirm(message, default=default).ask()

    def text(self, message: str, default: str = "") -> str:
        return questionary.text(message, default=default).ask()

    def info(self, message: str) -> None:
        print(message)

    def heading(self, title: str, lines: list[str] | None = None) -> None:
        border = "=" * max(60, len(title) + 4)
        print(f"\n{border}")
        print(f"  {title}")
        if lines:
            print()
            for line in lines:
                print(f"  {line}" if line else "")
        print(f"{border}\n")
