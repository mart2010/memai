# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .model import LanguageCode


class Topology(Enum):
    SINGLE_HOST = auto()
    SPLIT_HOST = auto()


@dataclass
class InstallationPlan:
    """Aggregate root accumulating wizard decisions across steps. Not persisted
    directly — GenerateConfig (a use case) translates it into the on-disk TOML
    config once the flow completes.

    Invariant: topology cannot change once locked (set by ExistingInstallDetector
    when a prior install is found on re-run) — see CLAUDE.md "Fully re-runnable:
    ... topology locked after first install"."""

    topology: Topology | None = None
    llm_model_id: str | None = None
    languages: list[LanguageCode] = field(default_factory=list)
    whisper_model: str | None = None
    tts_engine_by_language: dict[LanguageCode, str] = field(default_factory=dict)
    # No wizard step collects Postgres connection details yet (the original
    # flow's "3. Prerequisites check" step isn't implemented) — defaults to
    # the same local connection string shipped in
    # server/config/memai.example.toml. GenerateConfig and SetupSchema both
    # read this field rather than duplicating the literal.
    database_url: str = "postgresql://memai:changeme@localhost:5432/memai"

    _topology_locked: bool = field(default=False, repr=False, compare=False)

    def set_topology(self, topology: Topology) -> None:
        if self._topology_locked and self.topology is not None and topology != self.topology:
            raise ValueError(
                f"Topology is locked to {self.topology.name} from a previous install; "
                "changing topology is not supported on re-run."
            )
        self.topology = topology

    def lock_topology(self) -> None:
        """Called by ExistingInstallDetector once a prior install's topology has
        been pre-filled onto this plan."""
        if self.topology is None:
            raise ValueError("Cannot lock topology before it is set.")
        self._topology_locked = True
