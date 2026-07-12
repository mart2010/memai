import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import (
    Conversation,
    Language,
    Speaker,
    Turn,
)


def _turn() -> Turn:
    return Turn(timestamp=datetime.now(UTC), speaker=Speaker.USER, content="Hello", language=Language("en"))


def _conversation() -> Conversation:
    return Conversation(id=uuid4(), started_at=datetime.now(UTC), persona_id=uuid4())


class TestConversationInvariants:
    def test_add_turn_to_active_conversation(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        assert len(conv.turns) == 1

    def test_cannot_add_turn_after_ending(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        conv.end(datetime.now(UTC))
        with pytest.raises(ValueError, match="ended"):
            conv.add_turn(_turn())

    def test_cannot_consolidate_twice(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        conv.end(datetime.now(UTC))
        conv.mark_consolidated(worthiness=True, summary=None)
        with pytest.raises(ValueError, match="already consolidated"):
            conv.mark_consolidated(worthiness=False, summary=None)

    def test_cannot_consolidate_active_conversation(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        with pytest.raises(ValueError, match="active"):
            conv.mark_consolidated(worthiness=True, summary=None)

    def test_cannot_consolidate_empty_conversation(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.end(datetime.now(UTC))
        with pytest.raises(ValueError, match="no turns"):
            conv.mark_consolidated(worthiness=False, summary=None)


class TestConsolidationEligibility:
    def test_active_conversation_not_eligible(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        assert not conv.is_eligible_for_consolidation

    def test_ended_conversation_with_turns_eligible(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        conv.end(datetime.now(UTC))
        assert conv.is_eligible_for_consolidation

    def test_ended_empty_conversation_not_eligible(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.end(datetime.now(UTC))
        assert not conv.is_eligible_for_consolidation

    def test_consolidated_conversation_not_eligible(self):
        """Spec: TR-507"""
        conv = _conversation()
        conv.add_turn(_turn())
        conv.end(datetime.now(UTC))
        conv.mark_consolidated(worthiness=True, summary=None)
        assert not conv.is_eligible_for_consolidation
