# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""Integration test + calibration harness for the real embedding model. Skips gracefully
when the model can't be loaded (e.g. the dev laptop, or not yet downloaded).

This is also the "print similarity scores to help determine a good threshold value"
calibration test called for in PLAN.md Phase 3 — run with `pytest -s` to see the printed
numbers. The two-tier threshold (0.93 auto-merge / 0.75 disambiguate, per CLAUDE.md) is
still a placeholder pending real data; this test only asserts *relative* ordering
(similar > dissimilar), not the absolute cutoff values.
"""
import math

import pytest

from memai_server.infrastructure.embedding import SentenceTransformerEmbeddingService

_EXPECTED_DIM = 1024


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


@pytest.fixture(scope="module")
def embedding_service() -> SentenceTransformerEmbeddingService:
    try:
        return SentenceTransformerEmbeddingService()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"embedding model unavailable: {e}")


class TestSentenceTransformerEmbeddingService:
    def test_embedding_dimension_matches_schema(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-501, TR-952"""
        vec = embedding_service.embed("hello")
        assert len(vec) == _EXPECTED_DIM

    def test_embedding_is_normalized(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-952"""
        vec = embedding_service.embed("hello")
        norm = math.sqrt(sum(x * x for x in vec))
        assert norm == pytest.approx(1.0, abs=1e-3)

    def test_identical_text_has_similarity_one(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-509"""
        vec = embedding_service.embed("The quick brown fox.")
        assert _cosine(vec, vec) == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.parametrize(
        "text_a,text_b,relation",
        [
            ("The dog ran in the park.", "A dog was running in the park.", "similar"),
            ("The dog ran in the park.", "Quarterly tax filings are due in April.", "dissimilar"),
            ("golden retriever", "dog", "similar"),
            ("big bang theory of the universe", "Big Bang the TV sitcom", "dissimilar"),
            ("J'aime le café le matin.", "I like coffee in the morning.", "similar"),  # cross-lingual
        ],
    )
    def test_calibration_similarity_scores(
        self, embedding_service: SentenceTransformerEmbeddingService, text_a: str, text_b: str, relation: str
    ) -> None:
        """Spec: TR-602"""
        similarity = _cosine(embedding_service.embed(text_a), embedding_service.embed(text_b))
        print(f"\n[calibration:{relation}] {similarity:.4f}  '{text_a}'  vs  '{text_b}'")

    def test_similar_pairs_score_higher_than_dissimilar_pairs(
        self, embedding_service: SentenceTransformerEmbeddingService
    ) -> None:
        """Spec: TR-602"""
        similar = _cosine(
            embedding_service.embed("The dog ran in the park."),
            embedding_service.embed("A dog was running in the park."),
        )
        dissimilar = _cosine(
            embedding_service.embed("The dog ran in the park."),
            embedding_service.embed("Quarterly tax filings are due in April."),
        )
        print(f"\n[calibration] similar={similar:.4f} dissimilar={dissimilar:.4f}")
        assert similar > dissimilar
