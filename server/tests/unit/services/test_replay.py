from datetime import datetime, timedelta, UTC
from uuid import UUID, uuid4

import pytest

from memai_server.domain.events import ConversationBoundaryType
from memai_server.domain.model import AssistantPersona, GENERAL_ASSISTANT_ID, Language, Speaker
from memai_server.services.ports import SessionLine
from memai_server.services.replay import TurnLogReplayer, _group_into_conversations

from tests.fakes.fakes import (
    FakeConversationRepository,
    FakePersonaRepository,
    FakeSessionReplayReader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _ts(offset_seconds: int = 0) -> datetime:
    return _BASE + timedelta(seconds=offset_seconds)


def _user(offset: int, content: str = "Hello") -> SessionLine:
    return SessionLine(ts=_ts(offset), speaker=Speaker.USER, content=content)


def _asst(offset: int, content: str = "Hi", marker: ConversationBoundaryType | None = None) -> SessionLine:
    return SessionLine(ts=_ts(offset), speaker=Speaker.ASSISTANT, content=content, marker=marker)


def _closed(offset: int, clean_exit: bool = True) -> SessionLine:
    return SessionLine(ts=_ts(offset), is_session_closed=True, clean_exit=clean_exit)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are helpful.")


def _make_replayer(
    sessions: list[tuple[UUID, list[SessionLine]]] | None = None,
    existing_conversations: int = 0,
) -> tuple[TurnLogReplayer, FakeConversationRepository]:
    reader = FakeSessionReplayReader(sessions or [])
    conversation_repo = FakeConversationRepository()
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())

    # Pre-seed conversations so get_last_open_id works in continuation tests
    for _ in range(existing_conversations):
        from memai_server.domain.model import Conversation, Turn
        conv = Conversation(
            id=None,
            started_at=_ts(-100),
            ended_at=_ts(-10),
            persona_id=GENERAL_ASSISTANT_ID,
            turns=[Turn(timestamp=_ts(-50), speaker=Speaker.USER, content="old turn")],
        )
        conversation_repo.save_new(conv, session_id=uuid4())

    replayer = TurnLogReplayer(
        session_reader=reader,
        conversation_repo=conversation_repo,
        persona_repo=persona_repo,
    )
    return replayer, conversation_repo


# ---------------------------------------------------------------------------
# Tests for _group_into_conversations (pure function)
# ---------------------------------------------------------------------------

class TestGroupIntoConversations:
    def test_single_exchange_no_marker(self):
        lines = [_user(0), _asst(1), _closed(2)]
        groups = _group_into_conversations(lines)
        assert len(groups) == 1
        assert len(groups[0].turns) == 2
        assert groups[0].is_continuation is False
        assert groups[0].ended_at == _ts(2)

    def test_topic_continuation_first_assistant_turn(self):
        lines = [_user(0), _asst(1, marker=ConversationBoundaryType.CONTINUATION), _user(2), _asst(3), _closed(4)]
        groups = _group_into_conversations(lines)
        assert len(groups) == 1
        assert groups[0].is_continuation is True
        assert len(groups[0].turns) == 4

    def test_conversation_boundary_on_first_assistant_ignored(self):
        """Boundary on the very first assistant turn doesn't split — whole session is one new conversation."""
        lines = [_user(0), _asst(1, marker=ConversationBoundaryType.BREAK), _user(2), _asst(3), _closed(4)]
        groups = _group_into_conversations(lines)
        assert len(groups) == 1
        assert groups[0].is_continuation is False
        assert len(groups[0].turns) == 4

    def test_conversation_boundary_mid_session_splits(self):
        lines = [
            _user(0), _asst(1),
            _user(2), _asst(3, marker=ConversationBoundaryType.BREAK),
            _user(4), _asst(5),
            _closed(6),
        ]
        groups = _group_into_conversations(lines)
        assert len(groups) == 2
        assert len(groups[0].turns) == 4   # first 2 exchanges
        assert groups[0].ended_at == _ts(3)
        assert groups[0].is_continuation is False
        assert len(groups[1].turns) == 2   # last exchange
        assert groups[1].ended_at == _ts(6)
        assert groups[1].is_continuation is False

    def test_topic_continuation_then_mid_session_split(self):
        lines = [
            _user(0), _asst(1, marker=ConversationBoundaryType.CONTINUATION),
            _user(2), _asst(3, marker=ConversationBoundaryType.BREAK),
            _user(4), _asst(5),
            _closed(6),
        ]
        groups = _group_into_conversations(lines)
        assert len(groups) == 2
        assert groups[0].is_continuation is True   # first group extends prior conversation
        assert groups[1].is_continuation is False  # second group is a fresh conversation

    def test_crashed_session_uses_last_turn_timestamp(self):
        """No session_closed → ended_at set to last turn's timestamp."""
        lines = [_user(0), _asst(1), _user(2)]
        groups = _group_into_conversations(lines)
        assert len(groups) == 1
        assert groups[0].ended_at == _ts(2)

    def test_empty_lines_returns_no_groups(self):
        assert _group_into_conversations([]) == []

    def test_only_session_closed_returns_no_groups(self):
        assert _group_into_conversations([_closed(0)]) == []


