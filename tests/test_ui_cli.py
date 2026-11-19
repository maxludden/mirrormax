"""Tests for human output (snapshots, color rules, narrow terminals) and the CLI."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import DATA, LINES, START_LINE, FakeBackend, FakeLaunchd, append
from rich.console import Console, RenderableType
from typer.testing import CliRunner

from mirrormax import cli, ui
from mirrormax.agent import AgentStatus, Launchctl
from mirrormax.config import Config, save
from mirrormax.detector import SessionState
from mirrormax.doctor import Check, Report, Status
from mirrormax.errors import ConfigError
from mirrormax.layouts import ListedLayout
from mirrormax.legacy import LegacyInfo
from mirrormax.paths import AGENT_LABEL, LEGACY_LABEL
from mirrormax.state import Mode
from mirrormax.status import StatusReport

SNAPSHOTS = DATA / "snapshots"
NOW = datetime(2026, 11, 19, 19, 25, tzinfo=UTC)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def render(renderable: RenderableType, width: int = 100) -> str:
    console = Console(
        record=True,
        width=width,
        theme=ui.THEME,
        color_system=None,
        force_terminal=False,
        file=open(os.devnull, "w"),
    )
    console.print(renderable)
    return console.export_text()


def check_snapshot(name: str, text: str) -> None:
    path = SNAPSHOTS / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8"), (
        f"snapshot {name} changed; UPDATE_SNAPSHOTS=1 to accept"
    )


def legacy(active: bool) -> LegacyInfo:
    return LegacyInfo(
        script=Path("/Users/max/dev/scripts/vnc-mirror.sh"),
        script_exists=True,
        plist=Path("/Users/max/Library/LaunchAgents/com.maxludden.vnc-mirror.plist"),
        plist_exists=True,
        loaded=active,
        disabled=not active,
        mirrored=False,
    )


def report(**changes: object) -> StatusReport:
    base: dict[str, object] = {
        "session": SessionState.ACTIVE,
        "session_started": NOW - timedelta(minutes=23),
        "session_error": None,
        "mode": Mode.MIRRORED,
        "shadow": False,
        "shadow_reason": "",
        "shadow_note": None,
        "warning": None,
        "last_switch": NOW - timedelta(minutes=23),
        "last_switch_action": "mirrored",
        "last_check": NOW - timedelta(seconds=3),
        "has_snapshot": True,
        "agent": AgentStatus(
            AGENT_LABEL,
            Path("/Users/max/Library/LaunchAgents/x.plist"),
            True,
            True,
            False,
            "running",
            812,
            "0",
            "watch",
            ("/Users/max/.local/bin/mirrormax", "watch"),
        ),
        "legacy": legacy(False),
        "migration": None,
        "config_path": Path("/Users/max/.config/mirrormax/config.toml"),
        "config_exists": True,
        "log_path": Path("/Library/Logs/rvncserver.log"),
    }
    base.update(changes)
    return StatusReport(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ snapshots
def test_status_snapshot() -> None:
    text = render(ui.status_view(report(), NOW))
    assert "● Mirrored · VNC session active since" in text
    check_snapshot("status-mirrored", text)


def test_status_shadow_and_drift_snapshot() -> None:
    text = render(
        ui.status_view(
            report(
                mode=Mode.NORMAL,
                shadow=True,
                shadow_reason="legacy agent loaded",
                legacy=legacy(True),
                shadow_note="Would mirror (shadow mode: legacy agent loaded)",
                warning="Display drift: can't mirror; layout 'mirror' needs display(s) 6B26DAE3 "
                "that aren't connected",
            ),
            NOW,
        )
    )
    assert "◐ Shadow mode" in text and "mirrormax migrate" in text and "Display drift" in text
    check_snapshot("status-shadow-drift", text)


def test_doctor_snapshot() -> None:
    rep = Report(
        [
            Check("macOS", Status.OK, "26.1"),
            Check("Config", Status.OK, "~/.config/mirrormax/config.toml"),
            Check(
                "displayplacer",
                Status.OK,
                "/opt/homebrew/bin/displayplacer · 2 display(s) connected",
            ),
            Check("Layout `normal`", Status.OK, "all displays connected"),
            Check("Launch agent", Status.WARN, "not installed", "mirrormax agent install"),
            Check(
                "Legacy vnc-mirror",
                Status.WARN,
                "legacy agent loaded: mirrormax is in shadow mode",
                "mirrormax migrate",
            ),
        ]
    )
    text = render(ui.doctor_view(rep))
    assert "4 passed · 2 warnings · 0 errors" in text
    assert "How to fix" in text
    check_snapshot("doctor", text)


def test_layout_show_snapshot(config: Config, normal_listing: ListedLayout) -> None:
    text = render(ui.layout_detail(config.layout("normal"), normal_listing.connected))
    assert "Arrangement" in text and "#1 1280×832 ★" in text
    check_snapshot("layout-normal", text)


def test_error_panel_snapshot() -> None:
    text = render(
        ui.error_panel(
            ConfigError(
                "No mirrormax config yet",
                cause="~/.config/... doesn't exist.",
                fix="Run `mirrormax init`.",
            )
        )
    )
    assert "What" in text and "Why" in text and "Fix" in text
    check_snapshot("error", text)


@pytest.mark.parametrize("width", [60, 80])
def test_narrow_terminal_does_not_overflow(
    width: int, config: Config, normal_listing: ListedLayout
) -> None:
    rep = Report(
        [
            Check(
                "Legacy vnc-mirror",
                Status.WARN,
                "legacy agent loaded: mirrormax is in shadow "
                "mode and will stay there until you migrate",
                "mirrormax migrate",
            )
        ]
    )
    for renderable in (
        ui.status_view(report(), NOW),
        ui.doctor_view(rep),
        ui.layouts_table(config, normal_listing.connected),
        ui.layout_detail(config.layout("normal"), normal_listing.connected),
    ):
        for line in render(renderable, width).splitlines():
            assert len(line) <= width, line


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: Config,
    backend: FakeBackend,
    launchd: FakeLaunchd,
) -> Config:
    home = config.path.parents[2]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    monkeypatch.setattr(cli.App, "backend", lambda self, cfg: backend)
    monkeypatch.setattr(
        cli.App, "lc", lambda self: Launchctl(runner=launchd, target_domain="gui/501")
    )
    save(config)
    return config


def invoke(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run the real entry point (including error rendering) and capture output."""
    runner = CliRunner()
    result = runner.invoke(cli.app, list(args), env=env, catch_exceptions=True)
    if result.exception and not isinstance(result.exception, SystemExit):
        # Render the way main() does.
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, theme=ui.THEME, width=100, color_system=None)
        if hasattr(result.exception, "what"):
            console.print(ui.error_panel(result.exception))  # type: ignore[arg-type]
        return 1, result.stdout, buf.getvalue()
    return result.exit_code, result.stdout, result.stderr


