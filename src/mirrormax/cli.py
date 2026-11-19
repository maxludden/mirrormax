"""The ``mirrormax`` command line (Typer).

This layer only parses arguments, wires collaborators together, and hands
typed results to :mod:`mirrormax.ui` (humans) or :mod:`mirrormax.jsonout`
(``--json``). All decisions live in the controller and friends.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import FrameType
from typing import Annotated, NoReturn

import typer
from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.prompt import Confirm
from rich.text import Text

from mirrormax import jsonout, ui
from mirrormax import logs as logmod
from mirrormax.agent import Launchctl, build_plist, find_executable, install, uninstall
from mirrormax.config import MIRROR, NORMAL, Config, DetectionMode, load, save
from mirrormax.controller import Action, Controller, Outcome
from mirrormax.displays import DisplayPlacer
from mirrormax.doctor import run_doctor
from mirrormax.errors import AgentError, LayoutError, MirrormaxError
from mirrormax.layouts import Layout
from mirrormax.legacy import (
    inspect,
    make_guard,
    migrate,
    parse_legacy_script,
    plan_migration,
    plan_rollback,
    rollback,
)
from mirrormax.paths import AGENT_LABEL, Paths
from mirrormax.state import Mode, StateStore, now
from mirrormax.status import gather
from mirrormax.watcher import KqueueWatcher, WatchEvent, WatchStopped, watch_loop

try:
    __version__ = version("mirrormax")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

SETUP, SWITCHING, AUTOMATION, DIAGNOSTICS = "Setup", "Switching", "Automation", "Diagnostics"


# ----------------------------------------------------------------------- app context
@dataclass(slots=True)
class App:
    """Per-invocation settings and factories shared by every command.

    Attributes:
        paths: Standard paths (``config_file`` reflects ``--config``).
        config_override: The ``--config`` value, if given.
        dry_run: Global ``--dry-run``.
        verbose: Global ``--verbose``.
        no_color: Global ``--no-color``.
        yes: Global ``--yes``.
        json: Whether the running command emits JSON.
        out: Console for normal output.
        err: Console for errors and warnings.
    """

    paths: Paths = field(default_factory=Paths.default)
    config_override: Path | None = None
    dry_run: bool = False
    verbose: bool = False
    no_color: bool = False
    yes: bool = False
    json: bool = False
    out: Console = field(default_factory=ui.make_console)
    err: Console = field(default_factory=lambda: ui.make_console(stderr=True))

    def config(self, *, required: bool = True) -> Config:
        """Load the config.

        Args:
            required: Raise if it doesn't exist (else return defaults).

        Returns:
            The config.
        """
        return load(self.paths.config_file, required=required)

    def backend(self, config: Config) -> DisplayPlacer:
        """Build the displayplacer backend.

        Args:
            config: Config holding the displayplacer path.

        Returns:
            The backend.
        """
        return DisplayPlacer(config.displayplacer)

    def lc(self) -> Launchctl:
        """Build the launchctl wrapper.

        Returns:
            The wrapper.
        """
        return Launchctl()

    def store(self) -> StateStore:
        """Build the state store.

        Returns:
            The store.
        """
        return StateStore(self.paths.state_file)

    def controller(self, config: Config) -> Controller:
        """Build the controller with the legacy shadow-mode guard.

        Args:
            config: Loaded config.

        Returns:
            The controller.
        """
        return Controller(
            config=config,
            store=self.store(),
            backend=self.backend(config),
            lock_path=self.paths.lock_file,
            legacy=make_guard(config, self.paths, self.lc()),
        )

    def confirm(self, question: str, default: bool = True) -> bool:
        """Ask a yes/no question unless ``--yes`` was given.

        Args:
            question: The question.
            default: Answer when the user just presses Enter.

        Returns:
            The answer (always True with ``--yes``).
        """
        if self.yes:
            return True
        if not sys.stdin.isatty():
            raise MirrormaxError(
                "Confirmation needed, but there's no terminal to ask in",
                fix="Re-run with --yes to confirm in advance.",
            )
        return Confirm.ask(question, default=default, console=self.out)

    def emit(self, kind: str, payload: object) -> None:
        """Print a JSON document on stdout (nothing else).

        Args:
            kind: Document kind.
            payload: The data.
        """
        print(jsonout.document(kind, payload))


APP = App()


def _ctx() -> App:
    """Return the shared per-invocation context.

    Returns:
        The context.
    """
    return APP


# ----------------------------------------------------------------------- typer apps
app = typer.Typer(
    name="mirrormax",
    help=(
        "[bold magenta]mirrormax[/] mirrors your external display onto the MacBook screen while "
        "a RealVNC session is active, and puts your previous layout back when it ends."
    ),
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
    pretty_exceptions_enable=False,
    add_completion=True,
    epilog="Start with [cyan]mirrormax init[/], then [cyan]mirrormax doctor[/]. "
    "Docs: README.md in the project folder.",
)
layout_app = typer.Typer(
    help="Save, inspect and apply named display layouts.",
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)
agent_app = typer.Typer(
    help="Install and control the launchd agent that runs mirrormax in the background.",
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)
app.add_typer(layout_app, name="layout", rich_help_panel=SWITCHING)
app.add_typer(agent_app, name="agent", rich_help_panel=AUTOMATION)

JsonOpt = Annotated[
    bool, typer.Option("--json", help="Print plain JSON (stable schema) instead of tables.")
]
DryOpt = Annotated[
    bool, typer.Option("--dry-run", "-n", help="Show what would happen; change nothing.")
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")]


def _version(value: bool) -> None:
    """Print the version and exit (``--version``).

    Args:
        value: Whether the flag was given.
    """
    if value:
        print(f"mirrormax {__version__}")
        raise typer.Exit


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Use this config file.", show_default=False),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Change nothing; print commands.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Debug output and full tracebacks.")
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colored output.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Answer yes to every confirmation.")
    ] = False,
    show_version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Set global options. With no command, show the status."""
    APP.paths = Paths.default(config_file=config.expanduser() if config else None)
    APP.config_override = config.expanduser() if config else None
    APP.dry_run, APP.verbose, APP.no_color, APP.yes = dry_run, verbose, no_color, yes
    APP.out = ui.make_console(no_color=no_color)
    APP.err = ui.make_console(stderr=True, no_color=no_color)
    logmod.setup(APP.paths.log_file, verbose=verbose, err_console=APP.err)
    if ctx.invoked_subcommand is None:
        status(json_out=False)
        APP.out.print(
            Text.assemble(
                ("\nRun ", "muted"), ("mirrormax --help", "cmd"), (" to see all commands.", "muted")
            )
        )


