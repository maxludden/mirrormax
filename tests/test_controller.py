"""Tests for the mirror/restore state machine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import LINES, START_LINE, STOP_LINE, A, FakeBackend, append

from mirrormax.config import Config
from mirrormax.controller import Action, Controller
from mirrormax.detector import SessionState
from mirrormax.errors import LayoutError
from mirrormax.paths import Paths
from mirrormax.state import Mode, StateStore

Factory = Callable[..., Controller]


def connect(log: Path) -> None:
    append(log, LINES[: START_LINE + 1])


def disconnect(log: Path) -> None:
    append(log, LINES[START_LINE + 1 : STOP_LINE + 1])


def test_connect_mirrors_once(
    make_controller: Factory, log_path: Path, backend: FakeBackend
) -> None:
    ctl = make_controller()
    connect(log_path)
    first = ctl.check()
    assert first.action is Action.MIRRORED
    assert first.exit_code == 10
    assert first.session is SessionState.ACTIVE
    for _ in range(5):  # repeated checks never switch again
        assert ctl.check().action is Action.NONE
    assert [layout.name for layout in backend.applied] == ["mirror"]


def test_disconnect_restores_snapshot(
    make_controller: Factory, log_path: Path, backend: FakeBackend, paths: Paths
) -> None:
    ctl = make_controller()
    before = backend.current_layout.args
    connect(log_path)
    ctl.check()
    disconnect(log_path)
    outcome = ctl.check()
    assert outcome.action is Action.RESTORED
    assert outcome.exit_code == 11
    assert outcome.layout == "snapshot"
    assert backend.current_layout.args == before
    assert ctl.check().action is Action.NONE
    state = StateStore(paths.state_file).load()
    assert state.mode is Mode.NORMAL
    assert state.snapshot is None


def test_unknown_state_changes_nothing(make_controller: Factory, backend: FakeBackend) -> None:
    outcome = make_controller().check()
    assert outcome.action is Action.NONE
    assert outcome.session is SessionState.UNKNOWN
    assert backend.applied == []


def test_rotation_mid_session_does_not_restore(
    make_controller: Factory, log_path: Path, backend: FakeBackend
) -> None:
    ctl = make_controller()
    connect(log_path)
    ctl.check()
    log_path.unlink()
    log_path.write_text("")  # RealVNC rotated: no markers yet
    assert ctl.check().action is Action.NONE
    assert [layout.name for layout in backend.applied] == ["mirror"]


def test_drift_skips_with_warning(
    make_controller: Factory, log_path: Path, backend: FakeBackend, paths: Paths
) -> None:
    backend.connected = frozenset({A})  # external monitor unplugged
    connect(log_path)
    outcome = make_controller().check()
    assert outcome.action is Action.SKIPPED
    assert outcome.warning is not None
    assert "drift" in outcome.warning.lower()
    assert backend.applied == []
    assert StateStore(paths.state_file).load().warning == outcome.warning


def test_missing_snapshot_falls_back_to_normal(
    make_controller: Factory, log_path: Path, backend: FakeBackend, paths: Paths
) -> None:
    ctl = make_controller()
    connect(log_path)
    ctl.check()
    store = StateStore(paths.state_file)
    state = store.load()
    state.snapshot = None
    store.save(state)
    disconnect(log_path)
    outcome = ctl.check()
    assert outcome.action is Action.RESTORED
    assert outcome.layout == "normal"


def test_stale_snapshot_falls_back_to_normal(
    make_controller: Factory, log_path: Path, paths: Paths
) -> None:
    ctl = make_controller()
    connect(log_path)
    ctl.check()
    store = StateStore(paths.state_file)
    state = store.load()
    state.snapshot = ["id:DEAD-BEEF res:800x600 origin:(0,0)"]  # display no longer exists
    store.save(state)
    disconnect(log_path)
    assert ctl.check().layout == "normal"


def test_shadow_mode_never_switches(
    make_controller: Factory, log_path: Path, backend: FakeBackend
) -> None:
    ctl = make_controller(legacy_active=True)
    connect(log_path)
    outcome = ctl.check()
    assert outcome.action is Action.SHADOW
    assert "Would mirror" in outcome.message
    assert outcome.exit_code == 0
    assert backend.applied == []


def test_dry_run_changes_nothing(
    make_controller: Factory, log_path: Path, backend: FakeBackend, paths: Paths
) -> None:
    connect(log_path)
    outcome = make_controller().check(dry_run=True)
    assert outcome.action is Action.MIRRORED
    assert outcome.dry_run
    assert outcome.command and outcome.command[0].endswith("displayplacer")
    assert backend.applied == []
    assert not paths.state_file.exists()


def test_manual_mirror_and_restore(make_controller: Factory, backend: FakeBackend) -> None:
    ctl = make_controller()
    assert ctl.mirror().action is Action.MIRRORED
    assert ctl.mirror().action is Action.NONE
    assert ctl.restore().action is Action.RESTORED
    assert [layout.name for layout in backend.applied] == ["mirror", "snapshot"]


def test_apply_layout_checks_drift(make_controller: Factory, backend: FakeBackend) -> None:
    backend.connected = frozenset({A})
    ctl = make_controller()
    with pytest.raises(LayoutError):
        ctl.apply_layout("normal")
    assert ctl.apply_layout("normal", force=True).action is Action.APPLIED


def test_already_mirrored_by_hand_restores_normal(
    make_controller: Factory, log_path: Path, backend: FakeBackend, config: Config
) -> None:
    backend.current_layout = config.layout("mirror")
    ctl = make_controller()
    connect(log_path)
    ctl.check()
    disconnect(log_path)
    assert ctl.check().layout == "normal"
