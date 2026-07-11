# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import subprocess


class NvidiaSmiGPUDetector:
    """CUDA-only, matching the current GPU backend (CLAUDE.md: ROCm/Metal are
    long-term goals). Returns None on any failure — GPUDetector's contract —
    rather than raising, so the wizard can still proceed with a warning. None
    therefore also covers a non-NVIDIA GPU (e.g. AMD) being present but
    undetectable by this CUDA-only check — DetectComputeDevice treats that
    the same as no GPU at all: CPU fallback, not an error."""

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
