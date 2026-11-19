"""Shared fixtures: fake displays, fake launchctl, temp paths and log files."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mirrormax.agent import Launchctl
from mirrormax.config import Config, DetectionConfig, LegacyConfig
from mirrormax.controller import Controller, LegacyStatus
from mirrormax.layouts import Layout, ListedLayout, parse_list_output
from mirrormax.paths import Paths
from mirrormax.proc import CommandResult
from mirrormax.state import StateStore

DATA = Path(__file__).parent / "data"
A = "37D8832A-2D66-02CA-B9F7-8F30A301B230"
B = "6B26DAE3-CCEA-48CF-B064-462B0F753F39"
LOG_TEXT = (DATA / "rvnc-log.txt").read_text(encoding="utf-8")
LINES = LOG_TEXT.splitlines(keepends=True)
START_LINE = next(i for i, ln in enumerate(LINES) if "Starting desktop" in ln)
STOP_LINE = next(i for i, ln in enumerate(LINES) if "Stopping desktop" in ln)


class FakeBackend:
    """In-memory DisplayBackend: tracks the applied layout and connected displays."""

    def __init__(self, current: ListedLayout) -> None:
        self.current_layout = current.layout
        self.connected = current.connected
        self.applied: list[Layout] = []

    def snapshot(self) -> ListedLayout:
        return ListedLayout(layout=self.current_layout, connected=self.connected)

    def command_for(self, layout: Layout) -> list[str]:
        return ["/opt/homebrew/bin/displayplacer", *layout.args]

    def apply(self, layout: Layout) -> float:
        self.applied.append(layout)
        self.current_layout = layout
        return 0.42


@dataclass
class FakeLaunchd:
    """Fake ``launchctl`` runner holding loaded and disabled labels."""

    loaded: dict[str, int] = field(default_factory=dict)  # label -> pid
    disabled: set[str] = field(default_factory=set)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, args: Sequence[str], timeout: float, check: bool) -> CommandResult:
        argv = tuple(args)
        self.calls.append(argv[1:])
        sub = argv[1]
        out, code = "", 0
        if sub == "print":
            label = argv[2].split("/", 2)[-1]
            if label in self.loaded:
                pid = self.loaded[label]
                out = f"{argv[2]} = {{\n\tstate = running\n\tpid = {pid}\n\tlast exit code = 0\n}}"
            else:
                code, out = 113, f'Could not find service "{label}" in domain'
        elif sub == "print-disabled":
            out = (
                "disabled services = {\n"
                + "".join(f'\t"{lbl}" => disabled\n' for lbl in sorted(self.disabled))
                + "}"
            )
        elif sub == "bootstrap":
            import plistlib

            label = plistlib.loads(Path(argv[3]).read_bytes())["Label"]
            if label in self.loaded:
                code, out = 5, "Bootstrap failed: 5: Input/output error"
            else:
                self.loaded[label] = 4242
        elif sub == "bootout":
            label = argv[2].split("/", 2)[-1]
            if self.loaded.pop(label, None) is None:
                code = 3
        elif sub == "enable":
            self.disabled.discard(argv[2].split("/", 2)[-1])
        elif sub == "disable":
            self.disabled.add(argv[2].split("/", 2)[-1])
        elif sub == "kickstart":
            pass
        return CommandResult(argv, code, out, "", 0.01)


@pytest.fixture(autouse=True)
def fixed_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render local times identically on every machine."""
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths.default(home=tmp_path / "home")


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    path = tmp_path / "rvncserver.log"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def normal_listing() -> ListedLayout:
    return parse_list_output((DATA / "displayplacer-normal.txt").read_text(), name="current")


@pytest.fixture
def mirrored_listing() -> ListedLayout:
    return parse_list_output((DATA / "displayplacer-mirrored.txt").read_text(), name="current")


@pytest.fixture
def config(
    paths: Paths, log_path: Path, normal_listing: ListedLayout, mirrored_listing: ListedLayout
) -> Config:
    return Config(
        path=paths.config_file,
        detection=DetectionConfig(log_path=log_path),
        legacy=LegacyConfig(script=DATA / "vnc-mirror.sh", state_marker=paths.legacy_state_marker),
        layouts={
            "normal": normal_listing.layout.renamed("normal"),
            "mirror": mirrored_listing.layout.renamed("mirror"),
        },
        exists=True,
    )


@pytest.fixture
def backend(normal_listing: ListedLayout) -> FakeBackend:
    return FakeBackend(normal_listing)


class Clock:
    """Controllable clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def make_controller(config: Config, paths: Paths, backend: FakeBackend):  # type: ignore[no-untyped-def]
    def factory(legacy_active: bool = False) -> Controller:
        return Controller(
            config=config,
            store=StateStore(paths.state_file),
            backend=backend,
            lock_path=paths.lock_file,
            legacy=lambda: LegacyStatus(
                legacy_active, "legacy agent loaded" if legacy_active else ""
            ),
            clock=Clock(),
            lock_wait=1.0,
        )

    return factory


@pytest.fixture
def launchd() -> FakeLaunchd:
    return FakeLaunchd()


@pytest.fixture
def lc(launchd: FakeLaunchd) -> Launchctl:
    return Launchctl(runner=launchd, target_domain="gui/501")


def append(path: Path, lines: Sequence[str]) -> None:
    """Append log lines to a file."""
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)
