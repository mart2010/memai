import pytest
from datetime import datetime, UTC
from uuid import uuid4

from memai_server.domain.model import (
    AssistantPersona,
    ConversationRecord,
    Language,
    Speaker,
    Turn,
)


def _persona() -> AssistantPersona:
    now = datetime.now(UTC)
    return AssistantPersona(
        id=uuid4(), name="Test", system_prompt="Be helpful.",
        is_system=False, created_at=now, updated_at=now,
    )


def _turn() -> Turn:
    return Turn(timestamp=datetime.now(UTC), speaker=Speaker.USER, content="Hello", language=Language("en"))


def _record() -> ConversationRecord:
    return ConversationRecord(id=uuid4(), started_at=datetime.now(UTC), persona_snapshot=_persona())


class TestConversationRecordInvariants:
    def test_add_turn_to_active_record(self):
        record = _record()
        record.add_turn(_turn())
        assert len(record.turns) == 1

    def test_cannot_add_turn_after_ending(self):
        record = _record()
        record.add_turn(_turn())
        record.end(datetime.now(UTC))
        with pytest.raises(ValueError, match="ended"):
            record.add_turn(_turn())

    def test_cannot_consolidate_twice(self):
        record = _record()
        record.add_turn(_turn())
        record.end(datetime.now(UTC))
        record.mark_consolidated(worthiness=True, summary=None)
        with pytest.raises(ValueError, match="already consolidated"):
            record.mark_consolidated(worthiness=False, summary=None)

    def test_cannot_consolidate_active_record(self):
        record = _record()
        record.add_turn(_turn())
        with pytest.raises(ValueError, match="active"):
            record.mark_consolidated(worthiness=True, summary=None)

    def test_cannot_consolidate_empty_record(self):
        record = _record()
        record.end(datetime.now(UTC))
        with pytest.raises(ValueError, match="no turns"):
            record.mark_consolidated(worthiness=False, summary=None)


class TestConsolidationEligibility:
    def test_active_record_not_eligible(self):
        record = _record()
        record.add_turn(_turn())
        assert not record.is_eligible_for_consolidation

    def test_ended_record_with_turns_eligible(self):
        record = _record()
        record.add_turn(_turn())
        record.end(datetime.now(UTC))
        assert record.is_eligible_for_consolidation

    def test_ended_empty_record_not_eligible(self):
        record = _record()
        record.end(datetime.now(UTC))
        assert not record.is_eligible_for_consolidation

    def test_consolidated_record_not_eligible(self):
        record = _record()
        record.add_turn(_turn())
        record.end(datetime.now(UTC))
        record.mark_consolidated(worthiness=True, summary=None)
        assert not record.is_eligible_for_consolidation
