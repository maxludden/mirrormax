"""Everything mirrormax shows to humans.

One :class:`rich.theme.Theme` defines semantic styles (``ok``, ``warn``,
``error``...), and every renderer uses only those names, so the whole tool
looks consistent and colors can be retuned in one place. Every colored status
also carries a symbol and a word, so nothing depends on color alone and the
output still reads when color is off (``NO_COLOR``, ``--no-color``, pipes).

Renderers take typed results and return Rich renderables; printing is left to
the CLI layer.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from mirrormax.agent import AgentStatus
from mirrormax.config import Config
from mirrormax.controller import Action, Outcome
from mirrormax.detector import SessionState
from mirrormax.doctor import Report, Status
from mirrormax.errors import MirrormaxError
from mirrormax.layouts import Layout, ScreenArg
from mirrormax.legacy import Step
from mirrormax.state import Mode
from mirrormax.status import StatusReport

THEME = Theme(
    {
        "ok": "bold green",
        "warn": "bold yellow",
        "error": "bold red",
        "info": "cyan",
        "muted": "dim",
        "accent": "bold magenta",
        "key": "bold",
        "cmd": "bold cyan",
        "mode.mirrored": "bold magenta",
        "mode.normal": "bold green",
        "mode.shadow": "bold yellow",
        "mode.unknown": "bold dim",
    }
)

SYMBOL = {
    "ok": "✓",
    "warn": "⚠",
    "error": "✗",
    "info": "•",
    "mirrored": "●",
    "normal": "○",
    "shadow": "◐",
    "unknown": "?",
}


def make_console(
    *, stderr: bool = False, no_color: bool = False, width: int | None = None
) -> Console:
    """Create a themed console.

    Rich already honours ``NO_COLOR``/``FORCE_COLOR`` and drops color when
    output isn't a terminal; ``no_color`` adds the ``--no-color`` flag on top.

    Args:
        stderr: Write to standard error instead of standard output.
        no_color: Disable color even in a terminal.
        width: Fixed width (tests); ``None`` follows the terminal.

    Returns:
        The console.
    """
    return Console(
        theme=THEME,
        stderr=stderr,
        no_color=no_color,
        # no_color alone keeps bold/dim; dropping the color system gives truly plain text.
        color_system=None if no_color else "auto",
        highlight=False,
        width=width,
        soft_wrap=False,
    )


# --------------------------------------------------------------------- helpers
def badge(status: str, word: str | None = None) -> Text:
    """Return a symbol-plus-word badge in the status's style.

    Args:
        status: ``ok``, ``warn``, ``error`` or ``info``.
        word: Text after the symbol; defaults to a word for the status.

    Returns:
        The styled text, e.g. ``✓ OK``.
    """
    words = {"ok": "OK", "warn": "Warning", "error": "Error", "info": "Info"}
    return Text(f"{SYMBOL[status]} {word or words[status]}", style=status)


def local(dt: datetime, now: datetime) -> str:
    """Format a timestamp in local time, adding the date if it isn't today.

    Args:
        dt: An aware datetime.
        now: The current time (aware).

    Returns:
        e.g. ``14:02`` or ``Nov 18 14:02``.
    """
    loc = dt.astimezone()
    if loc.date() == now.astimezone().date():
        return loc.strftime("%H:%M")
    return loc.strftime("%b %d %H:%M").replace(" 0", " ")


def ago(dt: datetime, now: datetime) -> str:
    """Describe how long ago a timestamp was, briefly.

    Args:
        dt: The earlier time.
        now: The current time.

    Returns:
        e.g. ``just now``, ``40 s``, ``23 min``, ``2 h 5 min``, ``3 d``.
    """
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs} s"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{hours // 24} d"


def when(dt: datetime | None, now: datetime) -> Text:
    """Format a timestamp as ``14:02 · 23 min ago`` (muted second part).

    Args:
        dt: The time, or ``None``.
        now: The current time.

    Returns:
        Styled text; ``never`` when ``dt`` is ``None``.
    """
    if dt is None:
        return Text("never", style="muted")
    rel = ago(dt, now)
    return Text.assemble(
        local(dt, now), (f" · {rel}{'' if rel == 'just now' else ' ago'}", "muted")
    )


def short_id(display_id: str, verbose: bool = False) -> str:
    """Shorten a display UUID for tables.

    Args:
        display_id: Full persistent display ID.
        verbose: Return the full ID.

    Returns:
        The first block of the UUID, or the full ID.
    """
    return display_id if verbose else display_id.split("-", maxsplit=1)[0]


def kv_table() -> Table:
    """Return an unboxed two-column key/value table.

    Returns:
        The table.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(style="key", no_wrap=True)
    table.add_column(overflow="fold")
    return table


