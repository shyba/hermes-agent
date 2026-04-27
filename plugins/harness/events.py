"""Append-only event logs for Hermes harness runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .paths import ensure_run_dir, get_active_run, run_dir


EVENTS_FILENAME = "events.jsonl"


class EventLogCorruptionError(RuntimeError):
    """Raised when an events.jsonl file contains malformed or inconsistent data."""


@dataclass(frozen=True)
class HarnessEvent:
    """A parsed harness event."""

    s: int
    t: str
    payload: dict[str, Any]

    @property
    def event(self) -> str | None:
        value = self.payload.get("event")
        return value if isinstance(value, str) else None

    @property
    def ok(self) -> bool | None:
        value = self.payload.get("ok")
        return value if isinstance(value, bool) else None

    def to_dict(self) -> dict[str, Any]:
        return {"s": self.s, "t": self.t, **self.payload}


class EventLog:
    """Validated append-only JSONL event writer with monotonic sequence numbers."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._next_s = self._scan_next_sequence()

    @classmethod
    def for_run(
        cls,
        run_id: str | None = None,
        repo_root: str | os.PathLike[str] | None = None,
    ) -> "EventLog":
        """Open the event log for a run id, or for the active run when omitted."""

        resolved_run_id = run_id or get_active_run(repo_root)
        if resolved_run_id is None:
            raise ValueError("run_id is required when no active harness run is set")
        return cls(ensure_run_dir(resolved_run_id, repo_root) / EVENTS_FILENAME)

    def append(self, t: str, payload: dict[str, Any] | None = None) -> HarnessEvent:
        """Append an event and return the persisted event object.

        ``t`` is the event family (`run`, `intent`, `tool`, `sensor`, etc.).
        Event-specific fields are kept at top level so the JSONL file stays easy
        to inspect and future ledger checks do not have to unpack nested blobs.
        A `ts` timestamp is added when the caller does not provide one; sequence
        number `s` remains the source of truth for ordering.
        """

        if not t:
            raise ValueError("t must be non-empty")
        fields = dict(payload or {})
        fields.pop("s", None)
        fields.pop("t", None)
        fields.setdefault("ts", _utc_now())

        with _exclusive_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            next_s = self._scan_next_sequence()
            event = HarnessEvent(s=next_s, t=t, payload=fields)
            line = json.dumps(
                event.to_dict(),
                separators=(",", ":"),
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
            self._next_s = next_s + 1
            return event

    def read_all(self) -> list[HarnessEvent]:
        """Read and validate all events."""

        return list(read_events(self.path))

    def _scan_next_sequence(self) -> int:
        if not self.path.exists():
            return 1
        last_s = 0
        for event in read_events(self.path):
            last_s = event.s
        return last_s + 1


def append_event(
    run_id: str,
    t: str,
    payload: dict[str, Any] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> HarnessEvent:
    """Append one event to a run's events.jsonl."""

    return EventLog.for_run(run_id, repo_root).append(t, payload)


def read_events(path: str | os.PathLike[str]) -> Iterator[HarnessEvent]:
    """Yield validated events from a JSONL log."""

    log_path = Path(path)
    if not log_path.exists():
        return

    expected_s = 1
    with log_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                raise EventLogCorruptionError(f"{log_path}:{line_no}: blank lines are not valid events")
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise EventLogCorruptionError(f"{log_path}:{line_no}: invalid JSON: {exc}") from exc

            event = _coerce_event(log_path, line_no, raw)
            if event.s != expected_s:
                raise EventLogCorruptionError(
                    f"{log_path}:{line_no}: expected sequence {expected_s}, found {event.s}"
                )
            expected_s += 1
            yield event


def events_path(
    run_id: str,
    repo_root: str | os.PathLike[str] | None = None,
    *,
    create: bool = True,
) -> Path:
    """Return the path to a run's events.jsonl file."""

    if create:
        directory = ensure_run_dir(run_id, repo_root)
    else:
        directory = run_dir(run_id, repo_root, create=False)
    return directory / EVENTS_FILENAME


def _coerce_event(path: Path, line_no: int, raw: Any) -> HarnessEvent:
    if not isinstance(raw, dict):
        raise EventLogCorruptionError(f"{path}:{line_no}: event must be a JSON object")

    s = raw.get("s")
    t = raw.get("t")

    if not isinstance(s, int) or s < 1:
        raise EventLogCorruptionError(f"{path}:{line_no}: s must be a positive integer")
    if not isinstance(t, str) or not t:
        raise EventLogCorruptionError(f"{path}:{line_no}: t must be a non-empty string")

    payload = dict(raw)
    payload.pop("s", None)
    payload.pop("t", None)
    return HarnessEvent(s=s, t=t, payload=payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - non-POSIX fallback
            yield