# ----------------------------------------------------------------------- helpers
def _print_outcome(outcome: Outcome, json_out: bool) -> None:
    """Show a controller outcome as JSON or Rich output.

    Args:
        outcome: The outcome.
        json_out: Print JSON.
    """
    if json_out:
        APP.emit("outcome", outcome)
    else:
        APP.out.print(ui.outcome_view(outcome, verbose=APP.verbose))


def _connected(config: Config) -> frozenset[str] | None:
    """Return connected display IDs, or ``None`` if displayplacer can't tell.

    Args:
        config: Config with the displayplacer path.

    Returns:
        The IDs or ``None``.
    """
    try:
        return APP.backend(config).snapshot().connected
    except MirrormaxError as exc:
        logger.debug("can't list displays: {}", exc.what)
        return None


def _send_cmd_f1() -> bool:
    """Press ⌘F1 (toggle display mirroring) via System Events.

    Returns:
        True if osascript reported success.
    """
    script = 'tell application "System Events" to key code 122 using command down'
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# ----------------------------------------------------------------------- setup
@app.command(
    rich_help_panel=SETUP,
    epilog="[bold]Examples[/]\n\n"
    "  [cyan]mirrormax init[/]                  guided capture of both layouts\n\n"
    "  [cyan]mirrormax init --import-legacy[/]  reuse the layouts from vnc-mirror.sh",
)
def init(
    import_legacy: Annotated[
        bool,
        typer.Option("--import-legacy", help="Read MIRROR_ARGS/NORMAL_ARGS from the old script."),
    ] = False,
    yes: YesOpt = False,
) -> None:
    """Guided setup: capture your [green]normal[/] and [magenta]mirror[/] layouts."""
    APP.yes = APP.yes or yes
    config = APP.config(required=False)
    if config.layouts and not APP.confirm(
        f"{config.path} already has layouts ({', '.join(sorted(config.layouts))}). Replace them?",
        default=False,
    ):
        raise typer.Exit(1)

    if import_legacy:
        script = config.legacy.script
        if not script.exists():
            raise MirrormaxError(
                f"Legacy script not found: {script}",
                fix="Set [legacy] script in the config, or run `mirrormax init`.",
            )
        mirror, normal = parse_legacy_script(script.read_text(encoding="utf-8"))
        config = config.with_layout(normal).with_layout(mirror)
        save(config)
        APP.out.print(ui.layouts_table(config, _connected(config), APP.verbose))
        APP.out.print(
            ui.success_panel(f"Imported 2 layouts into {config.path}", _next_step(config))
        )
        return

    total = 5
    out = APP.out
    out.print(ui.step_line(1, total, "Check displayplacer"))
    backend = APP.backend(config)
    with out.status("Reading displays…", spinner="dots"):
        current = backend.snapshot()
    out.print(
        Text.assemble(("  ", ""), ui.badge("ok", f"{len(current.connected)} display(s) found"))
    )
    if current.layout.is_mirrored:
        raise MirrormaxError(
            "Your displays are mirrored right now",
            cause="init captures the normal layout first.",
            fix="Turn mirroring off (⌘F1), arrange your displays, then run `mirrormax init` again.",
        )

    out.print(ui.step_line(2, total, f"Capture your {NORMAL} layout"))
    normal = current.layout.renamed(NORMAL)
    out.print(ui.layout_detail(normal, current.connected, APP.verbose))

    out.print(ui.step_line(3, total, "Turn on mirroring"))
    if APP.confirm("Send ⌘F1 to turn on mirroring for you?", default=True):
        if not _send_cmd_f1():
            out.print(
                Text(
                    "  Couldn't send ⌘F1 (Accessibility permission?). Turn it on yourself.",
                    style="warn",
                )
            )
        time.sleep(3)
    mirrored = None
    for _ in range(3):
        if APP.yes or Confirm.ask("Are your displays mirrored now?", default=True, console=out):
            with out.status("Reading displays…", spinner="dots"):
                listed = backend.snapshot()
            if listed.layout.is_mirrored:
                mirrored = listed.layout.renamed(MIRROR)
                break
            out.print(
                Text(
                    "  displayplacer doesn't see mirroring yet. Use ⌘F1 or "
                    "System Settings → Displays → Mirror.",
                    style="warn",
                )
            )
        if APP.yes:
            time.sleep(2)
    if mirrored is None:
        raise MirrormaxError(
            "Mirroring wasn't detected",
            fix="Turn mirroring on, then run `mirrormax layout save mirror`.",
        )

    out.print(ui.step_line(4, total, f"Capture your {MIRROR} layout"))
    out.print(ui.layout_detail(mirrored, listed.connected, APP.verbose))

    out.print(ui.step_line(5, total, "Restore your normal layout and save"))
    with out.status("Restoring…", spinner="dots"):
        backend.apply(normal)
    config = config.with_layout(normal).with_layout(mirrored)
    save(config)
    out.print(ui.success_panel(f"Saved both layouts to {config.path}", _next_step(config)))


