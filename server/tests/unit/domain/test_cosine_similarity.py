import pytest

from memai_server.domain.model import cosine_similarity


def test_identical_vectors_are_maximally_similar():
    """Spec: FR-309, TR-314"""
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_orthogonal_vectors_have_zero_similarity():
    """Spec: FR-309, TR-314"""
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_opposite_vectors_have_negative_one_similarity():
    """Spec: FR-309, TR-314"""
    assert cosine_similarity([1.0, 2.0], [-1.0, -2.0]) == pytest.approx(-1.0)


def test_scale_does_not_affect_similarity():
    """Cosine similarity is scale-invariant — RecallGate must not need
    L2-normalised input to compare correctly."""
    assert cosine_similarity([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


def test_zero_vector_returns_zero_rather_than_dividing_by_zero():
    """Spec: FR-309, TR-314"""
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0
