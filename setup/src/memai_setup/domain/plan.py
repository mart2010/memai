# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto

from .model import LanguageCode

_DSN_PASSWORD = re.compile(r"(://[^:/@]+):[^@]*(@)")


def masked_database_url(url: str) -> str:
    """A DSN safe to echo back to the terminal: the password (if any) replaced
    with '***'. Peer-auth DSNs have no password and pass through unchanged."""
    return _DSN_PASSWORD.sub(r"\1:***\2", url)


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
    # "cuda" | "cpu". Defaults to the fail-safe "cpu" — same "never assume the
    # optimistic case" philosophy as SystemGPUDetector.detect_vram_gb() returning None on any
    # failure. Set once by DetectComputeDevice; GenerateConfig/TomlConfigWriter
    # is the only other reader.
    compute_device: str = "cpu"
    # No wizard step collects Postgres connection details yet (the original
    # flow's "3. Prerequisites check" step isn't implemented) — defaults to
    # the same local connection string shipped in
    # server/config/memai.example.toml. GenerateConfig and SetupSchema both
    # read this field rather than duplicating the literal.
    database_url: str = "postgresql://memai:changeme@localhost:5432/memai"
    # True when this plan was pre-filled from a prior install's memai.toml
    # (ExistingInstallDetector): steps then present the recorded state as
    # defaults — pre-checked languages, current LLM/Whisper selections, a
    # keep-current database option — instead of starting from nothing.
    from_existing_install: bool = False

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