def test_status_json_is_pure(cli_env: Config, log_path: Path) -> None:
    append(log_path, LINES[: START_LINE + 1])
    code, out, _ = invoke("status", "--json", env={"FORCE_COLOR": "1"})
    assert code == 0
    data = json.loads(out)
    assert data["schema"] == "mirrormax.status/1"
    assert data["session"] == "active"
    assert not ANSI.search(out)


def test_no_color_flag_beats_force_color(cli_env: Config) -> None:
    _, colored, _ = invoke("status", env={"FORCE_COLOR": "1"})
    assert ANSI.search(colored)
    _, plain, _ = invoke("--no-color", "status", env={"FORCE_COLOR": "1"})
    assert not ANSI.search(plain)
    _, nocolor_env, _ = invoke("status", env={"FORCE_COLOR": "", "NO_COLOR": "1"})
    assert not ANSI.search(nocolor_env)


def test_piped_output_is_plain(cli_env: Config) -> None:
    _, out, _ = invoke("layout", "list")
    assert not ANSI.search(out)
    assert "normal" in out and "mirror" in out


def test_mirror_dry_run(cli_env: Config, backend: FakeBackend) -> None:
    code, out, _ = invoke("mirror", "--dry-run")
    assert code == 0
    assert "Dry run: nothing changed" in out
    assert "Would mirror displays" in out
    assert backend.applied == []


def test_check_exit_codes(cli_env: Config, log_path: Path) -> None:
    append(log_path, LINES[: START_LINE + 1])
    code, out, _ = invoke("check")
    assert code == 10
    assert "Mirrored displays" in out
    code, _, _ = invoke("check")
    assert code == 0


def test_check_shadow_mode_when_legacy_loaded(
    cli_env: Config, log_path: Path, launchd: FakeLaunchd, backend: FakeBackend
) -> None:
    launchd.loaded[LEGACY_LABEL] = 99
    append(log_path, LINES[: START_LINE + 1])
    code, out, _ = invoke("check", "--json")
    assert code == 0
    assert json.loads(out)["action"] == "shadow"
    assert backend.applied == []


def test_doctor_json(cli_env: Config) -> None:
    code, out, _ = invoke("doctor", "--json")
    data = json.loads(out)
    assert data["schema"] == "mirrormax.doctor/1"
    assert {c["name"] for c in data["checks"]} >= {"Config", "Launch agent", "Legacy vnc-mirror"}
    assert code == (0 if data["ok"] else 1)


def test_missing_config_renders_error(cli_env: Config) -> None:
    cli_env.path.unlink()
    code, _, err = invoke("mirror")
    assert code == 1
    assert "No mirrormax config yet" in err
    assert "mirrormax init" in err


def test_main_renders_error_panel(
    cli_env: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env.path.unlink()
    monkeypatch.setattr(sys, "argv", ["mirrormax", "--no-color", "mirror"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 1
    captured = capsys.readouterr()
    assert "Error" in captured.err and "mirrormax init" in captured.err
    assert captured.out == ""


def test_main_json_error(
    cli_env: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env.path.unlink()
    monkeypatch.setattr(sys, "argv", ["mirrormax", "mirror", "--json"])
    with pytest.raises(SystemExit):
        cli.main()
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "mirrormax.error/1"


def test_help_groups_commands() -> None:
    result = CliRunner().invoke(cli.app, ["-h"], env={"COLUMNS": "100"})
    assert result.exit_code == 0
    for panel in ("Setup", "Switching", "Automation", "Diagnostics"):
        assert panel in result.stdout


def test_layout_show_current(cli_env: Config) -> None:
    code, out, _ = invoke("layout", "show", "current")
    assert code == 0
    assert "Arrangement" in out and "All displays connected" in out


def test_init_import_legacy(cli_env: Config) -> None:
    cli_env.path.unlink()
    script = (
        cli_env.path.parents[2] / "dev" / "scripts" / "vnc-mirror.sh"
    )  # default legacy location
    script.parent.mkdir(parents=True)
    script.write_text((DATA / "vnc-mirror.sh").read_text())
    code, out, _ = invoke("init", "--import-legacy", "--yes")
    assert code == 0, out
    assert "Imported 2 layouts" in out
