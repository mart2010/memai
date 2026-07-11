# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from __future__ import annotations

import subprocess

from huggingface_hub import hf_hub_download, snapshot_download

_PIPER_VOICES_REPO = "rhasspy/piper-voices"
# Must match SentenceTransformerEmbeddingService's default in
# server/src/memai_server/infrastructure/embedding.py — the embedding model is a
# hardcoded invariant of Mémai (CLAUDE.md: not voice-configurable, not swappable via
# GA conversation), so there is no plan field to drive this from, unlike Whisper/Piper.
_EMBEDDING_MODEL_REPO = "intfloat/multilingual-e5-large"


class OllamaModelInstaller:
    """Real network/subprocess side effects — additive only (CLAUDE.md:
    engines are never removed), each method safe to re-run if a prior wizard
    run was interrupted (huggingface_hub downloads and `ollama pull` are both
    already idempotent/resumable on their own)."""

    def pull_llm(self, model_id: str) -> None:
        subprocess.run(["ollama", "pull", model_id], check=True)

    def download_whisper_model(self, name: str) -> None:
        # Matches faster-whisper's own internal repo mapping (Systran's
        # CTranslate2 conversions) — the same download FasterWhisperSTTService
        # would trigger lazily on first use; the wizard just does it up front
        # (see docs/INSTALL_SERVER.md step 6).
        snapshot_download(repo_id=f"Systran/faster-whisper-{name}")

    def download_piper_voice(self, voice_id: str) -> None:
        # voice_id format: "{lang}_{REGION}-{name}-{quality}", e.g.
        # "de_DE-thorsten-medium" — verified against rhasspy/piper-voices'
        # actual directory layout (lang/lang_region/name/quality/voice_id.*).
        lang_region, voice_name, quality = voice_id.split("-")
        lang = lang_region.split("_")[0]
        base_path = f"{lang}/{lang_region}/{voice_name}/{quality}/{voice_id}"
        hf_hub_download(repo_id=_PIPER_VOICES_REPO, filename=f"{base_path}.onnx")
        hf_hub_download(repo_id=_PIPER_VOICES_REPO, filename=f"{base_path}.onnx.json")

    def download_embedding_model(self) -> None:
        # Same rationale as download_whisper_model: SentenceTransformerEmbeddingService
        # sets HF_HUB_OFFLINE=1 so the live server never touches the network for it (see
        # its docstring) — so if the wizard doesn't pre-download it, first server startup
        # fails outright (offline mode, no cached model) instead of just being slower.
        snapshot_download(repo_id=_EMBEDDING_MODEL_REPO)
