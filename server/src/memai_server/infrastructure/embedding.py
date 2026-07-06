# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import os

# Must be set before `sentence_transformers` (via huggingface_hub) is imported.
# The live server must never touch the network for model loading, same principle
# as STT/TTS: SentenceTransformer otherwise does a HEAD request to Hugging Face
# Hub on every load to check for updates, even when the model is already fully
# cached locally — that call has no reason to succeed, or even be attempted, on
# a locked-down/offline deployment.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from sentence_transformers import SentenceTransformer  # noqa: E402


class SentenceTransformerEmbeddingService:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-large") -> None:
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()