# ---------------------------------------------------------------------- errors
def error_panel(exc: MirrormaxError) -> Panel:
    """Render an error as what / cause / fix.

    Args:
        exc: The error.

    Returns:
        A red panel.
    """
    body = kv_table()
    body.add_row("What", Text(exc.what, style="bold"))
    if exc.cause:
        body.add_row("Why", exc.cause)
    if exc.fix:
        body.add_row("Fix", Text(exc.fix, style="cmd"))
    return Panel(
        body,
        title=Text(f"{SYMBOL['error']} Error", style="error"),
        title_align="left",
        border_style="error",
        box=box.ROUNDED,
        expand=False,
    )


def warning_panel(title: str, lines: Iterable[RenderableType]) -> Panel:
    """Render a highlighted warning.

    Args:
        title: Panel title.
        lines: Content lines.

    Returns:
        A yellow panel.
    """
    return Panel(
        Group(*lines),
        title=Text(f"{SYMBOL['warn']} {title}", style="warn"),
        title_align="left",
        border_style="warn",
        box=box.ROUNDED,
        expand=False,
    )


def success_panel(message: str, next_command: str | None = None) -> Panel:
    """Render a final success message with an optional next step.

    Args:
        message: What succeeded.
        next_command: The command to run next.

    Returns:
        A green panel.
    """
    parts: list[RenderableType] = [Text(message, style="bold")]
    if next_command:
        parts.append(Text.assemble(("Next: ", "muted"), (next_command, "cmd")))
    return Panel(
        Group(*parts),
        title=Text(f"{SYMBOL['ok']} Done", style="ok"),
        title_align="left",
        border_style="ok",
        box=box.ROUNDED,
        expand=False,
    )


# ---------------------------------------------------------------------- status
def headline(report: StatusReport, now: datetime) -> tuple[Text, str]:
    """Pick the one-line answer for ``status`` and its border style.

    Args:
        report: The status report.
        now: The current time.

    Returns:
        ``(text, style name)``.
    """
    since = ""
    if report.session is SessionState.ACTIVE and report.session_started:
        since = f" since {local(report.session_started, now)} ({ago(report.session_started, now)})"
    if report.shadow:
        return (
            Text.assemble(
                (f"{SYMBOL['shadow']} Shadow mode", "mode.shadow"),
                " · the legacy vnc-mirror agent owns the displays",
            ),
            "mode.shadow",
        )
    if report.session is SessionState.UNKNOWN:
        why = report.session_error or "no session marker in the log"
        return Text.assemble(
            (f"{SYMBOL['unknown']} Session unknown", "mode.unknown"), f" · {why}"
        ), "muted"
    if report.mode is Mode.MIRRORED:
        tail = (
            f" · VNC session active{since}"
            if report.session is SessionState.ACTIVE
            else " · session ended, restore pending"
        )
        return Text.assemble(
            (f"{SYMBOL['mirrored']} Mirrored", "mode.mirrored"), tail
        ), "mode.mirrored"
    tail = (
        f" · VNC session active{since}, not mirrored"
        if report.session is SessionState.ACTIVE
        else " · no VNC session"
    )
    return Text.assemble((f"{SYMBOL['normal']} Normal", "mode.normal"), tail), "mode.normal"