def _next_step(config: Config) -> str:
    """Suggest the command to run after setup.

    Args:
        config: The saved config.

    Returns:
        A command line.
    """
    info = inspect(config, APP.paths, APP.lc())
    if info.active:
        return "mirrormax agent install   (shadow run), then: mirrormax migrate"
    return "mirrormax agent install"


@app.command(
    name="migrate",
    rich_help_panel=SETUP,
    epilog="[bold]Examples[/]\n\n"
    "  [cyan]mirrormax migrate --dry-run[/]   preview the handover\n\n"
    "  [cyan]mirrormax migrate[/]             take over from vnc-mirror\n\n"
    "  [cyan]mirrormax migrate --rollback[/]  give control back to vnc-mirror",
)
def migrate_cmd(
    rollback_: Annotated[
        bool, typer.Option("--rollback", help="Undo: give control back to vnc-mirror.")
    ] = False,
    mode: Annotated[
        DetectionMode | None,
        typer.Option("--mode", help="Agent mode (default: from config).", show_default=False),
    ] = None,
    dry_run: DryOpt = False,
    yes: YesOpt = False,
) -> None:
    """Take over from the old [bold]vnc-mirror[/] automation (safely, reversibly)."""
    APP.yes = APP.yes or yes
    dry = APP.dry_run or dry_run
    config = APP.config(required=False)
    lc = APP.lc()
    store = APP.store()
    info = inspect(config, APP.paths, lc)

    if rollback_:
        steps = plan_rollback(info, store.load().mode is Mode.MIRRORED, config.legacy.state_marker)
        APP.out.print(ui.steps_view(steps, "Rollback plan"))
        if dry:
            APP.out.print(Text("Dry run: nothing changed.", style="info"))
            return
        if not APP.confirm("Roll back to vnc-mirror?", default=False):
            raise typer.Exit(1)
        with APP.out.status("Rolling back…", spinner="dots"):
            rollback(config, APP.paths, store, lc)
        APP.out.print(
            ui.success_panel(
                "vnc-mirror is back in charge; mirrormax is disabled.",
                "mirrormax migrate   (when you want to try again)",
            )
        )
        return

    chosen = mode or config.detection.mode
    steps = plan_migration(config, info, chosen)
    APP.out.print(ui.steps_view(steps, "Migration plan"))
    APP.out.print(
        Text(
            "Nothing is deleted. Undo any time with `mirrormax migrate --rollback`.", style="muted"
        )
    )
    if dry:
        APP.out.print(Text("Dry run: nothing changed.", style="info"))
        return
    if not APP.confirm("Go ahead?", default=True):
        raise typer.Exit(1)
    executable = find_executable()
    counter = iter(range(1, len(steps) + 1))

    def progress(step: object) -> None:
        title = getattr(step, "title", "")
        APP.out.print(ui.step_line(next(counter), len(steps), title))

    migrate(
        config,
        APP.paths,
        store,
        lc,
        executable,
        chosen,
        progress=progress,
        config_override=APP.config_override,
    )
    APP.out.print(
        ui.success_panel(
            "mirrormax is now in charge of your displays.",
            "connect from the iPad, then: mirrormax status",
        )
    )


