# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
from dataclasses import dataclass
from datetime import datetime

from ..domain.events import ConversationBoundaryType
from ..domain.model import GENERAL_ASSISTANT_ID, Conversation, Speaker, Turn
from .ports import (
    ConversationRepository,
    PersonaRepository,
    SessionLine,
    SessionReplayReader,
)


@dataclass
class _ConversationGroup:
    turns: list[Turn]
    ended_at: datetime | None
    is_continuation: bool  # True → extend the last open conversation in the DB


def _group_into_conversations(lines: list[SessionLine]) -> list[_ConversationGroup]:
    """Group session lines into conversation buckets using embedded markers.

    Rules:
    - topic_continuation on the first assistant turn of a session → the whole
      session extends the last open conversation (is_continuation=True).
    - conversation_boundary on a non-first assistant turn → split: turns up to
      and including that assistant turn close the current conversation; remaining
      turns start a new one.
    - conversation_boundary on the first assistant turn is ignored for grouping
      purposes — the session is already a new session, so all turns form one new
      conversation.
    """
    groups: list[_ConversationGroup] = []
    current_turns: list[Turn] = []
    is_continuation = False
    first_assistant_seen = False
    session_ended = False

    for line in lines:
        if line.is_session_closed:
            session_ended = True
            if current_turns:
                groups.append(_ConversationGroup(
                    turns=current_turns,
                    ended_at=line.ts,
                    is_continuation=is_continuation,
                ))
            break

        if line.speaker is None:
            continue

        turn = Turn(
            timestamp=line.ts,
            speaker=line.speaker,
            content=line.content or "",
            language=line.language,
        )
        current_turns.append(turn)

        if line.speaker == Speaker.ASSISTANT and not first_assistant_seen:
            first_assistant_seen = True
            # topic_continuation only valid on the first assistant turn of the first group
            if line.marker == ConversationBoundaryType.CONTINUATION and not groups:
                is_continuation = True
            # ConversationBoundaryType.BREAK on the very first assistant turn → no split

        elif line.marker == ConversationBoundaryType.BREAK and line.speaker == Speaker.ASSISTANT:
            # Mid-session split: close current group, start fresh
            groups.append(_ConversationGroup(
                turns=current_turns,
                ended_at=line.ts,
                is_continuation=is_continuation,
            ))
            current_turns = []
            is_continuation = False
            first_assistant_seen = False

    if not session_ended and current_turns:
        # Crashed session — no session_closed marker; use last turn timestamp
        groups.append(_ConversationGroup(
            turns=current_turns,
            ended_at=current_turns[-1].timestamp,
            is_continuation=is_continuation,
        ))

    return groups


class TurnLogReplayer:
    """Replays unprocessed JSONL session files into the DB.

    Triggered either at recovery (server start) or after a clean disconnect idle
    timer fires. In both cases it is idempotent: sessions already in the DB are
    detected and skipped via the monotonic scan invariant.
    """

    def __init__(
        self,
        session_reader: SessionReplayReader,
        conversation_repo: ConversationRepository,
        persona_repo: PersonaRepository,
    ) -> None:
        self._session_reader = session_reader
        self._conversation_repo = conversation_repo
        self._persona_repo = persona_repo

    def execute(self) -> int:
        """Replay all unprocessed sessions. Returns the number of sessions replayed."""
        sessions = self._session_reader.get_unprocessed(
            self._conversation_repo.is_session_persisted
        )
        if not sessions:
            return 0

        persona = self._persona_repo.get(GENERAL_ASSISTANT_ID)
        if persona is None:
            raise RuntimeError("GeneralAssistant not found — database not initialised")

        for session_id, lines in sessions:
            for group in _group_into_conversations(lines):
                if not group.turns:
                    continue

                if group.is_continuation:
                    last_id = self._conversation_repo.get_last_open_id()
                    if last_id is not None:
                        self._conversation_repo.extend_conversation(
                            last_id, session_id, group.turns, group.ended_at
                        )
                        continue
                    # No prior conversation — fall through and save as new

                conv = Conversation(
                    id=None,
                    started_at=group.turns[0].timestamp,
                    ended_at=group.ended_at,
                    persona_snapshot=persona,
                    turns=list(group.turns),
                )
                self._conversation_repo.save_new(conv, session_id)

        return len(sessions)