def agent_summary(agent: AgentStatus) -> Text:
    """Summarise the launch agent in one line.

    Args:
        agent: Its status.

    Returns:
        Styled text such as ``✓ watch mode · pid 812``.
    """
    if not agent.plist_exists:
        return Text.assemble(badge("warn", "not installed"), ("  mirrormax agent install", "cmd"))
    if agent.disabled:
        return Text.assemble(badge("warn", "disabled"), ("  mirrormax agent start", "cmd"))
    if not agent.loaded:
        return Text.assemble(badge("warn", "not loaded"), ("  mirrormax agent start", "cmd"))
    detail = f"{agent.mode or '?'} mode"
    if agent.pid:
        detail += f" · pid {agent.pid}"
    elif agent.mode == "watch":
        return Text.assemble(badge("warn", detail + " · not running"), ("  mirrormax logs", "cmd"))
    return badge("ok", detail)


def status_view(report: StatusReport, now: datetime) -> Group:
    """Render ``mirrormax status``.

    Args:
        report: The status report.
        now: The current time.

    Returns:
        Headline panel, details table and any warning panels.
    """
    text, style = headline(report, now)
    parts: list[RenderableType] = [
        Panel(
            text,
            title=Text("mirrormax", style="accent"),
            title_align="left",
            border_style=style,
            box=box.ROUNDED,
            padding=(0, 1),
            expand=False,
        )
    ]
    details = kv_table()
    session = {
        SessionState.ACTIVE: Text("active", style="ok"),
        SessionState.ENDED: Text("none", style="muted"),
        SessionState.UNKNOWN: Text("unknown", style="warn"),
    }[report.session]
    details.add_row("Session", session)
    mode_style = "mode.mirrored" if report.mode is Mode.MIRRORED else "mode.normal"
    mode = Text(f"{SYMBOL[report.mode.value]} {report.mode.value}", style=mode_style)
    if report.mode is Mode.MIRRORED:
        mode.append(
            " · previous layout saved" if report.has_snapshot else " · will restore `normal`",
            style="muted",
        )
    details.add_row("Displays", mode)
    details.add_row("Agent", agent_summary(report.agent))
    last = when(report.last_switch, now)
    if report.last_switch_action:
        last = Text.assemble((report.last_switch_action, "bold"), " at ", last)
    details.add_row("Last switch", last)
    details.add_row("Last check", when(report.last_check, now))
    if report.migration and not report.migration.rolled_back_at:
        legacy = Text.assemble(
            badge("ok", "migrated"), (f" · {local(report.migration.migrated_at, now)}", "muted")
        )
    elif report.shadow:
        legacy = badge("warn", report.shadow_reason)
    elif report.legacy.plist_exists or report.legacy.script_exists:
        legacy = Text("inactive", style="muted")
    else:
        legacy = Text("not present", style="muted")
    details.add_row("Legacy", legacy)
    details.add_row(
        "Config",
        Text(
            str(report.config_path) + ("" if report.config_exists else "  (missing)"),
            style="muted" if report.config_exists else "warn",
        ),
    )
    details.add_row("RealVNC log", Text(str(report.log_path), style="muted"))
    parts.append(details)

    if report.shadow:
        lines: list[RenderableType] = [
            Text("mirrormax is watching but won't switch displays while vnc-mirror is active."),
        ]
        if report.shadow_note:
            lines.append(Text.assemble(("Last decision: ", "muted"), report.shadow_note))
        lines.append(Text.assemble(("When you're ready: ", "muted"), ("mirrormax migrate", "cmd")))
        parts.append(warning_panel("Shadow mode", lines))
    if not report.config_exists:
        parts.append(
            warning_panel(
                "Not set up",
                [
                    Text.assemble(
                        "Run ",
                        ("mirrormax init", "cmd"),
                        " or ",
                        ("mirrormax init --import-legacy", "cmd"),
                    )
                ],
            )
        )
    if report.warning:
        fix = "mirrormax doctor"
        if "drift" in report.warning.lower():
            fix = "mirrormax layout list   (then: mirrormax layout save <name>)"
        parts.append(
            warning_panel(
                "Attention", [Text(report.warning), Text.assemble(("Fix: ", "muted"), (fix, "cmd"))]
            )
        )
    return Group(*parts)


