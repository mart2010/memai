# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_config_dir

from ..domain.plan import InstallationPlan, Topology

CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"


class FileExistingInstallDetector:
    """Parses a prior install's memai.toml into a pre-filled InstallationPlan so a
    re-run starts from the recorded state instead of from nothing (FR-706): the
    wizard shows the current settings up front, languages come pre-checked (so
    adding one never silently drops the rest of [languages].installed), and the
    LLM/Whisper/database prompts default to the current values.

    Field mapping is best-effort per key — anything absent stays at the plan's
    own default. Topology: a config carrying [server].ssh_host was written for a
    split-host client machine; the server-side configs of single- and split-host
    installs are indistinguishable, so topology is otherwise left unset and
    SelectTopology asks again. A malformed file degrades to a fresh run rather
    than crashing (same graceful posture as SystemGPUDetector)."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path

    def load_existing_plan(self) -> InstallationPlan | None:
        if not self._path.exists():
            return None
        try:
            with open(self._path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(
                f"Found an existing config at {self._path} but could not parse it "
                f"({exc}) — starting a fresh wizard run instead."
            )
            return None

        plan = InstallationPlan(from_existing_install=True)
        server = raw.get("server", {})
        database = raw.get("database", {})
        stt = raw.get("stt", {})
        llm = raw.get("llm", {})
        languages = raw.get("languages", {})

        if server.get("ssh_host"):
            plan.set_topology(Topology.SPLIT_HOST)
        if database.get("url"):
            plan.database_url = database["url"]
        if stt.get("model_path"):
            plan.whisper_model = stt["model_path"]
        if stt.get("device"):
            plan.compute_device = stt["device"]
        if llm.get("model"):
            plan.llm_model_id = llm["model"]
        plan.languages = list(languages.get("installed", []))
        return plan
