from datetime import datetime, UTC, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from memai_server.domain.model import Language, Speaker, Turn
from memai_server.infrastructure.json_file import JSONLSessionLogReader, JSONLTurnLogger


def _now() -> datetime:
    return datetime.now(UTC)


def _turn(content: str, speaker: Speaker = Speaker.USER, lang: str = "en") -> Turn:
    return Turn(timestamp=_now(), speaker=speaker, content=content, language=Language(lang))


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "sessions"


class TestJSONLTurnLogger:
    def test_creates_file_on_first_append(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        sid = uuid4()
        logger.append(sid, _turn("hello"))
        files = list(log_dir.glob(f"*_{sid}.jsonl"))
        assert len(files) == 1

    def test_turn_fields_round_trip(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        t = _turn("bonjour", speaker=Speaker.ASSISTANT, lang="fr")
        logger.append(sid, t)
        turns = reader.read_tail(sid, max_turns=10)
        assert len(turns) == 1
        assert turns[0].content == "bonjour"
        assert turns[0].speaker == Speaker.ASSISTANT
        assert turns[0].language == Language("fr")

    def test_turn_without_language(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        t = Turn(timestamp=_now(), speaker=Speaker.USER, content="hi", language=None)
        logger.append(sid, t)
        turns = reader.read_tail(sid, max_turns=10)
        assert turns[0].language is None

    def test_multiple_turns_appended_in_order(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        for i in range(5):
            logger.append(sid, _turn(f"turn {i}"))
        turns = reader.read_tail(sid, max_turns=10)
        assert [t.content for t in turns] == [f"turn {i}" for i in range(5)]

    def test_marker_embedded_in_assistant_turn(self, log_dir: Path) -> None:
        import json
        logger = JSONLTurnLogger(log_dir)
        sid = uuid4()
        logger.append(sid, _turn("first"))
        logger.append(sid, _turn("response", speaker=Speaker.ASSISTANT), marker="topic_break")
        file = next(log_dir.glob(f"*_{sid}.jsonl"))
        lines = [json.loads(l) for l in file.read_text().splitlines()]
        assert len(lines) == 2
        assert lines[1]["marker"] == "topic_break"
        assert lines[0].get("marker") is None

    def test_close_writes_session_closed_marker(self, log_dir: Path) -> None:
        import json
        logger = JSONLTurnLogger(log_dir)
        sid = uuid4()
        logger.append(sid, _turn("hi"))
        logger.close(sid, ended_at=_now(), clean_exit=True)
        file = next(log_dir.glob(f"*_{sid}.jsonl"))
        lines = [json.loads(l) for l in file.read_text().splitlines()]
        closed = [l for l in lines if l.get("type") == "session_closed"]
        assert len(closed) == 1
        assert closed[0]["clean_exit"] is True

    def test_close_without_prior_append(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        sid = uuid4()
        logger.close(sid, ended_at=_now(), clean_exit=False)
        files = list(log_dir.glob(f"*_{sid}.jsonl"))
        assert len(files) == 1


class TestJSONLSessionLogReader:
    def test_returns_none_when_log_dir_absent(self, tmp_path: Path) -> None:
        reader = JSONLSessionLogReader(tmp_path / "nonexistent")
        assert reader.get_previous() is None

    def test_returns_none_when_no_files(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True)
        reader = JSONLSessionLogReader(log_dir)
        assert reader.get_previous() is None

    def test_get_previous_clean_exit(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        ended = _now()
        logger.append(sid, _turn("hi"))
        logger.close(sid, ended_at=ended, clean_exit=True)
        info = reader.get_previous()
        assert info is not None
        assert info.session_id == sid
        assert info.clean_exit is True

    def test_get_previous_unclean_exit(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        logger.append(sid, _turn("hi"))
        # no close — simulates crash
        info = reader.get_previous()
        assert info is not None
        assert info.session_id == sid
        assert info.clean_exit is False

    def test_get_previous_returns_none_for_empty_file(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True)
        (log_dir / f"2025-01-01_{uuid4()}.jsonl").write_text("")
        reader = JSONLSessionLogReader(log_dir)
        assert reader.get_previous() is None

    def test_get_previous_picks_most_recent_session(self, log_dir: Path) -> None:
        import os, time as _time
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        old_sid = uuid4()
        new_sid = uuid4()
        logger.append(old_sid, _turn("old session"))
        logger.close(old_sid, ended_at=_now(), clean_exit=True)
        # Push the old file's mtime 60s into the past so sorting is unambiguous.
        old_file = next(log_dir.glob(f"*_{old_sid}.jsonl"))
        past = _time.time() - 60
        os.utime(old_file, (past, past))
        logger.append(new_sid, _turn("new session"))
        logger.close(new_sid, ended_at=_now(), clean_exit=True)
        info = reader.get_previous()
        assert info is not None
        assert info.session_id == new_sid

    def test_read_tail_returns_last_n_turns(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        for i in range(10):
            logger.append(sid, _turn(f"turn {i}"))
        tail = reader.read_tail(sid, max_turns=3)
        assert len(tail) == 3
        assert [t.content for t in tail] == ["turn 7", "turn 8", "turn 9"]

    def test_read_tail_ignores_marker_field(self, log_dir: Path) -> None:
        logger = JSONLTurnLogger(log_dir)
        reader = JSONLSessionLogReader(log_dir)
        sid = uuid4()
        logger.append(sid, _turn("user question"))
        logger.append(sid, _turn("answer", speaker=Speaker.ASSISTANT), marker="topic_break")
        turns = reader.read_tail(sid, max_turns=10)
        assert len(turns) == 2
        assert turns[0].content == "user question"
        assert turns[1].content == "answer"

    def test_read_tail_unknown_session_returns_empty(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True)
        reader = JSONLSessionLogReader(log_dir)
        assert reader.read_tail(uuid4(), max_turns=10) == []