# ---------------------------------------------------------------------- doctor
_STATUS_STYLE = {Status.OK: "ok", Status.WARN: "warn", Status.ERROR: "error", Status.INFO: "info"}


def doctor_view(report: Report) -> Group:
    """Render ``mirrormax doctor``.

    Args:
        report: The doctor report.

    Returns:
        Checklist table, fixes and a summary line.
    """
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, show_edge=False)
    table.add_column("Check", style="key", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("Details", overflow="fold")
    for check in report.checks:
        style = _STATUS_STYLE[check.status]
        word = {"ok": "OK", "warn": "Warn", "error": "Fail", "info": "Info"}[style]
        table.add_row(check.name, badge(style, word), check.details)
    parts: list[RenderableType] = [table]

    fixes = [c for c in report.checks if c.fix and c.status in (Status.WARN, Status.ERROR)]
    if fixes:
        fix_table = Table.grid(padding=(0, 1))
        fix_table.add_column(no_wrap=True)
        fix_table.add_column(overflow="fold")
        for c in fixes:
            fix_table.add_row(
                Text(SYMBOL[_STATUS_STYLE[c.status]], style=_STATUS_STYLE[c.status]),
                Text.assemble((c.name, "key"), "\n", (c.fix, "cmd")),
            )
        parts += [Text(""), Text("How to fix", style="bold underline"), fix_table]

    summary = Text("\n")
    counts = [
        (report.count(Status.OK), "passed", "ok"),
        (
            report.count(Status.WARN),
            "warning" if report.count(Status.WARN) == 1 else "warnings",
            "warn",
        ),
        (
            report.count(Status.ERROR),
            "error" if report.count(Status.ERROR) == 1 else "errors",
            "error",
        ),
    ]
    for i, (n, word, style) in enumerate(counts):
        if i:
            summary.append(" · ", style="muted")
        summary.append(f"{n} {word}", style=style if n else "muted")
    parts.append(summary)
    return Group(*parts)


# --------------------------------------------------------------------- outcome
def command_panel(command: Sequence[str], title: str) -> Panel:
    """Show a command line, syntax-highlighted.

    Args:
        command: Argument vector.
        title: Panel title.

    Returns:
        The panel.
    """
    return Panel(
        Syntax(shlex.join(command), "bash", word_wrap=True, background_color="default"),
        title=Text(title, style="info"),
        title_align="left",
        border_style="info",
        box=box.ROUNDED,
        expand=False,
    )


_ACTION_STYLE = {
    Action.MIRRORED: ("mode.mirrored", SYMBOL["mirrored"]),
    Action.RESTORED: ("ok", SYMBOL["ok"]),
    Action.APPLIED: ("ok", SYMBOL["ok"]),
    Action.SKIPPED: ("warn", SYMBOL["warn"]),
    Action.SHADOW: ("mode.shadow", SYMBOL["shadow"]),
    Action.NONE: ("muted", SYMBOL["info"]),
}


def outcome_line(outcome: Outcome) -> Text:
    """Render an outcome as one styled line, e.g. ``✓ Restored previous layout in 0.6 s``.

    Args:
        outcome: The result.

    Returns:
        The line.
    """
    style, symbol = _ACTION_STYLE[outcome.action]
    took = (
        f" in {outcome.duration:.1f} s"
        if outcome.duration is not None and not outcome.dry_run
        else ""
    )
    return Text.assemble((f"{symbol} {outcome.message}", style), (took, "muted"))


def outcome_view(outcome: Outcome, verbose: bool = False) -> Group:
    """Render the result of a switch, check or apply.

    Args:
        outcome: The result.
        verbose: Also show the displayplacer command for real switches.

    Returns:
        A result line, plus a command panel for dry runs (or with ``verbose``).
    """
    parts: list[RenderableType] = []
    if outcome.dry_run and outcome.command:
        parts.append(command_panel(outcome.command, "Dry run: nothing changed"))
    parts.append(outcome_line(outcome))
    if outcome.warning:
        parts.append(Text(f"  {outcome.warning}", style="warn"))
    if verbose and outcome.command and not outcome.dry_run:
        parts.append(command_panel(outcome.command, "displayplacer"))
    return Group(*parts)


# --------------------------------------------------------------------- layouts
def layouts_table(config: Config, connected: frozenset[str] | None, verbose: bool = False) -> Table:
    """Render ``mirrormax layout list``.

    Args:
        config: Config holding the layouts.
        connected: Currently connected display IDs, or ``None`` if unknown.
        verbose: Show full display IDs.

    Returns:
        The table.
    """
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, show_edge=False)
    table.add_column("Layout", style="accent", no_wrap=True)
    table.add_column("Screens", justify="right")
    table.add_column("Kind", no_wrap=True)
    table.add_column("Displays", overflow="fold")
    table.add_column("Status", no_wrap=True)
    for name in sorted(config.layouts):
        layout = config.layouts[name]
        kind = (
            Text(f"{SYMBOL['mirrored']} mirrored", style="mode.mirrored")
            if layout.is_mirrored
            else Text(f"{SYMBOL['normal']} extended", style="mode.normal")
        )
        ids = ", ".join(short_id(i, verbose) for i in sorted(layout.display_ids))
        if connected is None:
            status = Text("unknown", style="muted")
        elif missing := layout.missing_ids(connected):
            status = badge("warn", f"{len(missing)} missing")
        else:
            status = badge("ok", "ready")
        table.add_row(name, str(len(layout.screens)), kind, ids, status)
    return table


