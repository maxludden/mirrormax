"""Tests for layouts, config round-trips and validation, and the state store."""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest
from conftest import A, B

from mirrormax.config import Config as Cfg
from mirrormax.config import DetectionMode, dumps, load, save
from mirrormax.detector import LogCursor, SessionState
from mirrormax.errors import ConfigError, LayoutError, LockHeldError, StateError
from mirrormax.layouts import Layout, ListedLayout, ScreenArg, parse_command
from mirrormax.state import MigrationRecord, Mode, State, StateStore, file_lock, now


def test_parse_listing(normal_listing: ListedLayout) -> None:
    assert normal_listing.layout.display_ids == {A, B}
    assert normal_listing.connected == {A, B}
    assert len(normal_listing.screens) == 2
    assert normal_listing.screens[0].kind == "MacBook built in screen"
    assert not normal_listing.layout.is_mirrored


def test_parse_mirrored_listing(mirrored_listing: ListedLayout) -> None:
    assert mirrored_listing.layout.is_mirrored
    assert mirrored_listing.connected == {A, B}


def test_screen_arg_fields() -> None:
    arg = ScreenArg.parse(f"id:{B} res:1920x1080 hz:60 scaling:off origin:(-359,-1080) degree:0")
    assert arg.resolution == (1920, 1080)
    assert arg.origin == (-359, -1080)
    assert arg.scaling is False
    assert arg.degree == 0


def test_bad_args_raise() -> None:
    with pytest.raises(LayoutError):
        ScreenArg.parse("res:1x1")
    with pytest.raises(LayoutError):
        ScreenArg.parse("id:X res:axb")
    with pytest.raises(LayoutError):
        Layout.from_args("empty", [])


def test_parse_command_with_and_without_prefix() -> None:
    raw = f'displayplacer "id:{A} res:1280x832 origin:(0,0)"'
    assert parse_command(raw).args == parse_command(raw.removeprefix("displayplacer ")).args


def test_missing_ids(config: Cfg) -> None:
    assert config.layout("normal").missing_ids(frozenset({A})) == {B}


def test_config_round_trip(config: Cfg) -> None:
    save(config)
    loaded = load(config.path)
    assert loaded.layouts["normal"].args == config.layouts["normal"].args
    assert loaded.detection.mode is DetectionMode.WATCH
    assert loaded.detection.log_path == config.detection.log_path


def test_config_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load(tmp_path / "none.toml")
    assert "mirrormax init" in info.value.fix
    assert load(tmp_path / "none.toml", required=False).layouts == {}


def test_config_bad_toml(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("[detection\nmode = 1\n")
    with pytest.raises(ConfigError, match="valid TOML"):
        load(path)


def test_config_bad_value_points_at_line(config: Cfg) -> None:
    text = dumps(config).replace('mode = "watch"', 'mode = "sometimes"')
    config.path.parent.mkdir(parents=True, exist_ok=True)
    config.path.write_text(text)
    with pytest.raises(ConfigError) as info:
        load(config.path)
    assert "detection.mode" in info.value.what
    assert f"{config.path}:4" in info.value.cause
    assert '"watch" or "poll"' in info.value.fix


def test_config_bad_heartbeat(config: Cfg) -> None:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    config.path.write_text(dumps(config).replace("heartbeat_seconds = 60", "heartbeat_seconds = 1"))
    with pytest.raises(ConfigError, match="heartbeat_seconds"):
        load(config.path)


def test_config_bad_layout(config: Cfg) -> None:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    config.path.write_text(dumps(config) + "\n[layouts.broken]\nargs = []\n")
    with pytest.raises(ConfigError, match=r"layouts\.broken"):
        load(config.path)


def test_state_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s" / "state.json")
    state = State(
        mode=Mode.MIRRORED,
        snapshot=["id:X res:1x1"],
        cursor=LogCursor(5, 10, SessionState.ACTIVE, now()),
        migration=MigrationRecord(migrated_at=now(), imported_layouts=["normal"]),
    )
    store.save(state)
    loaded = store.load()
    assert loaded.mode is Mode.MIRRORED
    assert loaded.cursor.state is SessionState.ACTIVE
    assert loaded.migration is not None
    assert loaded.migration.imported_layouts == ["normal"]
    assert json.loads(store.path.read_text())["version"] == 1
    assert not list(store.path.parent.glob(".state.*"))  # no temp files left behind


def test_state_missing_is_fresh(tmp_path: Path) -> None:
    assert StateStore(tmp_path / "x.json").load().mode is Mode.NORMAL


def test_state_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(StateError):
        StateStore(path).load()


def _hold(path: str, seconds: float) -> None:
    with file_lock(Path(path)):
        time.sleep(seconds)


def test_lock_excludes_other_processes(tmp_path: Path) -> None:
    lock = tmp_path / "m.lock"
    proc = multiprocessing.get_context("fork").Process(target=_hold, args=(str(lock), 2.0))
    proc.start()
    time.sleep(0.5)
    try:
        with pytest.raises(LockHeldError), file_lock(lock, wait=0.3):
            pass
    finally:
        proc.join()
    with file_lock(lock, wait=0.3):
        pass
