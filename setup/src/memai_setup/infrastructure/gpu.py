# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..domain.model import DetectedGPU

_CARD_NAME = re.compile(r"^card\d+$")
_PCI_VENDOR_IDS = {
    "0x10de": "nvidia",
    "0x1002": "amd",
    "0x8086": "intel",
}
# amdgpu-driver-specific sysfs files, in GPU-reachable-memory order: dedicated
# VRAM, then GTT (system memory the GPU can also address — the pool an
# integrated APU like AMD's Ryzen AI / Strix Halo actually relies on; see
# docs/archive/PLAN_phases_1-13.md Phase 12's "17.7 GB in the GTT pool"
# finding, read by hand from these same two files).
_AMDGPU_MEMORY_FILES = ("mem_info_vram_total", "mem_info_gtt_total")


class SystemGPUDetector:
    """CUDA sizing (`detect_vram_gb`) stays NVIDIA-only, matching the current
    GPU backend (CLAUDE.md: ROCm/Metal are long-term goals, no adapter exists
    yet) — returns None on any failure rather than raising, so the wizard can
    still proceed with a warning. `detect_gpu` is a separate, additive,
    Linux-only sysfs fallback so the wizard can at least *identify* a
    non-NVIDIA GPU (name + best-effort memory) instead of reporting "no GPU"
    when one is very much present and, per real testing on an AMD Ryzen AI
    APU box, already working fine for Ollama's own LLM acceleration — that
    detection just doesn't happen through this CUDA-specific check."""

    def __init__(self, drm_root: Path = Path("/sys/class/drm")) -> None:
        self._drm_root = drm_root

    def detect_vram_gb(self) -> float | None:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        try:
            return int(first_line) / 1024
        except ValueError:
            return None

    def detect_gpu(self) -> DetectedGPU | None:
        if not self._drm_root.is_dir():
            return None

        for card in sorted(self._drm_root.iterdir()):
            if not _CARD_NAME.match(card.name):
                continue  # skip connector nodes like "card0-DP-1"
            device_dir = card / "device"
            vendor_id = self._read_text(device_dir / "vendor")
            if vendor_id is None:
                continue
            vendor = _PCI_VENDOR_IDS.get(vendor_id.strip().lower(), "unknown")
            vram_gb = self._read_amdgpu_memory_gb(device_dir) if vendor == "amd" else None
            return DetectedGPU(vendor=vendor, vram_gb=vram_gb)
        return None

    def _read_amdgpu_memory_gb(self, device_dir: Path) -> float | None:
        total_bytes = 0
        found = False
        for filename in _AMDGPU_MEMORY_FILES:
            text = self._read_text(device_dir / filename)
            if text is None:
                continue
            try:
                total_bytes += int(text.strip())
                found = True
            except ValueError:
                continue
        return total_bytes / (1024**3) if found else None

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text()
        except OSError:
            return None