# ---------------------------------------------------------------------------
# Tests for TurnLogReplayer.execute()
# ---------------------------------------------------------------------------

class TestTurnLogReplayer:
    def test_no_sessions_returns_zero(self):
        replayer, repo = _make_replayer()
        assert replayer.execute() == 0
        assert len(repo._records) == 0

    def test_single_session_creates_one_conversation(self):
        sid = uuid4()
        lines = [_user(0), _asst(1), _user(2), _asst(3), _closed(4)]
        replayer, repo = _make_replayer(sessions=[(sid, lines)])

        count = replayer.execute()

        assert count == 1
        assert len(repo._records) == 1
        conv = list(repo._records.values())[0]
        assert len(conv.turns) == 4
        assert conv.ended_at == _ts(4)
        assert conv.persona_id == GENERAL_ASSISTANT_ID

    def test_topic_continuation_extends_existing_conversation(self):
        sid = uuid4()
        lines = [_user(0), _asst(1, marker=ConversationBoundaryType.CONTINUATION), _user(2), _asst(3), _closed(4)]
        replayer, repo = _make_replayer(sessions=[(sid, lines)], existing_conversations=1)
        prior_id = repo.get_last_open_id()

        replayer.execute()

        # Existing conversation should be extended, no new conversation created
        assert len(repo._records) == 1
        conv = repo._records[prior_id]
        assert any(t.content == "Hello" for t in conv.turns)
        assert conv.ended_at == _ts(4)

    def test_topic_continuation_with_no_prior_conversation_creates_new(self):
        """When DB has no prior conversation, topic_continuation falls back to new conversation."""
        sid = uuid4()
        lines = [_user(0), _asst(1, marker=ConversationBoundaryType.CONTINUATION), _closed(2)]
        replayer, repo = _make_replayer(sessions=[(sid, lines)])

        replayer.execute()

        assert len(repo._records) == 1
        assert not list(repo._records.values())[0].consolidated

    def test_mid_session_split_creates_two_conversations(self):
        sid = uuid4()
        lines = [
            _user(0), _asst(1),
            _user(2), _asst(3, marker=ConversationBoundaryType.BREAK),
            _user(4), _asst(5),
            _closed(6),
        ]
        replayer, repo = _make_replayer(sessions=[(sid, lines)])

        replayer.execute()

        assert len(repo._records) == 2
        convs = sorted(repo._records.values(), key=lambda c: c.started_at)
        assert len(convs[0].turns) == 4
        assert len(convs[1].turns) == 2

    def test_multiple_sessions_processed_oldest_first(self):
        sid1, sid2 = uuid4(), uuid4()
        lines1 = [_user(0), _asst(1), _closed(2)]
        lines2 = [_user(10), _asst(11), _closed(12)]
        replayer, repo = _make_replayer(sessions=[(sid1, lines1), (sid2, lines2)])

        count = replayer.execute()

        assert count == 2
        assert len(repo._records) == 2
        convs = sorted(repo._records.values(), key=lambda c: c.started_at)
        assert convs[0].turns[0].timestamp == _ts(0)
        assert convs[1].turns[0].timestamp == _ts(10)

    def test_already_persisted_session_is_skipped(self):
        sid1, sid2 = uuid4(), uuid4()
        lines1 = [_user(0), _asst(1), _closed(2)]
        lines2 = [_user(10), _asst(11), _closed(12)]
        replayer, repo = _make_replayer(sessions=[(sid1, lines1), (sid2, lines2)])

        # First run processes both
        replayer.execute()
        assert len(repo._records) == 2

        # Second run: both sessions are now persisted → nothing to do
        count = replayer.execute()
        assert count == 0
        assert len(repo._records) == 2

    def test_crashed_session_is_replayed(self):
        """Sessions without session_closed (crash recovery) are still replayed."""
        sid = uuid4()
        lines = [_user(0), _asst(1)]  # no session_closed
        replayer, repo = _make_replayer(sessions=[(sid, lines)])

        replayer.execute()

        assert len(repo._records) == 1
        conv = list(repo._records.values())[0]
        assert conv.ended_at == _ts(1)  # last turn's timestamp