def _screen_label(arg: ScreenArg, verbose: bool) -> str:
    """Label one screen argument by its display IDs.

    Args:
        arg: The screen argument.
        verbose: Use full IDs.

    Returns:
        e.g. ``37D8832A`` or ``37D8832A + 6B26DAE3``.
    """
    return " + ".join(short_id(i, verbose) for i in arg.ids)


def arrangement_map(screens: Sequence[ScreenArg], width: int = 48, max_rows: int = 11) -> Text:
    """Draw a small text map of where each screen sits.

    Boxes are placed by origin and sized by resolution (logical points),
    scaled to fit ``width`` characters. Terminal cells are about twice as tall
    as they are wide, so vertical distances are halved.

    Args:
        screens: Screen arguments with resolution and origin.
        width: Maximum map width in characters.
        max_rows: Maximum map height in lines.

    Returns:
        The map, or a short note if positions are unknown.
    """
    boxes = [(i, s) for i, s in enumerate(screens, 1) if s.resolution and s.origin]
    if not boxes:
        return Text("(no position information)", style="muted")
    left = min(s.origin[0] for _, s in boxes if s.origin)
    top = min(s.origin[1] for _, s in boxes if s.origin)
    right = max(s.origin[0] + s.resolution[0] for _, s in boxes if s.origin and s.resolution)
    bottom = max(s.origin[1] + s.resolution[1] for _, s in boxes if s.origin and s.resolution)
    # A cell is ~2x taller than wide, so y uses half the x scale; tall arrangements
    # are squashed to max_rows (the map is a sketch, not a measurement).
    sx = (width - 1) / max(1, right - left)
    sy = min(sx / 2, (max_rows - 1) / max(1, bottom - top))
    rows = max(3, round((bottom - top) * sy) + 1)
    grid = [[" "] * width for _ in range(rows)]
    styles: list[list[str | None]] = [[None] * width for _ in range(rows)]
    for n, s in boxes:
        assert s.origin is not None
        assert s.resolution is not None
        x0 = round((s.origin[0] - left) * sx)
        y0 = round((s.origin[1] - top) * sy)
        # The right/bottom edge stops one cell short so neighbouring screens
        # sit side by side instead of sharing a border.
        x1 = min(width - 1, max(x0 + 11, round((s.origin[0] + s.resolution[0] - left) * sx) - 1))
        y1 = min(rows - 1, max(y0 + 2, round((s.origin[1] + s.resolution[1] - top) * sy) - 1))
        style = "mode.mirrored" if s.mirrored else ("accent" if s.origin == (0, 0) else "info")
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                grid[y][x] = "─"
                styles[y][x] = style
        for y in range(y0, y1 + 1):
            for x in (x0, x1):
                grid[y][x] = "│"
                styles[y][x] = style
        for (y, x), ch in {(y0, x0): "╭", (y0, x1): "╮", (y1, x0): "╰", (y1, x1): "╯"}.items():
            grid[y][x] = ch
            styles[y][x] = style
        label = f"#{n} {s.resolution[0]}×{s.resolution[1]}"
        if s.origin == (0, 0):
            label += " ★"
        inner = x1 - x0 - 1
        label = label[:inner]
        ly = (y0 + y1) // 2
        lx = x0 + 1 + (inner - len(label)) // 2
        for k, ch in enumerate(label):
            grid[ly][lx + k] = ch
            styles[ly][lx + k] = "bold"
    out = Text()
    for y in range(rows):
        line = "".join(grid[y]).rstrip()
        for x, ch in enumerate(line):
            out.append(ch, style=styles[y][x] or "")
        if y < rows - 1:
            out.append("\n")
    return out


