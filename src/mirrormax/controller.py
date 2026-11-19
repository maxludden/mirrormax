"""The state machine that decides when to mirror and when to restore.

Transitions (``mode`` is what mirrormax believes is applied)::

    log says ACTIVE  + mode NORMAL    -> snapshot current layout, apply `mirror`  (MIRRORED)
    log says ENDED   + mode MIRRORED  -> apply snapshot (or `normal`)              (RESTORED)
    log says UNKNOWN                  -> do nothing                                 (NONE)
    anything else                     -> already in the right mode                  (NONE)

Guards, checked in this order before any switch:

* **Shadow mode**: if the legacy vnc-mirror agent is active it owns the
  displays, so mirrormax only records what it *would* do.
* **Drift**: every display ID in the target layout must be connected, or
  the switch is skipped with a warning.

Because ``mode`` only changes after a successful switch, each connect and
each disconnect causes at most one switch, however often the check runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from loguru import logger

from mirrormax.config import MIRROR, NORMAL, Config
from mirrormax.detector import Detection, SessionState, detect
from mirrormax.displays import DisplayBackend
from mirrormax.errors import LayoutError, MirrormaxError
from mirrormax.layouts import Layout, ListedLayout
from mirrormax.state import Mode, State, StateStore, file_lock, now


class Action(StrEnum):
    """What a pass did."""

    NONE = "none"
    """Nothing to do."""
    MIRRORED = "mirrored"
    """Displays were mirrored."""
    RESTORED = "restored"
    """The previous layout was restored."""
    SKIPPED = "skipped"
    """A switch was needed but skipped (drift or missing layout)."""
    SHADOW = "shadow"
    """A switch was needed but the legacy agent owns the displays."""
    APPLIED = "applied"
    """A named layout was applied by hand."""


EXIT_CODES: dict[Action, int] = {Action.MIRRORED: 10, Action.RESTORED: 11}
"""``mirrormax check`` exit codes; anything not listed exits 0."""


@dataclass(frozen=True, slots=True)
class LegacyStatus:
    """Whether the old vnc-mirror agent currently owns the displays.

    Attributes:
        active: True if mirrormax must stay in shadow mode.
        reason: Why, in a few words (e.g. ``"legacy agent loaded"``).
    """

    active: bool
    reason: str = ""


LegacyGuard = Callable[[], LegacyStatus]
"""Callable that reports the legacy agent's status."""


def no_legacy() -> LegacyStatus:
    """Legacy guard that always reports the legacy agent as inactive.

    Returns:
        An inactive status.
    """
    return LegacyStatus(active=False)


@dataclass(frozen=True, slots=True)
class Outcome:
    """The result of one controller operation, for the UI and ``--json``.

    Attributes:
        action: What happened.
        message: One-line human summary.
        mode: The mode after the operation.
        session: Session state from the log (``None`` for manual operations).
        layout: Name of the layout applied (or that would be applied).
        command: The displayplacer command (always set for dry runs).
        duration: Seconds the switch took, if one happened.
        warning: A warning to show, if any.
        dry_run: Whether this was a dry run.
        detection_reason: How the session state was determined.
        marker_time: Timestamp of the log marker behind the session state.
    """

    action: Action
    message: str
    mode: Mode
    session: SessionState | None = None
    layout: str | None = None
    command: list[str] | None = None
    duration: float | None = None
    warning: str | None = None
    dry_run: bool = False
    detection_reason: str = ""
    marker_time: datetime | None = None

    @property
    def exit_code(self) -> int:
        """Exit status for ``mirrormax check``."""
        return EXIT_CODES.get(self.action, 0)


