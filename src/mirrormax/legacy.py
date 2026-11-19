"""Coexisting with, and migrating from, the old ``vnc-mirror`` shell automation.

The old setup is a zsh script run every 5 s by the launch agent
``com.maxludden.vnc-mirror``. It restores a *fixed* layout and has no lock,
so if both tools switched displays they would race on every disconnect.
Hence the rules:

* While the legacy agent is loaded, or its plist exists and isn't disabled,
  mirrormax runs in **shadow mode**: it detects and logs, but never switches.
* ``mirrormax migrate`` hands over cleanly: imports the old layouts, adopts
  the old "mirrored" marker, boots the old agent out and *disables* it (so it
  stays off at the next login even though its plist is left in place), then
  installs the mirrormax agent. Nothing is deleted.
* ``mirrormax migrate --rollback`` reverses that.
"""

from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mirrormax.agent import Launchctl, build_plist, install
from mirrormax.config import MIRROR, NORMAL, Config, DetectionMode, save
from mirrormax.controller import LegacyGuard, LegacyStatus
from mirrormax.errors import LegacyError
from mirrormax.layouts import Layout
from mirrormax.paths import AGENT_LABEL, LEGACY_LABEL, Paths
from mirrormax.state import MigrationRecord, Mode, StateStore, file_lock, now


