"""Tests for the watch loop, launchd agent and legacy migration."""

from __future__ import annotations

import os
import plistlib
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import DATA, LINES, START_LINE, STOP_LINE, FakeBackend, FakeLaunchd, append

from mirrormax.agent import Launchctl, build_plist, install, uninstall
from mirrormax.config import Config, DetectionMode, load
from mirrormax.controller import Action, Controller, Outcome
from mirrormax.errors import LegacyError, MirrormaxError
from mirrormax.legacy import (
    inspect,
    migrate,
    parse_legacy_script,
    plan_migration,
    rollback,
    safe_to_delete,
)
from mirrormax.paths import AGENT_LABEL, LEGACY_LABEL, Paths
from mirrormax.state import Mode, StateStore
from mirrormax.watcher import KqueueWatcher, WatchEvent, watch_loop

EXE = Path("/Users/max/.local/bin/mirrormax")


# ------------------------------------------------------------------ watcher
class ScriptedWatcher:
    """Fake LogWatcher that appends log lines as each event is delivered."""

    def __init__(self, log: Path, script: list[tuple[WatchEvent, list[str]]]) -> None:
        self.log = log
        self.script = list(script)
        self.closed = False

    def wait(self, timeout: float) -> WatchEvent:
        event, lines = self.script.pop(0)
        append(self.log, lines)
        return event

    def close(self) -> None:
        self.closed = True


def test_watch_loop_switches_on_events(
    make_controller: Callable[..., Controller], log_path: Path, backend: FakeBackend
) -> None:
    seen: list[tuple[WatchEvent, Outcome | None]] = []
    watcher = ScriptedWatcher(
        log_path,
        [
            (WatchEvent.CHANGED, LINES[: START_LINE + 1]),
            (WatchEvent.CHANGED, LINES[START_LINE + 1 : STOP_LINE]),
            (WatchEvent.CHANGED, [LINES[STOP_LINE]]),
        ],
    )
    passes = watch_loop(
        make_controller(), watcher, 60, lambda e, o, _err: seen.append((e, o)), max_passes=4
    )
    assert passes == 4
    assert watcher.closed
    actions = [o.action for _, o in seen if o]
    assert actions == [Action.NONE, Action.MIRRORED, Action.NONE, Action.RESTORED]
    assert len(backend.applied) == 2


def test_heartbeat_recovers_missed_event(
    make_controller: Callable[..., Controller], log_path: Path
) -> None:
    # The marker lands in the log but kqueue never reports it; the heartbeat
    # timeout runs a full pass anyway and catches it.
    seen: list[Outcome | None] = []
    watcher = ScriptedWatcher(log_path, [(WatchEvent.TIMEOUT, LINES[: START_LINE + 1])])
    watch_loop(make_controller(), watcher, 60, lambda _e, o, _err: seen.append(o), max_passes=2)
    assert [o.action for o in seen if o] == [Action.NONE, Action.MIRRORED]


def test_watch_loop_survives_errors(
    make_controller: Callable[..., Controller], log_path: Path, backend: FakeBackend
) -> None:
    errors: list[MirrormaxError] = []

    def boom() -> object:
        raise MirrormaxError("display system busy")

    backend.snapshot = boom  # type: ignore[assignment,method-assign]
    watcher = ScriptedWatcher(log_path, [(WatchEvent.CHANGED, LINES[: START_LINE + 1])])
    watch_loop(
        make_controller(),
        watcher,
        60,
        lambda _e, _o, err: errors.append(err) if err else None,
        max_passes=2,
    )
    assert [e.what for e in errors] == ["display system busy"]


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="kqueue needs macOS/BSD")
def test_kqueue_watcher_real_file(tmp_path: Path) -> None:
    log = tmp_path / "rvnc.log"
    log.write_text("start\n")
    watcher = KqueueWatcher(log, missing_backoff=0.1)
    try:
        assert watcher.wait(0.2) is WatchEvent.TIMEOUT
        append(log, ["more\n"])
        assert watcher.wait(2) is WatchEvent.CHANGED
        os.rename(log, tmp_path / "rvnc.log.1")
        log.write_text("fresh\n")
        assert watcher.wait(2) is WatchEvent.ROTATED
        append(log, ["after rotation\n"])
        assert watcher.wait(2) is WatchEvent.CHANGED
    finally:
        watcher.close()


# ------------------------------------------------------------------ agent
@pytest.mark.parametrize("mode", [DetectionMode.WATCH, DetectionMode.POLL])
def test_plist_matches_expected(mode: DetectionMode) -> None:
    paths = Paths.default(home=Path("/Users/max"))
    plist = build_plist(mode=mode, executable=EXE, paths=paths, poll_interval=5)
    expected = (DATA / f"expected-{mode.value}.plist").read_bytes()
    assert plist == expected
    data = plistlib.loads(plist)
    assert data["Label"] == AGENT_LABEL
    assert ("KeepAlive" in data) is (mode is DetectionMode.WATCH)


def test_plist_passes_custom_config() -> None:
    paths = Paths.default(home=Path("/Users/max"))
    plist = build_plist(
        mode=DetectionMode.WATCH,
        executable=EXE,
        paths=paths,
        poll_interval=5,
        config_file=Path("/tmp/c.toml"),
    )
    assert plistlib.loads(plist)["ProgramArguments"] == [
        str(EXE),
        "--config",
        "/tmp/c.toml",
        "watch",
    ]


