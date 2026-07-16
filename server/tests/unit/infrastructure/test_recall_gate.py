from memai_server.infrastructure.language_tutor import LanguageTutorRecallGate
from memai_server.infrastructure.recall_gate import DefaultRecallGate


class TestDefaultRecallGate:
    """Spec: FR-309, TR-314"""

    def test_short_utterance_below_min_words_is_skipped(self):
        gate = DefaultRecallGate(min_words=3)
        assert gate.should_embed("yes") is False
        assert gate.should_embed("ok thanks") is False

    def test_utterance_at_or_above_min_words_proceeds(self):
        gate = DefaultRecallGate(min_words=3)
        assert gate.should_embed("tell me about Paris") is True
        assert gate.should_embed("one two three") is True

    def test_min_words_is_configurable(self):
        gate = DefaultRecallGate(min_words=1)
        assert gate.should_embed("agua") is True

    def test_no_previous_search_always_searches(self):
        gate = DefaultRecallGate()
        assert gate.should_search(None) is True

    def test_similarity_below_dedup_threshold_searches(self):
        gate = DefaultRecallGate(dedup_threshold=0.93)
        assert gate.should_search(0.5) is True

    def test_similarity_at_or_above_dedup_threshold_skips_search(self):
        gate = DefaultRecallGate(dedup_threshold=0.93)
        assert gate.should_search(0.93) is False
        assert gate.should_search(0.99) is False

    def test_dedup_threshold_is_configurable(self):
        gate = DefaultRecallGate(dedup_threshold=0.8)
        assert gate.should_search(0.85) is False


class TestLanguageTutorRecallGate:
    """Spec: FR-309 — a tutor session makes short answers meaningful (e.g. a
    one-word vocabulary reply), unlike the generic default."""

    def test_short_utterance_is_not_skipped(self):
        gate = LanguageTutorRecallGate()
        assert gate.should_embed("agua") is True
        assert gate.should_embed("si") is True

    def test_dedup_behaviour_is_inherited_unchanged_from_default(self):
        gate = LanguageTutorRecallGate()
        assert gate.should_search(None) is True
        assert gate.should_search(0.99) is False
        assert gate.should_search(0.5) is True
