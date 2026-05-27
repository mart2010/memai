# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from sentence_transformers import SentenceTransformer


class SentenceTransformerEmbeddingService:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-large") -> None:
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()
