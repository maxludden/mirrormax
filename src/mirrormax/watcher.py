"""Follow RealVNC's log with kqueue instead of polling.

Why kqueue and not FSEvents (``watchdog``/``watchfiles``): Apple's guidance is
that kqueue's ``EVFILT_VNODE`` filter is the right tool for watching a
*single file*, while FSEvents is designed for directory trees and coalesces
events with latency. We watch exactly one file, so the stdlib ``select``
module gives instant wake-ups, zero CPU while idle and no extra dependency.

The watch loop also re-checks on a heartbeat timer, so a missed event, a log
rotation edge case or a sleep/wake can never leave the displays stuck.
"""

from __future__ import annotations

import contextlib
import os
import select
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from loguru import logger

from mirrormax.controller import Controller, Outcome
from mirrormax.errors import MirrormaxError

O_EVTONLY = getattr(os, "O_EVTONLY", 0x8000)
"""macOS open flag: open for event notifications only, so volumes can still unmount."""


class WatchEvent(StrEnum):
    """Why :meth:`LogWatcher.wait` returned."""

    CHANGED = "changed"
    """The log was written to or extended."""
    ROTATED = "rotated"
    """The log was deleted, renamed or revoked and has been reopened."""
    MISSING = "missing"
    """The log doesn't exist right now."""
    TIMEOUT = "timeout"
    """Nothing happened before the timeout (heartbeat)."""


class LogWatcher(Protocol):
    """Something that blocks until the log changes or a timeout passes."""

    def wait(self, timeout: float) -> WatchEvent:
        """Block until the log changes or ``timeout`` seconds pass.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            Why it returned.
        """
        ...

    def close(self) -> None:
        """Release any resources (file descriptors, kqueues)."""
        ...


class KqueueWatcher:
    """:class:`LogWatcher` using macOS/BSD kqueue on a single file."""

    def __init__(self, path: Path, missing_backoff: float = 2.0) -> None:
        """Create the watcher and start watching ``path`` if it exists.

        Args:
            path: The file to watch.
            missing_backoff: Initial seconds between reopen attempts while the
                file is missing (doubles up to 30 s).
        """
        # select.kqueue only exists on macOS/BSD; it's looked up here, not at
        # import time, so the rest of the package (and its tests) run anywhere.
        self.path = path
        self._kq = select.kqueue()
        self._fd: int | None = None
        self._initial_backoff = missing_backoff
        self._backoff = missing_backoff
        self._fflags = (
            select.KQ_NOTE_WRITE
            | select.KQ_NOTE_EXTEND
            | select.KQ_NOTE_DELETE
            | select.KQ_NOTE_RENAME
            | select.KQ_NOTE_REVOKE
            | select.KQ_NOTE_ATTRIB
        )
        self._open()

    def _open(self) -> bool:
        """Open the file and register it with the kqueue.

        Returns:
            True if the file is now being watched.
        """
        sel = select
        try:
            fd = os.open(self.path, O_EVTONLY)
        except OSError:
            return False
        event = sel.kevent(
            fd,
            filter=sel.KQ_FILTER_VNODE,
            flags=sel.KQ_EV_ADD | sel.KQ_EV_ENABLE | sel.KQ_EV_CLEAR,
            fflags=self._fflags,
        )
        self._kq.control([event], 0, 0)
        self._fd = fd
        self._backoff = self._initial_backoff
        logger.debug("watching {} (fd {})", self.path, fd)
        return True

    def _close_fd(self) -> None:
        """Stop watching the current file descriptor (closing removes its kevent)."""
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None

    def wait(self, timeout: float) -> WatchEvent:
        """Block until the log changes, is rotated, or ``timeout`` passes.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            Why it returned.
        """
        sel = select
        if self._fd is None:
            time.sleep(min(timeout, self._backoff))
            self._backoff = min(self._backoff * 2, 30.0)
            return WatchEvent.ROTATED if self._open() else WatchEvent.MISSING
        events = self._kq.control(None, 1, timeout)
        if not events:
            return WatchEvent.TIMEOUT
        fflags = events[0].fflags
        if fflags & (sel.KQ_NOTE_DELETE | sel.KQ_NOTE_RENAME | sel.KQ_NOTE_REVOKE):
            logger.info("log rotated or removed; reopening {}", self.path)
            self._close_fd()
            self._open()
            return WatchEvent.ROTATED
        return WatchEvent.CHANGED

    def close(self) -> None:
        """Close the file descriptor and the kqueue."""
        self._close_fd()
        with contextlib.suppress(OSError):
            self._kq.close()


class WatchStopped(BaseException):
    """Raised (from a signal handler) to stop the watch loop cleanly."""


OutcomeCallback = Callable[[WatchEvent, Outcome | None, MirrormaxError | None], None]
"""Called after every pass with the event and either an outcome or an error."""


def watch_loop(
    controller: Controller,
    watcher: LogWatcher,
    heartbeat: float,
    on_pass: OutcomeCallback | None = None,
    max_passes: int | None = None,
) -> int:
    """Run detection passes whenever the log changes, plus on a heartbeat.

    Args:
        controller: Performs each detection pass.
        watcher: Source of log-change events.
        heartbeat: Seconds between unconditional passes.
        on_pass: Optional callback for UI/logging after each pass.
        max_passes: Stop after this many passes (tests); ``None`` runs forever.

    Returns:
        The number of passes run.

    Raises:
        WatchStopped: Propagated if raised by a signal handler, after cleanup.
    """
    passes = 0

    def one(event: WatchEvent) -> None:
        nonlocal passes
        passes += 1
        try:
            outcome = controller.check()
        except MirrormaxError as exc:
            logger.error("{}: {}", exc.what, exc.cause)
            if on_pass:
                on_pass(event, None, exc)
            return
        if on_pass:
            on_pass(event, outcome, None)

    try:
        one(WatchEvent.TIMEOUT)  # start-up pass: sync with whatever happened while stopped
        while max_passes is None or passes < max_passes:
            event = watcher.wait(heartbeat)
            if event is WatchEvent.MISSING:
                continue
            one(event)
    finally:
        watcher.close()
    return passes