# ----------------------------------------------------------------------- switching
@app.command(
    rich_help_panel=SWITCHING, epilog="[bold]Example[/]\n\n  [cyan]mirrormax mirror --dry-run[/]"
)
def mirror(dry_run: DryOpt = False, json_out: JsonOpt = False) -> None:
    """Mirror displays now (saves the current layout to restore later)."""
    APP.json = json_out
    outcome = APP.controller(APP.config()).mirror(dry_run=APP.dry_run or dry_run)
    _print_outcome(outcome, json_out)


@app.command(rich_help_panel=SWITCHING, epilog="[bold]Example[/]\n\n  [cyan]mirrormax restore[/]")
def restore(dry_run: DryOpt = False, json_out: JsonOpt = False) -> None:
    """Restore the layout from before mirroring (or your [green]normal[/] layout)."""
    APP.json = json_out
    outcome = APP.controller(APP.config()).restore(dry_run=APP.dry_run or dry_run)
    _print_outcome(outcome, json_out)


@layout_app.command("save", epilog="[bold]Example[/]\n\n  [cyan]mirrormax layout save normal[/]")
def layout_save(
    name: Annotated[str, typer.Argument(help="Layout name, e.g. normal or mirror.")],
    yes: YesOpt = False,
) -> None:
    """Save the current display arrangement under NAME."""
    APP.yes = APP.yes or yes
    config = APP.config(required=False)
    if not name.replace("-", "").replace("_", "").isalnum():
        raise LayoutError(f"Invalid layout name {name!r}", fix="Use letters, digits, - or _.")
    if name in config.layouts and not APP.confirm(f"Replace layout {name!r}?", default=False):
        raise typer.Exit(1)
    with APP.out.status("Reading displays…", spinner="dots"):
        listed = APP.backend(config).snapshot()
    layout = listed.layout.renamed(name)
    if name == MIRROR and not layout.is_mirrored:
        APP.err.print(
            Text(
                f"{ui.SYMBOL['warn']} The current layout isn't mirrored; saving anyway.",
                style="warn",
            )
        )
    save(config.with_layout(layout))
    APP.out.print(ui.layout_detail(layout, listed.connected, APP.verbose))
    APP.out.print(ui.badge("ok", f"Saved {name!r}"))


