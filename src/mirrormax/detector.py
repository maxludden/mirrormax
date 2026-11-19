"""Work out whether a RealVNC session is active by reading RealVNC's log.

Why the log and not a port check: my iPad connects through RealVNC's cloud
connection, which tunnels over UDP rather than TCP 5900, so ``netstat`` never
sees it. RealVNC Server writes ``SServerAgent: Starting desktop`` when a viewer
session begins and ``SServerAgent: Stopping desktop`` when it ends, for direct
and cloud connections alike. Whichever marker appeared last is the current
state. If neither marker is present (for example just after RealVNC rotates
the log) the state is *unknown* and nobody should touch the displays.

The module is split into pure parsing (:func:`last_marker`) and a small file
reader (:func:`detect`) that only reads new bytes since the previous pass.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from mirrormax.errors import LogUnreadableError

START_MARKER = "SServerAgent: Starting desktop"
STOP_MARKER = "SServerAgent: Stopping desktop"
TAIL_BYTES = 64 * 1024
"""How much of the log to read per step when scanning backwards for a marker."""

_MARKER_RE = re.compile(r"SServerAgent: (Starting|Stopping) desktop")
_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


class SessionState(StrEnum):
    """What the log says about the VNC session."""

    ACTIVE = "active"
    """The last marker was ``Starting desktop``: someone is connected."""
    ENDED = "ended"
    """The last marker was ``Stopping desktop``: nobody is connected."""
    UNKNOWN = "unknown"
    """No marker found: don't change anything."""


@dataclass(frozen=True, slots=True)
class Marker:
    """The most recent session marker found in some log text.

    Attributes:
        state: ``ACTIVE`` for a start marker, ``ENDED`` for a stop marker.
        line: The full log line containing the marker.
        timestamp: When RealVNC logged it, if the line carries a timestamp.
    """

    state: SessionState
    line: str
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class LogCursor:
    """Where the previous pass stopped reading, and what it concluded.

    Attributes:
        inode: Inode of the log file at the last read (detects rotation).
        offset: Byte offset just after the last complete line that was read.
        state: The session state known at that point.
        marker_time: Timestamp of the marker that set ``state``.
    """

    inode: int = 0
    offset: int = 0
    state: SessionState = SessionState.UNKNOWN
    marker_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    """The result of one detection pass.

    Attributes:
        state: The current session state.
        marker_time: Timestamp of the marker behind ``state``, if known.
        cursor: The cursor to pass to the next call.
        rotated: Whether a log rotation (or truncation) was detected.
        reason: Short human-readable explanation of how the state was reached.
    """

    state: SessionState
    marker_time: datetime | None
    cursor: LogCursor
    rotated: bool
    reason: str


def parse_timestamp(line: str) -> datetime | None:
    """Extract the ISO-8601 timestamp RealVNC puts at the start of each line.

    Args:
        line: A log line such as ``<14> 2026-11-19T18:28:56.704Z host ...``.

    Returns:
        The timestamp, or ``None`` if the line has no parseable timestamp.
    """
    match = _TIME_RE.search(line)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None


def last_marker(text: str) -> Marker | None:
    """Find the last session marker in a block of log text.

    Args:
        text: Log text; may contain many lines.

    Returns:
        The last marker, or ``None`` if the text contains neither marker.
    """
    found: re.Match[str] | None = None
    for found in _MARKER_RE.finditer(text):  # noqa: B007 - we want the last match
        pass
    if found is None:
        return None
    start = text.rfind("\n", 0, found.start()) + 1
    end = text.find("\n", found.end())
    line = text[start : end if end != -1 else len(text)].strip()
    state = SessionState.ACTIVE if found.group(1) == "Starting" else SessionState.ENDED
    return Marker(state=state, line=line, timestamp=parse_timestamp(line))


def _read_bytes(path: Path, start: int, length: int) -> bytes:
    """Read ``length`` raw bytes from ``path`` starting at ``start``.

    Args:
        path: File to read.
        start: Byte offset to start from.
        length: Maximum number of bytes to read.

    Returns:
        The bytes read (fewer if the file ends first).
    """
    with path.open("rb") as fh:
        fh.seek(start)
        return fh.read(length)


def _read(path: Path, start: int, length: int) -> str:
    """Read ``length`` bytes from ``path`` starting at ``start`` as text.

    Args:
        path: File to read.
        start: Byte offset to start from.
        length: Maximum number of bytes to read.

    Returns:
        The bytes decoded as UTF-8 (invalid sequences replaced).
    """
    return _read_bytes(path, start, length).decode("utf-8", errors="replace")


