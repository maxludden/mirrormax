"""Tests for log parsing and incremental reading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import LINES, LOG_TEXT, START_LINE, STOP_LINE, append

from mirrormax.detector import LogCursor, SessionState, detect, last_marker, parse_timestamp
from mirrormax.errors import LogUnreadableError


def test_real_sample_ends_with_stop() -> None:
    marker = last_marker(LOG_TEXT)
    assert marker is not None
    assert marker.state is SessionState.ENDED
    assert marker.timestamp is not None
    assert marker.timestamp.isoformat() == "2026-11-19T18:31:07.460000+00:00"


def test_no_marker_returns_none() -> None:
    assert last_marker("just noise\nmore noise\n") is None


def test_parse_timestamp_handles_missing() -> None:
    assert parse_timestamp("no time here") is None


def test_cold_start_finds_active(log_path: Path) -> None:
    append(log_path, LINES[: START_LINE + 1])
    det = detect(log_path, None)
    assert det.state is SessionState.ACTIVE
    assert det.cursor.offset == log_path.stat().st_size


def test_empty_log_is_unknown(log_path: Path) -> None:
    det = detect(log_path, None)
    assert det.state is SessionState.UNKNOWN


def test_missing_log_is_unknown(tmp_path: Path) -> None:
    det = detect(tmp_path / "nope.log", None)
    assert det.state is SessionState.UNKNOWN
    assert det.reason == "log file not found"


def test_incremental_reads_only_new_lines(log_path: Path) -> None:
    append(log_path, LINES[: START_LINE + 1])
    first = detect(log_path, None)
    append(log_path, LINES[START_LINE + 1 : STOP_LINE])  # noise only
    second = detect(log_path, first.cursor)
    assert second.state is SessionState.ACTIVE
    assert second.reason == "no new marker"
    append(log_path, [LINES[STOP_LINE]])
    third = detect(log_path, second.cursor)
    assert third.state is SessionState.ENDED
    assert third.reason == "new marker"


def test_no_growth_is_cheap(log_path: Path) -> None:
    append(log_path, LINES)
    first = detect(log_path, None)
    again = detect(log_path, first.cursor)
    assert again.reason == "no new log lines"
    assert again.state is first.state


def test_partial_line_waits_for_newline(log_path: Path) -> None:
    append(log_path, LINES[:START_LINE])
    first = detect(log_path, None)
    line = LINES[START_LINE]
    append(log_path, [line[:30]])  # RealVNC is mid-write
    mid = detect(log_path, first.cursor)
    assert mid.state is SessionState.UNKNOWN
    append(log_path, [line[30:]])
    done = detect(log_path, mid.cursor)
    assert done.state is SessionState.ACTIVE


def test_rotation_by_rename(log_path: Path) -> None:
    append(log_path, LINES[: START_LINE + 1])
    first = detect(log_path, None)
    os.rename(log_path, log_path.with_suffix(".log.1"))
    log_path.write_text("", encoding="utf-8")  # fresh, empty log: no marker
    rotated = detect(log_path, first.cursor)
    assert rotated.rotated
    assert rotated.state is SessionState.UNKNOWN


def test_rotation_by_truncation(log_path: Path) -> None:
    append(log_path, LINES)
    first = detect(log_path, None)
    log_path.write_text(LINES[START_LINE], encoding="utf-8")
    det = detect(log_path, first.cursor)
    assert det.rotated
    assert det.state is SessionState.ACTIVE


def test_backwards_scan_across_chunks(log_path: Path) -> None:
    append(log_path, [LINES[START_LINE]])
    append(log_path, ["<14> 2026-11-19T18:29:00.000Z host x: noise " + "y" * 80 + "\n"] * 300)
    det = detect(log_path, None, chunk=1024)
    assert det.state is SessionState.ACTIVE


def test_unreadable_log_raises(log_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    append(log_path, LINES)
    log_path.chmod(0)
    try:
        with pytest.raises(LogUnreadableError) as info:
            detect(log_path, LogCursor())
        assert "Full Disk Access" in info.value.fix
    finally:
        log_path.chmod(0o644)