@layout_app.command("list", epilog="[bold]Example[/]\n\n  [cyan]mirrormax layout list --json[/]")
def layout_list(json_out: JsonOpt = False) -> None:
    """List saved layouts and whether their displays are connected."""
    APP.json = json_out
    config = APP.config()
    connected = _connected(config)
    if json_out:
        APP.emit("layouts", {"layouts": list(config.layouts.values()), "connected": connected})
        return
    if not config.layouts:
        APP.out.print(Text("No layouts saved yet. Run mirrormax init.", style="warn"))
        return
    APP.out.print(ui.layouts_table(config, connected, APP.verbose))


@layout_app.command(
    "show",
    epilog="[bold]Examples[/]\n\n  [cyan]mirrormax layout show normal[/]\n\n"
    "  [cyan]mirrormax layout show current[/]   what's applied right now",
)
def layout_show(
    name: Annotated[
        str, typer.Argument(help="Layout name, or `current` for the live arrangement.")
    ],
    json_out: JsonOpt = False,
) -> None:
    """Show a layout's screens and a map of the arrangement."""
    APP.json = json_out
    config = APP.config(required=name != "current")
    if name == "current":
        listed = APP.backend(config).snapshot()
        layout: Layout = listed.layout
        connected: frozenset[str] | None = listed.connected
    else:
        layout = config.layout(name)
        connected = _connected(config)
    if json_out:
        missing = layout.missing_ids(connected) if connected is not None else None
        APP.emit("layout", {"layout": layout, "connected": connected, "missing": missing})
        return
    APP.out.print(ui.layout_detail(layout, connected, APP.verbose))