def _array_body(text: str, name: str) -> str | None:
    """Return the raw text between ``NAME=(`` and its closing parenthesis.

    Parentheses inside quotes (``origin:(0,0)``) are skipped.

    Args:
        text: The shell script.
        name: Array variable name, e.g. ``MIRROR_ARGS``.

    Returns:
        The array body, or ``None`` if the assignment isn't found.
    """
    match = re.search(rf"^[ \t]*{re.escape(name)}=\(", text, re.MULTILINE)
    if match is None:
        return None
    pos = match.end()
    quote: str | None = None
    for i in range(pos, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == ")":
            return text[pos:i]
    return None


def parse_legacy_script(text: str) -> tuple[Layout, Layout]:
    """Read ``MIRROR_ARGS`` and ``NORMAL_ARGS`` from the old zsh script.

    Args:
        text: Contents of ``vnc-mirror.sh``.

    Returns:
        ``(mirror, normal)`` layouts.

    Raises:
        LegacyError: Either array is missing or empty.
    """
    layouts: dict[str, Layout] = {}
    for var, name in (("MIRROR_ARGS", MIRROR), ("NORMAL_ARGS", NORMAL)):
        body = _array_body(text, var)
        if body is None:
            raise LegacyError(
                f"{var} not found in the legacy script",
                fix="Capture layouts with `mirrormax init` instead of --import-legacy.",
            )
        args = shlex.split(body, comments=True)
        if not args:
            raise LegacyError(f"{var} is empty in the legacy script")
        layouts[name] = Layout.from_args(name, args)
    return layouts[MIRROR], layouts[NORMAL]


@dataclass(frozen=True, slots=True)
class LegacyInfo:
    """The state of the old automation.

    Attributes:
        script: Path of the old script.
        script_exists: Whether the script exists.
        plist: Path of the old plist.
        plist_exists: Whether the plist exists.
        loaded: Whether the old agent is loaded.
        disabled: Whether the old agent is disabled.
        mirrored: Whether the old "mirrored" marker file exists.
    """

    script: Path
    script_exists: bool
    plist: Path
    plist_exists: bool
    loaded: bool
    disabled: bool
    mirrored: bool

    @property
    def active(self) -> bool:
        """Whether the old agent owns the displays (mirrormax must shadow)."""
        return self.loaded or (self.plist_exists and not self.disabled)

    @property
    def reason(self) -> str:
        """Why the legacy agent counts as active, in a few words."""
        if self.loaded:
            return "legacy agent loaded"
        if self.active:
            return "legacy agent enabled at login"
        return ""


def inspect(config: Config, paths: Paths, lc: Launchctl) -> LegacyInfo:
    """Inspect the old automation.

    Args:
        config: Config (holds legacy script and marker paths).
        paths: Standard paths (holds the legacy plist path).
        lc: launchctl wrapper.

    Returns:
        What was found.
    """
    return LegacyInfo(
        script=config.legacy.script,
        script_exists=config.legacy.script.exists(),
        plist=paths.legacy_plist,
        plist_exists=paths.legacy_plist.exists(),
        loaded=lc.is_loaded(LEGACY_LABEL),
        disabled=lc.is_disabled(LEGACY_LABEL),
        mirrored=config.legacy.state_marker.exists(),
    )


def make_guard(config: Config, paths: Paths, lc: Launchctl, ttl: float = 30.0) -> LegacyGuard:
    """Build a legacy guard for the controller, caching launchctl results.

    The watch loop may run many passes a minute; asking launchctl at most every
    ``ttl`` seconds keeps that cheap.

    Args:
        config: Config.
        paths: Standard paths.
        lc: launchctl wrapper.
        ttl: Seconds to cache the answer.

    Returns:
        A callable returning the current :class:`LegacyStatus`.
    """
    cache: list[tuple[float, LegacyStatus]] = []

    def guard() -> LegacyStatus:
        if cache and time.monotonic() - cache[0][0] < ttl:
            return cache[0][1]
        info = inspect(config, paths, lc)
        status = LegacyStatus(active=info.active, reason=info.reason)
        cache[:] = [(time.monotonic(), status)]
        return status

    return guard


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a migration or rollback plan.

    Attributes:
        title: Short description.
        detail: Extra context (paths, commands).
        run: Whether the step will actually do something.
    """

    title: str
    detail: str = ""
    run: bool = True


def plan_migration(config: Config, info: LegacyInfo, mode: DetectionMode) -> list[Step]:
    """Describe what ``mirrormax migrate`` will do, without doing it.

    Args:
        config: Current config.
        info: Legacy state.
        mode: Detection mode for the new agent.

    Returns:
        The ordered steps.
    """
    need_import = not ({MIRROR, NORMAL} <= set(config.layouts))
    return [
        Step(
            "Import layouts from the legacy script",
            str(info.script) if need_import else "skipped: `mirror` and `normal` already saved",
            run=need_import,
        ),
        Step(
            "Adopt the legacy 'mirrored' state",
            "displays are mirrored now; the next disconnect restores `normal`"
            if info.mirrored
            else "skipped: displays aren't mirrored",
            run=info.mirrored,
        ),
        Step(
            f"Stop the legacy agent ({LEGACY_LABEL})",
            "launchctl bootout" if info.loaded else "skipped: not loaded",
            run=info.loaded,
        ),
        Step(
            "Disable the legacy agent at login",
            "launchctl disable (its plist and script stay where they are)",
            run=not info.disabled,
        ),
        Step(f"Install and start {AGENT_LABEL}", f"mode: {mode.value}"),
    ]


ProgressCallback = Callable[[Step], None]
"""Called as each migration step starts."""


def migrate(
    config: Config,
    paths: Paths,
    store: StateStore,
    lc: Launchctl,
    executable: Path,
    mode: DetectionMode,
    progress: ProgressCallback | None = None,
    config_override: Path | None = None,
) -> Config:
    """Hand over from vnc-mirror to mirrormax.

    Args:
        config: Current config (may lack layouts).
        paths: Standard paths.
        store: State store.
        lc: launchctl wrapper.
        executable: mirrormax executable for the new agent.
        mode: Detection mode for the new agent.
        progress: Optional callback per step.
        config_override: Non-default config path to pass to the agent.

    Returns:
        The (possibly updated) config.

    Raises:
        LegacyError: The legacy script is needed but missing or unparsable.
    """
    info = inspect(config, paths, lc)
    steps = plan_migration(config, info, mode)
    imported: list[str] = []

    def step(index: int) -> Step:
        if progress:
            progress(steps[index])
        return steps[index]

    if step(0).run:
        if not info.script_exists:
            raise LegacyError(
                "Can't import layouts: the legacy script is missing",
                cause=str(info.script),
                fix="Run `mirrormax init` to capture layouts, then migrate again.",
            )
        mirror, normal = parse_legacy_script(info.script.read_text(encoding="utf-8"))
        for layout in (mirror, normal):
            if layout.name not in config.layouts:
                config = config.with_layout(layout)
                imported.append(layout.name)
        save(config)

    with file_lock(paths.lock_file):
        state = store.load()
        if step(1).run:
            # The legacy script mirrored the screens; take ownership so our next
            # disconnect restores them. Its `normal` is the best snapshot we have.
            state.mode = Mode.MIRRORED
            state.snapshot = config.layout(NORMAL).args
            state.snapshot_taken = now()
        state.migration = MigrationRecord(
            migrated_at=now(),
            legacy_was_mirrored=info.mirrored,
            imported_layouts=imported,
        )
        state.shadow_note = None
        store.save(state)

    if step(2).run:
        lc.bootout(LEGACY_LABEL)
    if step(3).run:
        lc.disable(LEGACY_LABEL)
    step(4)
    plist = build_plist(
        mode=mode,
        executable=executable,
        paths=paths,
        poll_interval=config.detection.poll_interval,
        config_file=config_override,
    )
    install(lc, plist, paths.agent_plist)
    return config


def plan_rollback(info: LegacyInfo, mirrored: bool, marker: Path) -> list[Step]:
    """Describe what ``mirrormax migrate --rollback`` will do.

    Args:
        info: Legacy state.
        mirrored: Whether mirrormax currently has the displays mirrored.
        marker: The legacy "mirrored" marker file.

    Returns:
        The ordered steps.
    """
    return [
        Step(f"Stop and disable {AGENT_LABEL}", "launchctl bootout + disable (plist kept)"),
        Step(f"Re-enable and start {LEGACY_LABEL}", f"launchctl enable + bootstrap {info.plist}"),
        Step(
            "Hand the 'mirrored' state back",
            f"create {marker}" if mirrored else "skipped: displays aren't mirrored",
            run=mirrored,
        ),
    ]


def rollback(
    config: Config,
    paths: Paths,
    store: StateStore,
    lc: Launchctl,
    progress: ProgressCallback | None = None,
) -> None:
    """Undo a migration: put the legacy agent back in charge.

    Args:
        config: Current config.
        paths: Standard paths.
        store: State store.
        lc: launchctl wrapper.
        progress: Optional callback per step.

    Raises:
        LegacyError: The legacy plist no longer exists.
    """
    info = inspect(config, paths, lc)
    if not info.plist_exists:
        raise LegacyError(
            "Can't roll back: the legacy plist is gone",
            cause=str(info.plist),
            fix="Restore it from a backup (or re-run install-vnc-mirror.sh), then retry.",
        )
    with file_lock(paths.lock_file):
        state = store.load()
        mirrored = state.mode is Mode.MIRRORED
        steps = plan_rollback(info, mirrored, config.legacy.state_marker)
        if progress:
            progress(steps[0])
        lc.bootout(AGENT_LABEL)
        lc.disable(AGENT_LABEL)
        if progress:
            progress(steps[1])
        lc.enable(LEGACY_LABEL)
        if not lc.is_loaded(LEGACY_LABEL):
            lc.bootstrap(info.plist)
        if mirrored:
            if progress:
                progress(steps[2])
            config.legacy.state_marker.touch()
        if state.migration:
            state.migration.rolled_back_at = now()
        state.mode = Mode.NORMAL
        state.snapshot = None
        store.save(state)


def safe_to_delete(info: LegacyInfo, record: MigrationRecord | None) -> tuple[bool, str]:
    """Decide whether the legacy script and plist can be deleted.

    Args:
        info: Legacy state.
        record: The migration record, if any.

    Returns:
        ``(safe, explanation)``.
    """
    if record is None or record.rolled_back_at:
        return False, "not migrated yet"
    if info.loaded or not info.disabled:
        return False, "the legacy agent isn't disabled"
    if record.clean_cycles < 1:
        return False, "waiting for one clean connect/disconnect cycle under mirrormax"
    return True, f"{record.clean_cycles} clean cycle(s) since migrating"