def test_install_and_uninstall(paths: Paths, lc: Launchctl, launchd: FakeLaunchd) -> None:
    plist = build_plist(mode=DetectionMode.WATCH, executable=EXE, paths=paths, poll_interval=5)
    install(lc, plist, paths.agent_plist)
    status = lc.status(AGENT_LABEL, paths.agent_plist)
    assert status.loaded and status.pid == 4242 and status.mode == "watch"
    install(lc, plist, paths.agent_plist)  # reinstall boots out first: no error 5
    assert ("bootout", f"gui/501/{AGENT_LABEL}") in launchd.calls
    assert uninstall(lc, paths.agent_plist)
    assert not paths.agent_plist.exists()
    assert AGENT_LABEL not in launchd.loaded
    assert not any(c[0] in {"load", "unload"} for c in launchd.calls)


# ------------------------------------------------------------------ legacy
def legacy_setup(paths: Paths, launchd: FakeLaunchd, mirrored: bool) -> None:
    paths.legacy_plist.parent.mkdir(parents=True, exist_ok=True)
    paths.legacy_plist.write_bytes(plistlib.dumps({"Label": LEGACY_LABEL}))
    launchd.loaded[LEGACY_LABEL] = 99
    if mirrored:
        paths.legacy_state_marker.parent.mkdir(parents=True, exist_ok=True)
        paths.legacy_state_marker.touch()


def test_parse_legacy_script() -> None:
    mirror, normal = parse_legacy_script((DATA / "vnc-mirror.sh").read_text())
    assert mirror.is_mirrored
    assert len(normal.screens) == 2
    assert normal.screens[1].origin == (-359, -1080)


def test_parse_legacy_script_missing_array() -> None:
    with pytest.raises(LegacyError):
        parse_legacy_script("NORMAL_ARGS=('id:x')\n")


def test_legacy_active_means_shadow(
    config: Config, paths: Paths, lc: Launchctl, launchd: FakeLaunchd
) -> None:
    legacy_setup(paths, launchd, mirrored=False)
    info = inspect(config, paths, lc)
    assert info.active and info.reason == "legacy agent loaded"
    del launchd.loaded[LEGACY_LABEL]
    assert inspect(config, paths, lc).reason == "legacy agent enabled at login"


def test_migrate_while_mirrored_adopts_state(
    config: Config, paths: Paths, lc: Launchctl, launchd: FakeLaunchd
) -> None:
    legacy_setup(paths, launchd, mirrored=True)
    empty = Config(path=config.path, detection=config.detection, legacy=config.legacy)
    steps = plan_migration(empty, inspect(empty, paths, lc), DetectionMode.WATCH)
    assert all(s.run for s in steps)
    store = StateStore(paths.state_file)
    new_config = migrate(empty, paths, store, lc, EXE, DetectionMode.WATCH)
    assert set(new_config.layouts) == {"mirror", "normal"}
    assert set(load(config.path).layouts) == {"mirror", "normal"}
    state = store.load()
    assert state.mode is Mode.MIRRORED
    assert state.snapshot == new_config.layout("normal").args
    assert state.migration is not None and state.migration.legacy_was_mirrored
    assert LEGACY_LABEL not in launchd.loaded
    assert LEGACY_LABEL in launchd.disabled
    assert AGENT_LABEL in launchd.loaded
    assert paths.legacy_plist.exists()  # nothing deleted
    assert not inspect(new_config, paths, lc).active


def test_migrate_while_normal(
    config: Config, paths: Paths, lc: Launchctl, launchd: FakeLaunchd
) -> None:
    legacy_setup(paths, launchd, mirrored=False)
    store = StateStore(paths.state_file)
    migrate(config, paths, store, lc, EXE, DetectionMode.POLL)
    assert store.load().mode is Mode.NORMAL
    assert lc.status(AGENT_LABEL, paths.agent_plist).mode == "poll"


def test_rollback(config: Config, paths: Paths, lc: Launchctl, launchd: FakeLaunchd) -> None:
    legacy_setup(paths, launchd, mirrored=True)
    store = StateStore(paths.state_file)
    migrate(config, paths, store, lc, EXE, DetectionMode.WATCH)
    paths.legacy_state_marker.unlink()
    rollback(config, paths, store, lc)
    assert LEGACY_LABEL in launchd.loaded
    assert LEGACY_LABEL not in launchd.disabled
    assert AGENT_LABEL not in launchd.loaded
    assert AGENT_LABEL in launchd.disabled
    assert paths.legacy_state_marker.exists()  # mirrored state handed back
    state = store.load()
    assert state.migration is not None and state.migration.rolled_back_at is not None


def test_safe_to_delete_needs_a_clean_cycle(
    config: Config,
    paths: Paths,
    lc: Launchctl,
    launchd: FakeLaunchd,
    make_controller: Callable[..., Controller],
    log_path: Path,
) -> None:
    legacy_setup(paths, launchd, mirrored=False)
    store = StateStore(paths.state_file)
    migrate(config, paths, store, lc, EXE, DetectionMode.WATCH)
    info = inspect(config, paths, lc)
    assert safe_to_delete(info, store.load().migration) == (
        False,
        "waiting for one clean connect/disconnect cycle under mirrormax",
    )
    ctl = make_controller()
    append(log_path, LINES[: START_LINE + 1])
    ctl.check()
    append(log_path, LINES[START_LINE + 1 : STOP_LINE + 1])
    ctl.check()
    safe, _why = safe_to_delete(info, store.load().migration)
    assert safe