@layout_app.command(
    "apply", epilog="[bold]Example[/]\n\n  [cyan]mirrormax layout apply normal --dry-run[/]"
)
def layout_apply(
    name: Annotated[str, typer.Argument(help="Layout to apply.")],
    force: Annotated[
        bool, typer.Option("--force", help="Skip the connected-displays check.")
    ] = False,
    dry_run: DryOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Apply a saved layout now (doesn't change mirrormax's mode)."""
    APP.json = json_out
    outcome = APP.controller(APP.config()).apply_layout(
        name, dry_run=APP.dry_run or dry_run, force=force
    )
    _print_outcome(outcome, json_out)


@layout_app.command(
    "delete", epilog="[bold]Example[/]\n\n  [cyan]mirrormax layout delete old-dock[/]"
)
def layout_delete(
    name: Annotated[str, typer.Argument(help="Layout to delete.")],
    yes: YesOpt = False,
) -> None:
    """Delete a saved layout."""
    APP.yes = APP.yes or yes
    config = APP.config()
    config.layout(name)
    extra = " mirrormax needs it." if name in (NORMAL, MIRROR) else ""
    if not APP.confirm(f"Delete layout {name!r}?{extra}", default=False):
        raise typer.Exit(1)
    save(config.without_layout(name))
    APP.out.print(ui.badge("ok", f"Deleted {name!r}"))


# ----------------------------------------------------------------------- automation
@app.command(
    rich_help_panel=AUTOMATION,
    epilog="Exit codes: [bold]0[/] no change · [bold]1[/] error · "
    "[bold]10[/] mirrored · [bold]11[/] restored\n\n"
    "[bold]Example[/]\n\n  [cyan]mirrormax check -n[/]",
)
def check(dry_run: DryOpt = False, json_out: JsonOpt = False) -> None:
    """Run one detection pass (what the agent runs in [bold]poll[/] mode)."""
    APP.json = json_out
    outcome = APP.controller(APP.config()).check(dry_run=APP.dry_run or dry_run)
    if logmod.is_agent():
        if outcome.action not in (Action.NONE,):
            print(f"{now().astimezone():%Y-%m-%d %H:%M:%S} {outcome.message}")
    else:
        _print_outcome(outcome, json_out)
    raise typer.Exit(outcome.exit_code)


def _raise_stop(signum: int, frame: FrameType | None) -> NoReturn:
    """Signal handler that stops the watch loop.

    Args:
        signum: Signal number.
        frame: Current stack frame (unused).

    Raises:
        WatchStopped: Always.
    """
    raise WatchStopped


@app.command(
    rich_help_panel=AUTOMATION,
    epilog="[bold]Example[/]\n\n  [cyan]mirrormax watch[/]   "
    "(runs until Ctrl-C; the agent runs this in the background)",
)
def watch() -> None:
    """Follow RealVNC's log and switch instantly (the default agent mode)."""
    config = APP.config()
    controller = APP.controller(config)
    heartbeat = float(config.detection.heartbeat_seconds)
    signal.signal(signal.SIGTERM, _raise_stop)
    watcher = KqueueWatcher(config.detection.log_path)
    logger.info("watch started (heartbeat {} s, log {})", int(heartbeat), config.detection.log_path)
    interactive = APP.out.is_terminal and not logmod.is_agent()
    passes = 0
    started = time.monotonic()

    if not interactive:

        def on_plain(
            event: WatchEvent, outcome: Outcome | None, error: MirrormaxError | None
        ) -> None:
            if outcome and outcome.action is not Action.NONE:
                print(
                    f"{now().astimezone():%Y-%m-%d %H:%M:%S} [{event.value}] {outcome.message}",
                    flush=True,
                )

        try:
            watch_loop(controller, watcher, heartbeat, on_plain)
        except (WatchStopped, KeyboardInterrupt):
            logger.info("watch stopped")
        return

    line = ui.LiveLine(
        Mode.NORMAL, None, "starting", now() + timedelta(seconds=heartbeat), 0, False
    )

    class LiveView:
        """Re-renders the status line on every refresh so the countdown ticks."""

        def __rich__(self) -> Text:
            return ui.live_line(line, now())

    with Live(LiveView(), console=APP.out, refresh_per_second=2, transient=True) as live:

        def on_pass(
            event: WatchEvent, outcome: Outcome | None, error: MirrormaxError | None
        ) -> None:
            nonlocal line, passes
            passes += 1
            stamp = Text(f"{now().astimezone():%H:%M:%S} ", style="muted")
            if error is not None:
                live.console.print(Text.assemble(stamp, ui.badge("error", error.what)))
                last = "error"
                mode, session, shadow = line.mode, line.session, line.shadow
            else:
                assert outcome is not None
                if outcome.action is not Action.NONE:
                    live.console.print(Text.assemble(stamp, ui.outcome_line(outcome)))
                last = f"{event.value} · {outcome.action.value}"
                mode, session, shadow = (
                    outcome.mode,
                    outcome.session,
                    outcome.action is Action.SHADOW,
                )
            line = ui.LiveLine(
                mode, session, last, now() + timedelta(seconds=heartbeat), passes, shadow
            )

        with contextlib.suppress(WatchStopped, KeyboardInterrupt):
            watch_loop(controller, watcher, heartbeat, on_pass)
    mins = int((time.monotonic() - started) // 60)
    APP.out.print(
        Text.assemble(
            ui.badge("ok", "Stopped watching"), (f" · {passes} pass(es) in {mins} min", "muted")
        )
    )


@agent_app.command(
    "install",
    epilog="[bold]Examples[/]\n\n  [cyan]mirrormax agent install[/]\n\n"
    "  [cyan]mirrormax agent install --mode poll[/]   fallback: check every few seconds",
)
def agent_install(
    mode: Annotated[
        DetectionMode | None,
        typer.Option("--mode", help="watch (default) or poll.", show_default=False),
    ] = None,
) -> None:
    """Write the launch agent plist and start it."""
    config = APP.config()
    chosen = mode or config.detection.mode
    lc = APP.lc()
    plist = build_plist(
        mode=chosen,
        executable=find_executable(),
        paths=APP.paths,
        poll_interval=config.detection.poll_interval,
        config_file=APP.config_override,
    )
    if APP.dry_run:
        APP.out.print(
            ui.command_panel(
                ["launchctl", "bootstrap", lc.domain, str(APP.paths.agent_plist)],
                "Dry run: nothing changed",
            )
        )
        APP.out.print(plist.decode())
        return
    with APP.out.status("Installing agent…", spinner="dots"):
        install(lc, plist, APP.paths.agent_plist)
    APP.out.print(ui.agent_view(lc.status(AGENT_LABEL, APP.paths.agent_plist)))
    info = inspect(config, APP.paths, lc)
    if info.active:
        APP.out.print(
            ui.warning_panel(
                "Shadow mode",
                [
                    Text(
                        "vnc-mirror is still active, so mirrormax will only log what it would do."
                    ),
                    Text.assemble(("When you're ready: ", "muted"), ("mirrormax migrate", "cmd")),
                ],
            )
        )
    else:
        APP.out.print(
            ui.success_panel(f"Agent installed ({chosen.value} mode)", "mirrormax status")
        )


@agent_app.command("uninstall")
def agent_uninstall(yes: YesOpt = False) -> None:
    """Stop the agent and remove its plist."""
    APP.yes = APP.yes or yes
    if not APP.confirm("Remove the mirrormax launch agent?", default=False):
        raise typer.Exit(1)
    changed = uninstall(APP.lc(), APP.paths.agent_plist)
    APP.out.print(
        ui.badge("ok", "Agent removed")
        if changed
        else Text("Agent wasn't installed.", style="muted")
    )


@agent_app.command("start")
def agent_start() -> None:
    """Start (or restart) the agent."""
    lc = APP.lc()
    if not APP.paths.agent_plist.exists():
        raise AgentError("The agent isn't installed", fix="mirrormax agent install")
    lc.enable(AGENT_LABEL)
    if lc.is_loaded(AGENT_LABEL):
        lc.kickstart(AGENT_LABEL)
        APP.out.print(ui.badge("ok", "Agent restarted"))
    else:
        lc.bootstrap(APP.paths.agent_plist)
        APP.out.print(ui.badge("ok", "Agent started"))


@agent_app.command("stop")
def agent_stop() -> None:
    """Stop the agent until the next login (use uninstall to remove it)."""
    stopped = APP.lc().bootout(AGENT_LABEL)
    APP.out.print(
        ui.badge("ok", "Agent stopped") if stopped else Text("Agent wasn't running.", style="muted")
    )
    if stopped:
        APP.out.print(
            Text("It starts again at login. To remove it: mirrormax agent uninstall", style="muted")
        )


@agent_app.command("status")
def agent_status(json_out: JsonOpt = False) -> None:
    """Show what launchd knows about the agent."""
    APP.json = json_out
    st = APP.lc().status(AGENT_LABEL, APP.paths.agent_plist)
    if json_out:
        APP.emit("agent", st)
    else:
        APP.out.print(ui.agent_view(st))


# ----------------------------------------------------------------------- diagnostics
@app.command(
    rich_help_panel=DIAGNOSTICS,
    epilog="[bold]Examples[/]\n\n  [cyan]mirrormax status[/]\n\n"
    "  [cyan]mirrormax status --json | jq .mode[/]",
)
def status(json_out: JsonOpt = False) -> None:
    """Show the session, display mode, agent and any warnings."""
    APP.json = json_out
    config = APP.config(required=False)
    report = gather(config, APP.paths, APP.lc(), APP.store())
    if json_out:
        APP.emit("status", report)
    else:
        APP.out.print(ui.status_view(report, now()))


@app.command(rich_help_panel=DIAGNOSTICS, epilog="[bold]Example[/]\n\n  [cyan]mirrormax doctor[/]")
def doctor(json_out: JsonOpt = False) -> None:
    """Check everything mirrormax depends on and explain how to fix problems."""
    APP.json = json_out
    report = run_doctor(APP.paths, APP.lc(), APP.backend)
    if json_out:
        APP.emit("doctor", {"ok": report.ok, "checks": report.checks})
    else:
        APP.out.print(ui.doctor_view(report))
    raise typer.Exit(0 if report.ok else 1)


@app.command(
    name="logs",
    rich_help_panel=DIAGNOSTICS,
    epilog="[bold]Examples[/]\n\n"
    "  [cyan]mirrormax logs -f[/]\n\n  [cyan]mirrormax logs --level warning -n 200[/]",
)
def logs_cmd(
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep printing new lines.")
    ] = False,
    lines: Annotated[int, typer.Option("--lines", "-n", help="How many recent lines.")] = 40,
    level: Annotated[
        str, typer.Option("--level", help="Minimum level, e.g. info, warning.")
    ] = "debug",
) -> None:
    """Show (or follow) mirrormax's own log."""
    wanted = level.upper()
    if wanted not in logmod.LEVELS:
        raise MirrormaxError(
            f"Unknown level {level!r}", fix="Use one of: " + ", ".join(logmod.LEVELS).lower()
        )
    floor = logmod.LEVELS.index(wanted)

    def keep(line: str) -> bool:
        lvl = logmod.level_of(line)
        return lvl not in logmod.LEVELS or logmod.LEVELS.index(lvl) >= floor

    path = APP.paths.log_file
    recent = [ln for ln in logmod.tail(path, lines * 4) if keep(ln)][-lines:]
    if not recent and not follow:
        APP.out.print(Text(f"No log entries yet ({path}).", style="muted"))
        return
    for ln in recent:
        APP.out.print(logmod.style_line(ln))
    if follow:
        APP.out.print(Text(f"Following {path} · Ctrl-C to stop", style="muted"))
        try:
            for ln in logmod.follow(path):
                if keep(ln):
                    APP.out.print(logmod.style_line(ln))
        except KeyboardInterrupt:
            pass


# ----------------------------------------------------------------------- entry point
def main() -> None:
    """Console-script entry point: run the app and render errors nicely."""
    try:
        app()
    except MirrormaxError as exc:
        logger.error("{} ({})", exc.what, exc.cause)
        if APP.json:
            APP.emit("error", {"error": exc.what, "cause": exc.cause, "fix": exc.fix})
        else:
            APP.err.print(ui.error_panel(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        APP.err.print(Text("Interrupted.", style="muted"))
        sys.exit(130)
    except Exception as exc:
        logger.exception("unexpected error")
        if APP.verbose:
            APP.err.print_exception(show_locals=False)
        else:
            APP.err.print(
                ui.error_panel(
                    MirrormaxError(
                        f"Unexpected error: {exc}",
                        cause=type(exc).__name__,
                        fix="Re-run with --verbose for the traceback; "
                        "details are in `mirrormax logs`.",
                    )
                )
            )
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
