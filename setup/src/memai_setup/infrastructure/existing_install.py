# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir

from ..domain.plan import InstallationPlan

CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"


class FileExistingInstallDetector:
    """TODO: parse CONFIG_PATH (server or client memai.toml, if present) and
    pre-fill an InstallationPlan for re-runs. Returns None on a fresh install."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path

    def load_existing_plan(self) -> InstallationPlan | None:
        if not self._path.exists():
            return None
        raise NotImplementedError("TODO: parse existing memai.toml into an InstallationPlan")
