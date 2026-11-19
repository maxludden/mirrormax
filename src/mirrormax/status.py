"""Gather everything ``mirrormax status`` reports into one typed object.

The same :class:`StatusReport` feeds both the Rich view and ``--json``, so
the two can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mirrormax.agent import AgentStatus, Launchctl
from mirrormax.config import Config
from mirrormax.detector import SessionState, detect
from mirrormax.errors import MirrormaxError
from mirrormax.legacy import LegacyInfo, inspect
from mirrormax.paths import AGENT_LABEL, Paths
from mirrormax.state import MigrationRecord, Mode, State, StateStore


@dataclass(frozen=True, slots=True)
class StatusReport:
    """A point-in-time summary of mirrormax.

    Attributes:
        session: Session state according to RealVNC's log right now.
        session_started: When the current session started, if active.
        session_error: Why the session state couldn't be read, if it couldn't.
        mode: What mirrormax believes is applied.
        shadow: Whether mirrormax is in shadow mode (legacy agent active).
        shadow_reason: Why it's in shadow mode.
        shadow_note: Last thing it would have done in shadow mode.
        warning: Current warning (drift etc.).
        last_switch: When displays were last switched.
        last_switch_action: ``mirrored`` or ``restored``.
        last_check: When the agent last checked.
        has_snapshot: Whether a pre-session snapshot is stored.
        agent: The launch agent's status.
        legacy: The legacy automation's status.
        migration: The migration record, if any.
        config_path: Config file in use.
        config_exists: Whether it exists.
        log_path: RealVNC log path.
    """

    session: SessionState
    session_started: datetime | None
    session_error: str | None
    mode: Mode
    shadow: bool
    shadow_reason: str
    shadow_note: str | None
    warning: str | None
    last_switch: datetime | None
    last_switch_action: str | None
    last_check: datetime | None
    has_snapshot: bool
    agent: AgentStatus
    legacy: LegacyInfo
    migration: MigrationRecord | None
    config_path: Path
    config_exists: bool
    log_path: Path


def gather(config: Config, paths: Paths, lc: Launchctl, store: StateStore) -> StatusReport:
    """Collect the status without changing anything.

    The log is read independently of the saved cursor, so ``status`` never
    disturbs the agent's bookkeeping.

    Args:
        config: Loaded (or default) config.
        paths: Standard paths.
        lc: launchctl wrapper.
        store: State store.

    Returns:
        The report.
    """
    state: State
    try:
        state = store.load()
    except MirrormaxError:
        state = State()
    session_error = None
    try:
        det = detect(config.detection.log_path, None)
        session = det.state
        started = det.marker_time if session is SessionState.ACTIVE else None
        if det.state is SessionState.UNKNOWN:
            session_error = det.reason
    except MirrormaxError as exc:
        session, started, session_error = SessionState.UNKNOWN, None, exc.what
    legacy = inspect(config, paths, lc)
    return StatusReport(
        session=session,
        session_started=started or state.session_started
        if session is SessionState.ACTIVE
        else None,
        session_error=session_error,
        mode=state.mode,
        shadow=legacy.active,
        shadow_reason=legacy.reason,
        shadow_note=state.shadow_note,
        warning=state.warning,
        last_switch=state.last_switch,
        last_switch_action=state.last_switch_action,
        last_check=state.last_check,
        has_snapshot=bool(state.snapshot),
        agent=lc.status(AGENT_LABEL, paths.agent_plist),
        legacy=legacy,
        migration=state.migration,
        config_path=config.path,
        config_exists=config.exists,
        log_path=config.detection.log_path,
    )