def layout_detail(layout: Layout, connected: frozenset[str] | None, verbose: bool = False) -> Group:
    """Render ``mirrormax layout show NAME``.

    Args:
        layout: The layout.
        connected: Connected display IDs, or ``None`` if unknown.
        verbose: Show full display IDs.

    Returns:
        Screen table, arrangement map and a connection note.
    """
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold",
        pad_edge=False,
        show_edge=False,
        title=Text.assemble(("Layout ", "bold"), (layout.name, "accent")),
        title_justify="left",
    )
    table.add_column("#", justify="right", style="muted")
    table.add_column("Display(s)", overflow="fold")
    table.add_column("Resolution", no_wrap=True)
    table.add_column("Hz", justify="right")
    table.add_column("Scaling", no_wrap=True)
    table.add_column("Origin", no_wrap=True)
    table.add_column("Rotation", justify="right")
    for n, s in enumerate(layout.screens, 1):
        label = Text(_screen_label(s, verbose), style="mode.mirrored" if s.mirrored else "")
        if s.mirrored:
            label.append("  mirrored", style="muted")
        table.add_row(
            str(n),
            label,
            f"{s.resolution[0]}×{s.resolution[1]}" if s.resolution else "—",
            str(s.hz) if s.hz else "—",
            {True: "HiDPI", False: "off", None: "—"}[s.scaling],
            f"({s.origin[0]}, {s.origin[1]})" if s.origin else "—",
            f"{s.degree}°" if s.degree is not None else "—",
        )
    parts: list[RenderableType] = [
        table,
        Panel(
            arrangement_map(layout.screens),
            title=Text("Arrangement", style="muted"),
            title_align="left",
            border_style="muted",
            box=box.ROUNDED,
            expand=False,
        ),
        Text.assemble(("★ main display (origin 0,0)", "muted")),
    ]
    if connected is not None:
        missing = layout.missing_ids(connected)
        if missing:
            parts.append(
                Text.assemble(
                    badge("warn", "Not connected: "),
                    ", ".join(short_id(i, verbose) for i in sorted(missing)),
                )
            )
        else:
            parts.append(badge("ok", "All displays connected"))
    return Group(*parts)


