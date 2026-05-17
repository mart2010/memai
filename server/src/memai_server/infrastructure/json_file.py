# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
import json
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID

from ..domain.model import Language, Speaker, Turn
from ..services.ports import SessionInfo


class JSONLTurnLogger:
    """Appends turns and markers to per-session JSONL files under log_dir.

    One file per session (one client connection). Filename: YYYY-MM-DD_<session_id>.jsonl.
    Date is derived from the first write for each session (turn timestamp, marker
    timestamp, or ended_at from close — whichever arrives first).
    """

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._session_dates: dict[UUID, str] = {}

    def _file_path(self, session_id: UUID) -> Path:
        return self._log_dir / f"{self._session_dates[session_id]}_{session_id}.jsonl"

    def _register(self, session_id: UUID, ts: datetime) -> None:
        if session_id not in self._session_dates:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._session_dates[session_id] = ts.date().isoformat()

    def append(self, session_id: UUID, turn: Turn, marker: str | None = None) -> None:
        self._register(session_id, turn.timestamp)
        line: dict = {
            "ts": turn.timestamp.isoformat(),
            "speaker": turn.speaker.value,
            "content": turn.content,
        }
        if turn.language is not None:
            line["language"] = turn.language.code
        if marker is not None:
            line["marker"] = marker
        with self._file_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None:
        self._register(session_id, ended_at)
        line = {"type": "session_closed", "ts": ended_at.isoformat(), "clean_exit": clean_exit}
        with self._file_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")


class JSONLSessionLogReader:
    """Reads session metadata and turn tails from JSONL log files.

    Identifies the most recent session by file modification time so that
    same-day sessions are ordered correctly.
    """

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def _log_files_newest_last(self) -> list[Path]:
        if not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)

    @staticmethod
    def _session_id_from_path(path: Path) -> UUID | None:
        parts = path.stem.split("_", 1)
        if len(parts) != 2:
            return None
        try:
            return UUID(parts[1])
        except ValueError:
            return None

    def get_previous(self) -> SessionInfo | None:
        files = self._log_files_newest_last()
        if not files:
            return None
        latest = files[-1]
        session_id = self._session_id_from_path(latest)
        if session_id is None:
            return None

        ended_at: datetime | None = None
        clean_exit = False
        last_ts: datetime | None = None

        with latest.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    ts = datetime.fromisoformat(data["ts"])
                    last_ts = ts
                    if data.get("type") == "session_closed":
                        ended_at = ts
                        clean_exit = bool(data.get("clean_exit", False))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        # For crashed sessions (no session_closed marker), fall back to last
        # recorded timestamp so StartSession can still evaluate recency.
        effective_ended_at = ended_at or last_ts
        if effective_ended_at is None:
            return None  # file was empty

        return SessionInfo(
            session_id=session_id,
            ended_at=effective_ended_at,
            # clean_exit is only True when the marker was explicitly written
            clean_exit=ended_at is not None and clean_exit,
        )

    def read_tail(self, session_id: UUID, max_turns: int) -> list[Turn]:
        matches = list(self._log_dir.glob(f"*_{session_id}.jsonl")) if self._log_dir.exists() else []
        if not matches:
            return []
        turns: list[Turn] = []
        with matches[0].open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if "speaker" not in data:
                        continue  # marker line
                    turns.append(Turn(
                        timestamp=datetime.fromisoformat(data["ts"]),
                        speaker=Speaker(data["speaker"]),
                        content=data["content"],
                        language=Language(data["language"]) if "language" in data else None,
                    ))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return turns[-max_turns:]
