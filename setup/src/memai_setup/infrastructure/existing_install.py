# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir

from ..domain.plan import InstallationPlan

CONFIG_PATH = Path(user_config_dir("memai", appauthor=False)) / "memai.toml"


class FileExistingInstallDetector:
    """TODO: parse CONFIG_PATH (server or client memai.toml, if present) and
    pre-fill an InstallationPlan for re-runs — the field-by-field mapping isn't
    designed yet. Until then, an existing config is acknowledged but treated as
    unparseable: fall back to a fresh run rather than crashing, same as every
    other "not yet implemented" piece in this package degrades gracefully
    (NvidiaSmiGPUDetector returns None on failure, never raises)."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path

    def load_existing_plan(self) -> InstallationPlan | None:
        if not self._path.exists():
            return None
        print(
            f"Found an existing config at {self._path}, but re-run pre-fill isn't "
            "implemented yet — starting a fresh wizard run instead."
        )
        return None