# ----------------------------------------------------------------- plans/agent
def steps_view(steps: Sequence[Step], title: str) -> Table:
    """Preview the steps of a migration or rollback.

    Args:
        steps: The plan.
        title: Table title.

    Returns:
        The table.
    """
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold",
        pad_edge=False,
        show_edge=False,
        title=Text(title, style="bold"),
        title_justify="left",
    )
    table.add_column("#", justify="right", style="muted")
    table.add_column("Step", no_wrap=False)
    table.add_column("Details", overflow="fold")
    for n, s in enumerate(steps, 1):
        table.add_row(
            str(n), Text(s.title, style="" if s.run else "muted"), Text(s.detail, style="muted")
        )
    return table


def step_line(index: int, total: int, title: str) -> Text:
    """Render a ``Step 2 of 5 · Title`` progress line.

    Args:
        index: 1-based step number.
        total: Number of steps.
        title: Step title.

    Returns:
        The line.
    """
    return Text.assemble((f"Step {index} of {total}", "accent"), (" · ", "muted"), (title, "bold"))


def agent_view(agent: AgentStatus) -> Table:
    """Render ``mirrormax agent status``.

    Args:
        agent: The agent status.

    Returns:
        A key/value table.
    """
    table = kv_table()
    table.add_row("Label", agent.label)
    table.add_row("Status", agent_summary(agent))
    table.add_row("Loaded", badge("ok", "yes") if agent.loaded else Text("no", style="muted"))
    table.add_row(
        "At login",
        Text("disabled", style="warn")
        if agent.disabled
        else Text("enabled", style="ok")
        if agent.plist_exists
        else Text("—", style="muted"),
    )
    if agent.state:
        table.add_row("launchd state", agent.state)
    if agent.last_exit:
        table.add_row("Last exit", agent.last_exit)
    if agent.program:
        table.add_row("Runs", Text(shlex.join(agent.program), style="cmd"))
    table.add_row("Plist", Text(str(agent.plist), style="muted"))
    return table


@dataclass(frozen=True, slots=True)
class LiveLine:
    """Data for the ``watch`` live status line.

    Attributes:
        mode: Current mode.
        session: Session state.
        last_event: Description of the last event.
        next_heartbeat: When the next unconditional check is due.
        passes: Passes run so far.
        shadow: Whether in shadow mode.
    """

    mode: Mode
    session: SessionState | None
    last_event: str
    next_heartbeat: datetime
    passes: int
    shadow: bool


def live_line(data: LiveLine, now: datetime) -> Text:
    """Render the single status line shown by ``watch`` in a terminal.

    Args:
        data: Current watch data.
        now: The current time.

    Returns:
        The line.
    """
    if data.shadow:
        head = (f"{SYMBOL['shadow']} shadow", "mode.shadow")
    elif data.mode is Mode.MIRRORED:
        head = (f"{SYMBOL['mirrored']} mirrored", "mode.mirrored")
    else:
        head = (f"{SYMBOL['normal']} normal", "mode.normal")
    session = data.session.value if data.session else "?"
    secs = max(0, int((data.next_heartbeat - now).total_seconds()))
    return Text.assemble(
        ("watching ", "muted"),
        head,
        ("  session ", "muted"),
        (session, "bold"),
        ("  last ", "muted"),
        data.last_event,
        ("  heartbeat in ", "muted"),
        f"{secs} s",
        ("  passes ", "muted"),
        str(data.passes),
        ("   Ctrl-C to stop", "muted"),
    )