class Controller:
    """Applies the state machine using injected collaborators."""

    def __init__(
        self,
        config: Config,
        store: StateStore,
        backend: DisplayBackend,
        lock_path: Path,
        legacy: LegacyGuard = no_legacy,
        clock: Callable[[], datetime] = now,
        lock_wait: float = 10.0,
    ) -> None:
        """Create the controller.

        Args:
            config: Loaded configuration (layouts, log path).
            store: Where state is persisted.
            backend: Display backend (displayplacer or a fake).
            lock_path: Lock file shared by every mirrormax process.
            legacy: Reports whether the legacy agent is active.
            clock: Source of the current time (injectable for tests).
            lock_wait: Seconds to wait for the lock.
        """
        self.config = config
        self.store = store
        self.backend = backend
        self.lock_path = lock_path
        self.legacy = legacy
        self.clock = clock
        self.lock_wait = lock_wait

    # ------------------------------------------------------------------ check
    def check(self, *, dry_run: bool = False) -> Outcome:
        """Run one detection pass and switch displays if needed.

        Args:
            dry_run: Decide and report, but change nothing (not even state).

        Returns:
            What happened.

        Raises:
            MirrormaxError: Detection or the switch failed. The log cursor is
                still saved, and ``mode`` is unchanged so the switch is retried.
        """
        with file_lock(self.lock_path, self.lock_wait):
            state = self.store.load()
            detection = detect(self.config.detection.log_path, state.cursor)
            state.cursor = detection.cursor
            state.last_check = self.clock()
            self._track_session(state, detection)
            try:
                outcome = self._decide(state, detection, dry_run=dry_run)
            except MirrormaxError as exc:
                state.warning = exc.what
                if not dry_run:
                    self.store.save(state)
                raise
            if not dry_run:
                self.store.save(state)
            return outcome

    def _track_session(self, state: State, detection: Detection) -> None:
        """Remember when the current session started (for ``status``).

        Args:
            state: State to update in place.
            detection: The latest detection.
        """
        if detection.state is SessionState.ACTIVE:
            state.session_started = detection.marker_time or state.session_started or self.clock()
        elif detection.state is SessionState.ENDED:
            state.session_started = None

    def _decide(self, state: State, detection: Detection, *, dry_run: bool) -> Outcome:
        """Pick and perform the transition for one detection.

        Args:
            state: Current state (updated in place).
            detection: The latest detection.
            dry_run: Don't apply anything.

        Returns:
            What happened.
        """
        outcome = self._transition(state, detection, dry_run=dry_run)
        return replace(
            outcome,
            session=detection.state,
            detection_reason=detection.reason,
            marker_time=detection.marker_time,
            dry_run=dry_run,
        )

    def _transition(self, state: State, detection: Detection, *, dry_run: bool) -> Outcome:
        """Perform the transition for one detection, without detection metadata.

        Args:
            state: Current state (updated in place).
            detection: The latest detection.
            dry_run: Don't apply anything.

        Returns:
            What happened.
        """
        if detection.state is SessionState.UNKNOWN:
            missing_log = detection.reason == "log file not found"
            state.warning = (
                f"RealVNC log not found at {self.config.detection.log_path}"
                if missing_log
                else None
            )
            return Outcome(
                Action.NONE,
                "Session state unknown; leaving displays alone",
                state.mode,
                warning=state.warning,
            )

        want_mirror = detection.state is SessionState.ACTIVE and state.mode is Mode.NORMAL
        want_restore = detection.state is SessionState.ENDED and state.mode is Mode.MIRRORED
        if not (want_mirror or want_restore):
            state.warning = None
            state.shadow_note = None
            label = "active" if detection.state is SessionState.ACTIVE else "no session"
            return Outcome(
                Action.NONE,
                f"Already {state.mode.value} ({label})",
                state.mode,
            )

        legacy = self.legacy()
        if legacy.active:
            verb = "mirror" if want_mirror else "restore"
            note = f"Would {verb} (shadow mode: {legacy.reason})"
            if state.shadow_note != note:
                logger.info(note)
            state.shadow_note = note
            return Outcome(Action.SHADOW, note, state.mode)
        state.shadow_note = None

        if want_mirror:
            return self._mirror(state, dry_run=dry_run)
        return self._restore(state, dry_run=dry_run)

    # --------------------------------------------------------------- switching
    def _skip(self, state: State, layout: Layout, missing: frozenset[str], verb: str) -> Outcome:
        """Record and report a switch skipped because displays are missing.

        Args:
            state: State to update in place.
            layout: The layout that couldn't be applied.
            missing: Display IDs it needs that aren't connected.
            verb: ``"mirror"`` or ``"restore"``.

        Returns:
            A ``SKIPPED`` outcome.
        """
        short = ", ".join(sorted(i[:8] for i in missing))
        warning = (
            f"Display drift: can't {verb}; layout {layout.name!r} needs display(s) {short} "
            "that aren't connected"
        )
        if state.warning != warning:
            logger.warning(warning)
        state.warning = warning
        return Outcome(
            Action.SKIPPED,
            f"Skipped {verb}: displays changed",
            state.mode,
            layout=layout.name,
            warning=warning,
        )

    def _apply(self, layout: Layout, *, dry_run: bool) -> tuple[list[str], float | None]:
        """Apply a layout unless this is a dry run.

        Args:
            layout: The layout.
            dry_run: Only build the command.

        Returns:
            ``(command, duration)``; duration is ``None`` for dry runs.
        """
        command = self.backend.command_for(layout)
        if dry_run:
            return command, None
        return command, self.backend.apply(layout)

    def _mirror(self, state: State, *, dry_run: bool) -> Outcome:
        """Snapshot the current layout and apply the ``mirror`` layout.

        Args:
            state: State to update in place.
            dry_run: Don't apply anything.

        Returns:
            What happened.
        """
        target = self.config.layout(MIRROR)
        current = self.backend.snapshot()
        missing = target.missing_ids(current.connected)
        if missing:
            return self._skip(state, target, missing, "mirror")
        command, duration = self._apply(target, dry_run=dry_run)
        if not dry_run:
            # If the screens were already mirrored by hand there's nothing worth
            # restoring, so fall back to the saved `normal` layout later.
            state.snapshot = None if current.layout.is_mirrored else current.layout.args
            state.snapshot_taken = self.clock()
            state.mode = Mode.MIRRORED
            state.last_switch = self.clock()
            state.last_switch_action = Action.MIRRORED.value
            state.warning = None
            logger.success("Mirrored displays ({:.2f} s)", duration or 0.0)
        return Outcome(
            Action.MIRRORED,
            "Would mirror displays" if dry_run else "Mirrored displays",
            Mode.MIRRORED if not dry_run else state.mode,
            layout=MIRROR,
            command=command,
            duration=duration,
            dry_run=dry_run,
        )

    def restore_target(self, state: State, current: ListedLayout) -> tuple[Layout, str] | None:
        """Choose the layout to restore: the snapshot if usable, else ``normal``.

        Args:
            state: Current state (holds the snapshot).
            current: The current arrangement and connected displays.

        Returns:
            ``(layout, source)`` where source is ``"snapshot"`` or ``"normal"``,
            or ``None`` if neither can be applied.
        """
        if state.snapshot:
            try:
                snap = Layout.from_args("snapshot", state.snapshot)
            except LayoutError:
                snap = None
            if snap is not None and not snap.missing_ids(current.connected):
                return snap, "snapshot"
        normal = self.config.layouts.get(NORMAL)
        if normal is not None and not normal.missing_ids(current.connected):
            return normal, "normal"
        return None

    def _restore(self, state: State, *, dry_run: bool) -> Outcome:
        """Apply the pre-session snapshot (or the ``normal`` layout).

        Args:
            state: State to update in place.
            dry_run: Don't apply anything.

        Returns:
            What happened.
        """
        current = self.backend.snapshot()
        choice = self.restore_target(state, current)
        if choice is None:
            fallback = self.config.layouts.get(NORMAL)
            if fallback is None:
                raise LayoutError(
                    "Nothing to restore to",
                    cause="There's no usable snapshot and no `normal` layout.",
                    fix="Arrange your displays, then run `mirrormax layout save normal`.",
                )
            return self._skip(state, fallback, fallback.missing_ids(current.connected), "restore")
        layout, source = choice
        command, duration = self._apply(layout, dry_run=dry_run)
        if not dry_run:
            state.mode = Mode.NORMAL
            state.snapshot = None
            state.snapshot_taken = None
            state.last_switch = self.clock()
            state.last_switch_action = Action.RESTORED.value
            state.warning = None
            if state.migration and not state.migration.rolled_back_at:
                state.migration.clean_cycles += 1
            logger.success("Restored {} layout ({:.2f} s)", source, duration or 0.0)
        what = "previous layout" if source == "snapshot" else "`normal` layout"
        return Outcome(
            Action.RESTORED,
            f"Would restore {what}" if dry_run else f"Restored {what}",
            Mode.NORMAL if not dry_run else state.mode,
            layout=source if source == "snapshot" else NORMAL,
            command=command,
            duration=duration,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------ manual ops
    def mirror(self, *, dry_run: bool = False) -> Outcome:
        """Mirror displays now, regardless of the VNC session.

        Args:
            dry_run: Show what would happen without changing anything.

        Returns:
            What happened.
        """
        with file_lock(self.lock_path, self.lock_wait):
            state = self.store.load()
            if state.mode is Mode.MIRRORED:
                return Outcome(Action.NONE, "Already mirrored", state.mode, layout=MIRROR)
            outcome = self._mirror(state, dry_run=dry_run)
            if not dry_run:
                self.store.save(state)
            return outcome

    def restore(self, *, dry_run: bool = False) -> Outcome:
        """Restore the previous layout now, regardless of the VNC session.

        Args:
            dry_run: Show what would happen without changing anything.

        Returns:
            What happened.
        """
        with file_lock(self.lock_path, self.lock_wait):
            state = self.store.load()
            outcome = self._restore(state, dry_run=dry_run)
            if not dry_run:
                self.store.save(state)
            return outcome

    def apply_layout(self, name: str, *, dry_run: bool = False, force: bool = False) -> Outcome:
        """Apply a named layout by hand.

        Args:
            name: Layout name.
            dry_run: Show the command without running it.
            force: Skip the drift check.

        Returns:
            What happened.

        Raises:
            LayoutError: The layout doesn't exist or displays are missing.
        """
        layout = self.config.layout(name)
        with file_lock(self.lock_path, self.lock_wait):
            if not force:
                missing = layout.missing_ids(self.backend.snapshot().connected)
                if missing:
                    raise LayoutError(
                        f"Layout {name!r} needs displays that aren't connected",
                        cause="Missing: " + ", ".join(sorted(missing)),
                        fix=f"Connect them, recapture with `mirrormax layout save {name}`, "
                        "or pass --force.",
                    )
            command, duration = self._apply(layout, dry_run=dry_run)
            state = self.store.load()
            return Outcome(
                Action.APPLIED,
                f"Would apply {name!r}" if dry_run else f"Applied {name!r}",
                state.mode,
                layout=name,
                command=command,
                duration=duration,
                dry_run=dry_run,
            )
