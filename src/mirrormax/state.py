"""Persistent runtime state and the process lock.

State lives in ``~/.local/state/mirrormax/state.json`` and is always written
atomically (temp file in the same directory, then ``os.replace``) so a crash
or power loss mid-write can never leave a half-written file behind.

The lock (``fcntl.flock`` on ``mirrormax.lock``) makes sure two mirrormax
processes, say the launch agent and a manual ``mirrormax restore``, never
switch displays at the same time.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from mirrormax.detector import LogCursor, SessionState
from mirrormax.errors import LockHeldError, StateError

STATE_VERSION = 1


class Mode(StrEnum):
    """Which arrangement mirrormax believes is applied."""

    NORMAL = "normal"
    """Displays are in their usual (non-mirrored) arrangement."""
    MIRRORED = "mirrored"
    """mirrormax mirrored the displays for a VNC session."""


@dataclass(slots=True)
class MigrationRecord:
    """What ``mirrormax migrate`` did, so it can be reported and rolled back.

    Attributes:
        migrated_at: When the migration ran.
        legacy_was_mirrored: Whether the legacy script had displays mirrored then.
        imported_layouts: Names of layouts imported from the legacy script.
        rolled_back_at: When it was rolled back, if it was.
        clean_cycles: Connect/disconnect cycles handled by mirrormax since migrating.
    """

    migrated_at: datetime
    legacy_was_mirrored: bool = False
    imported_layouts: list[str] = field(default_factory=list)
    rolled_back_at: datetime | None = None
    clean_cycles: int = 0


@dataclass(slots=True)
class State:
    """Everything mirrormax remembers between runs.

    Attributes:
        mode: The arrangement mirrormax believes is applied.
        snapshot: Layout args captured just before mirroring (restored afterwards).
        snapshot_taken: When the snapshot was captured.
        cursor: Where the last log read stopped.
        last_switch: When displays were last switched.
        last_switch_action: ``"mirrored"`` or ``"restored"``.
        last_check: When the last detection pass ran.
        warning: Current warning (drift, unknown state...), cleared when resolved.
        shadow_note: In shadow mode, the last thing mirrormax *would* have done.
        session_started: When the current VNC session started, if active.
        migration: Record of ``mirrormax migrate``, if run.
    """

    mode: Mode = Mode.NORMAL
    snapshot: list[str] | None = None
    snapshot_taken: datetime | None = None
    cursor: LogCursor = field(default_factory=LogCursor)
    last_switch: datetime | None = None
    last_switch_action: str | None = None
    last_check: datetime | None = None
    warning: str | None = None
    shadow_note: str | None = None
    session_started: datetime | None = None
    migration: MigrationRecord | None = None


def now() -> datetime:
    """Return the current time as an aware UTC datetime.

    Returns:
        The current time.
    """
    return datetime.now(UTC)


def _dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp from JSON.

    Args:
        value: A string or ``None``.

    Returns:
        The datetime, or ``None``.
    """
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _encode(value: Any) -> Any:
    """JSON ``default`` hook for datetimes and enums.

    Args:
        value: A value json can't serialise natively.

    Returns:
        A serialisable representation.

    Raises:
        TypeError: The value has no known representation.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"Can't serialise {type(value).__name__}")


def to_dict(state: State) -> dict[str, Any]:
    """Convert state to a JSON-ready dict.

    Args:
        state: The state.

    Returns:
        A dict with only JSON-compatible values.
    """
    data: dict[str, Any] = json.loads(json.dumps(asdict(state), default=_encode))
    data["version"] = STATE_VERSION
    return data


def from_dict(data: dict[str, Any]) -> State:
    """Rebuild state from its JSON form.

    Args:
        data: The parsed JSON.

    Returns:
        The state.

    Raises:
        StateError: A field has an unexpected value.
    """
    try:
        cur = data.get("cursor") or {}
        mig = data.get("migration")
        migration = None
        if isinstance(mig, dict):
            migration = MigrationRecord(
                migrated_at=_dt(mig["migrated_at"]) or now(),
                legacy_was_mirrored=bool(mig.get("legacy_was_mirrored", False)),
                imported_layouts=list(mig.get("imported_layouts", [])),
                rolled_back_at=_dt(mig.get("rolled_back_at")),
                clean_cycles=int(mig.get("clean_cycles", 0)),
            )
        snapshot = data.get("snapshot")
        return State(
            mode=Mode(data.get("mode", Mode.NORMAL.value)),
            snapshot=list(snapshot) if isinstance(snapshot, list) else None,
            snapshot_taken=_dt(data.get("snapshot_taken")),
            cursor=LogCursor(
                inode=int(cur.get("inode", 0)),
                offset=int(cur.get("offset", 0)),
                state=SessionState(cur.get("state", SessionState.UNKNOWN.value)),
                marker_time=_dt(cur.get("marker_time")),
            ),
            last_switch=_dt(data.get("last_switch")),
            last_switch_action=data.get("last_switch_action"),
            last_check=_dt(data.get("last_check")),
            warning=data.get("warning"),
            shadow_note=data.get("shadow_note"),
            session_started=_dt(data.get("session_started")),
            migration=migration,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateError(
            "The state file is corrupt",
            cause=str(exc),
            fix="Move ~/.local/state/mirrormax/state.json aside; mirrormax will rebuild it.",
        ) from exc


class StateStore:
    """Reads and atomically writes the JSON state file."""

    def __init__(self, path: Path) -> None:
        """Create the store.

        Args:
            path: The state file.
        """
        self.path = path

    def load(self) -> State:
        """Load state, or return a fresh state if the file doesn't exist.

        Returns:
            The state.

        Raises:
            StateError: The file exists but can't be read or parsed.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return State()
        except OSError as exc:
            raise StateError(f"Can't read {self.path}", cause=str(exc)) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StateError(
                "The state file is corrupt",
                cause=str(exc),
                fix=f"Move {self.path} aside; mirrormax will rebuild it.",
            ) from exc
        if not isinstance(data, dict):
            raise StateError("The state file is corrupt", cause="Top level isn't an object.")
        return from_dict(data)

    def save(self, state: State) -> None:
        """Write state atomically.

        Args:
            state: The state to save.

        Raises:
            StateError: The file couldn't be written.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(to_dict(state), fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            raise StateError(f"Can't write {self.path}", cause=str(exc)) from exc


@contextlib.contextmanager
def file_lock(path: Path, wait: float = 10.0) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``path`` for the duration of the block.

    Args:
        path: The lock file (created if needed).
        wait: Seconds to keep retrying before giving up.

    Yields:
        Nothing; the lock is held while the block runs.

    Raises:
        LockHeldError: Another process still holds the lock after ``wait``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    raise LockHeldError(
                        "Another mirrormax process is busy",
                        cause=f"{path} is locked (probably the launch agent mid-switch).",
                        fix="Wait a few seconds and try again.",
                    ) from exc
                time.sleep(0.1)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
