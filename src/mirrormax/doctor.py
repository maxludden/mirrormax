"""Health checks behind ``mirrormax doctor``.

Each check returns a :class:`Check` with a status and, for anything that
isn't OK, a concrete fix. Checks never change anything.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from mirrormax.agent import AgentStatus, Launchctl
from mirrormax.config import MIRROR, NORMAL, Config, default_config, load
from mirrormax.detector import SessionState, detect
from mirrormax.displays import DisplayBackend
from mirrormax.errors import MirrormaxError
from mirrormax.layouts import ListedLayout
from mirrormax.legacy import LegacyInfo, inspect, safe_to_delete
from mirrormax.paths import AGENT_LABEL, Paths
from mirrormax.state import StateStore


class Status(StrEnum):
    """Result of one check."""

    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Check:
    """One doctor check result.

    Attributes:
        name: What was checked.
        status: The result.
        details: What was found.
        fix: How to fix it (empty when OK).
    """

    name: str
    status: Status
    details: str
    fix: str = ""


@dataclass(frozen=True, slots=True)
class Report:
    """All doctor results.

    Attributes:
        checks: Results, in display order.
    """

    checks: list[Check] = field(default_factory=list)

    def count(self, status: Status) -> int:
        """Count checks with a given status.

        Args:
            status: The status to count.

        Returns:
            How many checks have it.
        """
        return sum(1 for c in self.checks if c.status is status)

    @property
    def ok(self) -> bool:
        """Whether no check failed with an error."""
        return self.count(Status.ERROR) == 0


def run_doctor(
    paths: Paths,
    lc: Launchctl,
    backend_factory: Callable[[Config], DisplayBackend],
    is_macos: bool | None = None,
) -> Report:
    """Run every check.

    Args:
        paths: Standard paths (``config_file`` may be overridden).
        lc: launchctl wrapper.
        backend_factory: Builds the display backend from a config.
        is_macos: Override platform detection (tests).

    Returns:
        The report.
    """
    checks: list[Check] = []
    add = checks.append
    mac = platform.system() == "Darwin" if is_macos is None else is_macos
    add(
        Check("macOS", Status.OK, platform.mac_ver()[0] or "Darwin")
        if mac
        else Check(
            "macOS",
            Status.ERROR,
            f"running on {platform.system()}",
            "mirrormax only works on macOS.",
        )
    )

    # Config ------------------------------------------------------------------
    config: Config | None = None
    try:
        config = load(paths.config_file)
        add(Check("Config", Status.OK, str(paths.config_file)))
    except MirrormaxError as exc:
        add(
            Check(
                "Config", Status.ERROR, exc.what + (f" ({exc.cause})" if exc.cause else ""), exc.fix
            )
        )
    if config is not None:
        cfg = config
    else:
        # Keep going with defaults so the remaining checks still run.
        try:
            cfg = load(paths.config_file, required=False)
        except MirrormaxError:
            cfg = default_config(paths.config_file)

    # displayplacer -----------------------------------------------------------
    listed: ListedLayout | None = None
    dp = cfg.displayplacer
    if not dp.exists():
        add(
            Check("displayplacer", Status.ERROR, f"not found at {dp}", "brew install displayplacer")
        )
    elif not os.access(dp, os.X_OK):
        add(Check("displayplacer", Status.ERROR, f"{dp} isn't executable", f"chmod +x {dp}"))
    else:
        try:
            listed = backend_factory(cfg).snapshot()
            n = len(listed.screens) or len(listed.connected)
            add(Check("displayplacer", Status.OK, f"{dp} · {n} display(s) connected"))
        except MirrormaxError as exc:
            add(Check("displayplacer", Status.ERROR, exc.what, exc.fix))

    # Layouts and drift -------------------------------------------------------
    for name in (NORMAL, MIRROR):
        layout = cfg.layouts.get(name)
        label = f"Layout `{name}`"
        if layout is None:
            add(
                Check(
                    label,
                    Status.ERROR,
                    "not saved",
                    "mirrormax init  (or: mirrormax init --import-legacy)",
                )
            )
            continue
        if listed is None:
            add(
                Check(
                    label, Status.INFO, f"{len(layout.screens)} screen arg(s); can't check displays"
                )
            )
            continue
        missing = layout.missing_ids(listed.connected)
        if missing:
            add(
                Check(
                    label,
                    Status.WARN,
                    "needs disconnected display(s): " + ", ".join(sorted(i[:8] for i in missing)),
                    f"Reconnect them, or recapture: mirrormax layout save {name}",
                )
            )
        else:
            add(Check(label, Status.OK, "all displays connected"))

    # RealVNC log -------------------------------------------------------------
    log = cfg.detection.log_path
    if not log.exists():
        add(
            Check(
                "RealVNC log",
                Status.ERROR,
                f"{log} not found",
                "Is RealVNC Server installed and running?",
            )
        )
    else:
        try:
            det = detect(log, None)
            if det.state is SessionState.UNKNOWN:
                add(
                    Check(
                        "RealVNC log",
                        Status.WARN,
                        "readable, but no session marker yet",
                        "Normal right after RealVNC rotates its log; connect once to populate it.",
                    )
                )
            else:
                add(
                    Check(
                        "RealVNC log",
                        Status.OK,
                        f"readable · last marker: session {det.state.value}",
                    )
                )
        except MirrormaxError as exc:
            add(Check("RealVNC log", Status.ERROR, exc.what, exc.fix))

    # State -------------------------------------------------------------------
    store = StateStore(paths.state_file)
    record = None
    try:
        state = store.load()
        record = state.migration
        add(Check("State", Status.OK, f"{paths.state_file} · mode {state.mode.value}"))
    except MirrormaxError as exc:
        add(Check("State", Status.ERROR, exc.what, exc.fix))

    # Agent -------------------------------------------------------------------
    agent = lc.status(AGENT_LABEL, paths.agent_plist)
    add(_agent_check(agent, cfg))

    # Legacy ------------------------------------------------------------------
    info = inspect(cfg, paths, lc)
    add(_legacy_check(info, record is not None and record.rolled_back_at is None))
    if record is not None or info.plist_exists or info.script_exists:
        safe, why = safe_to_delete(info, record)
        add(
            Check(
                "Delete legacy files?",
                Status.OK if safe else Status.INFO,
                ("safe to delete: " if safe else "not yet: ") + why,
                f"rm {info.script} {info.plist}" if safe else "",
            )
        )
    return Report(checks)


def _agent_check(agent: AgentStatus, config: Config) -> Check:
    """Evaluate the mirrormax launch agent.

    Args:
        agent: Its launchd status.
        config: Config (for the expected mode).

    Returns:
        The check result.
    """
    name = "Launch agent"
    if not agent.plist_exists:
        return Check(
            name, Status.WARN, "not installed", "mirrormax agent install  (or: mirrormax migrate)"
        )
    if agent.disabled:
        return Check(name, Status.WARN, "installed but disabled", "mirrormax agent start")
    if not agent.loaded:
        return Check(name, Status.WARN, "installed but not loaded", "mirrormax agent start")
    exe = Path(agent.program[0]) if agent.program else None
    if exe is not None and not exe.exists():
        return Check(
            name, Status.ERROR, f"runs {exe}, which no longer exists", "mirrormax agent install"
        )
    if agent.mode != config.detection.mode.value:
        return Check(
            name,
            Status.WARN,
            f"running in {agent.mode} mode; config says {config.detection.mode.value}",
            f"mirrormax agent install --mode {config.detection.mode.value}",
        )
    if agent.mode == "watch" and agent.pid is None:
        return Check(
            name,
            Status.WARN,
            f"loaded but not running (last exit {agent.last_exit})",
            "mirrormax logs  then  mirrormax agent start",
        )
    detail = f"{agent.mode} mode" + (f" · pid {agent.pid}" if agent.pid else "")
    return Check(name, Status.OK, detail)


def _legacy_check(info: LegacyInfo, migrated: bool) -> Check:
    """Evaluate the legacy vnc-mirror agent.

    Args:
        info: Legacy state.
        migrated: Whether a migration is recorded (and not rolled back).

    Returns:
        The check result.
    """
    name = "Legacy vnc-mirror"
    if info.active:
        return Check(
            name, Status.WARN, f"{info.reason}: mirrormax is in shadow mode", "mirrormax migrate"
        )
    if migrated:
        return Check(name, Status.OK, "migrated · legacy agent disabled")
    if not info.plist_exists and not info.script_exists:
        return Check(name, Status.OK, "not present")
    return Check(name, Status.OK, "inactive")