def _scan_backwards(path: Path, end: int, chunk: int) -> Marker | None:
    """Search a file backwards, ``chunk`` bytes at a time, for the last marker.

    Only the final chunk is read in the common case, so a large log costs
    almost nothing. Chunks overlap by a line's worth so a marker split across
    a chunk boundary is still found.

    Args:
        path: The log file.
        end: Byte offset to treat as the end of the file.
        chunk: Bytes to read per step.

    Returns:
        The last marker before ``end``, or ``None`` if the file has none.
    """
    overlap = 512
    pos = end
    while pos > 0:
        start = max(0, pos - chunk)
        text = _read(path, start, pos - start + (overlap if pos < end else 0))
        marker = last_marker(text)
        if marker is not None:
            return marker
        pos = start
    return None


def detect(path: Path, cursor: LogCursor | None, chunk: int = TAIL_BYTES) -> Detection:
    """Run one detection pass over RealVNC's log.

    On the first pass (no cursor) or after a rotation the log is scanned
    backwards from the end. Otherwise only bytes appended since the cursor
    are read. Only complete lines are consumed, so a line RealVNC is still
    writing is picked up next time.

    Args:
        path: Path of RealVNC's log, normally ``/Library/Logs/rvncserver.log``.
        cursor: The cursor from the previous pass, or ``None``.
        chunk: Bytes per read step when scanning backwards.

    Returns:
        The detection result, including the cursor for the next pass.

    Raises:
        LogUnreadableError: The log exists but can't be read.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return Detection(
            state=SessionState.UNKNOWN,
            marker_time=None,
            cursor=LogCursor(),
            rotated=cursor is not None and cursor.inode != 0,
            reason="log file not found",
        )
    except PermissionError as exc:
        raise _unreadable(path) from exc
    if not os.access(path, os.R_OK):
        raise _unreadable(path)

    size = st.st_size
    rotated = (
        cursor is not None
        and cursor.inode != 0
        and (cursor.inode != st.st_ino or size < cursor.offset)
    )
    cold = cursor is None or cursor.inode == 0 or rotated

    try:
        if cold:
            # Read up to the last complete line, then search backwards.
            end = _complete_end(path, 0, size)
            marker = _scan_backwards(path, end, chunk)
            new_cursor = LogCursor(inode=st.st_ino, offset=end)
            if marker is None:
                return Detection(
                    state=SessionState.UNKNOWN,
                    marker_time=None,
                    cursor=new_cursor,
                    rotated=rotated,
                    reason="no session marker in log" + (" (rotated)" if rotated else ""),
                )
            new_cursor = replace(new_cursor, state=marker.state, marker_time=marker.timestamp)
            return Detection(marker.state, marker.timestamp, new_cursor, rotated, "scanned log")

        assert cursor is not None  # for type checkers: cold covers None
        if size == cursor.offset:
            return Detection(cursor.state, cursor.marker_time, cursor, False, "no new log lines")
        end = _complete_end(path, cursor.offset, size)
        text = _read(path, cursor.offset, end - cursor.offset)
    except PermissionError as exc:
        raise _unreadable(path) from exc

    marker = last_marker(text)
    if marker is None:
        new_cursor = replace(cursor, offset=end)
        return Detection(cursor.state, cursor.marker_time, new_cursor, False, "no new marker")
    new_cursor = LogCursor(st.st_ino, end, marker.state, marker.timestamp)
    return Detection(marker.state, marker.timestamp, new_cursor, False, "new marker")


def _complete_end(path: Path, start: int, size: int) -> int:
    """Return the offset just after the last newline in ``path[start:size]``.

    Args:
        path: The log file.
        start: Offset where unread data begins.
        size: Current file size.

    Returns:
        The offset after the last complete line, or ``start`` if no newline
        has been written yet.
    """
    if size <= start:
        return start
    tail_len = min(size - start, 4096)
    tail = _read_bytes(path, size - tail_len, tail_len)
    idx = tail.rfind(b"\n")
    if idx != -1:
        return size - tail_len + idx + 1
    if size - start <= 4096:
        return start
    # A single line longer than 4 KiB: fall back to a full search.
    data = _read_bytes(path, start, size - start)
    idx = data.rfind(b"\n")
    return start + idx + 1 if idx != -1 else start


def _unreadable(path: Path) -> LogUnreadableError:
    """Build the error for a log file we're not allowed to read.

    Args:
        path: The log file.

    Returns:
        A ready-to-raise error with a fix.
    """
    return LogUnreadableError(
        f"Can't read {path}",
        cause="macOS is blocking access to the log (privacy protection).",
        fix="System Settings → Privacy & Security → Full Disk Access → add your terminal "
        "and the mirrormax binary (`which mirrormax`).",
    )
