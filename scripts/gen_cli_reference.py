"""Generate the Markdown CLI reference (``docs/reference/cli.md``) from the Typer app.

The page is built by walking mirrormax's command tree, so it always matches
``mirrormax --help``. Rich markup in help text and epilogs is translated to
Markdown: ``[cyan]cmd[/]`` becomes inline code, ``[bold]x[/]`` becomes bold, and
indented example lines in epilogs become ``console`` code blocks.

Usage::

    uv run python scripts/gen_cli_reference.py           # rewrite the page
    uv run python scripts/gen_cli_reference.py --check   # exit 1 if it is stale
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import typer

from mirrormax.cli import app

#: Where the generated page lives, relative to the repository root.
OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "reference" / "cli.md"

_HEADER = """\
---
icon: lucide/terminal
---

# CLI reference

<!--
  GENERATED FILE - do not edit by hand.
  Regenerate with: uv run python scripts/gen_cli_reference.py
-->

Every command, argument and option, generated from `mirrormax --help`.
Global options go **before** the command name (`mirrormax -n mirror`); command
options go after it (`mirrormax mirror --dry-run`).
"""

# `[style]text[/]` or `[style]text[/style]`; styles never nest in mirrormax's help.
_MARKUP = re.compile(r"\[(?P<style>[a-z ]+)\](?P<text>.*?)\[/(?P=style)?\]")
# An epilog example line: two-space indent, the command in cyan, an optional note.
_EXAMPLE = re.compile(r"^\s+\[cyan\](?P<cmd>.+?)\[/\]\s*(?P<note>.*)$")
# A positional argument in Typer's usage line, e.g. `{name}`.
_USAGE_ARG = re.compile(r"\{(\w+)\}")


def markup_to_markdown(text: str) -> str:
    """Translate the small subset of Rich markup used in mirrormax's help to Markdown.

    Args:
        text: Help text that may contain Rich markup tags.

    Returns:
        The text with ``cyan`` spans as inline code, ``bold`` spans as bold and
        any other style dropped (its text kept).
    """

    def _swap(match: re.Match[str]) -> str:
        style, inner = match["style"], match["text"]
        if "cyan" in style:
            return f"`{inner}`"
        if "bold" in style:
            return f"**{inner}**"
        return inner

    return _MARKUP.sub(_swap, text)


def epilog_to_markdown(epilog: str) -> str:
    """Render a command epilog, turning indented example lines into a console block.

    Args:
        epilog: The raw epilog string (paragraphs separated by blank lines).

    Returns:
        Markdown for the epilog.
    """
    out: list[str] = []
    examples: list[str] = []

    def _flush() -> None:
        if examples:
            out.append("```console\n" + "\n".join(examples) + "\n```")
            examples.clear()

    for para in (p for p in epilog.split("\n\n") if p.strip()):
        if match := _EXAMPLE.match(para):
            note = match["note"].strip()
            # Notes wrapped in parentheses are already prose; keep them as comments.
            examples.append(f"$ {match['cmd']}" + (f"  # {note.strip('()')}" if note else ""))
            continue
        _flush()
        out.append(markup_to_markdown(para.strip()))
    _flush()
    return "\n\n".join(out)


def _cell(text: str) -> str:
    """Escape text for use inside a Markdown table cell.

    Args:
        text: Raw cell text.

    Returns:
        Text with pipes escaped and newlines collapsed.
    """
    return " ".join(text.split()).replace("|", r"\|")


def _option_rows(command: Any, ctx: typer.Context) -> tuple[list[str], list[str]]:
    """Build table rows for a command's arguments and options.

    Args:
        command: A Click/Typer command object.
        ctx: The context the command would run in.

    Returns:
        ``(argument_rows, option_rows)`` as Markdown table rows.
    """
    arguments: list[str] = []
    options: list[str] = []
    for param in command.get_params(ctx):
        if getattr(param, "hidden", False):
            continue
        help_text = _cell(markup_to_markdown(getattr(param, "help", None) or ""))
        value = _value_type(param, ctx)
        if param.param_type_name == "argument":
            required = "yes" if param.required else "no"
            arg_name = (param.name or "").upper()
            arguments.append(f"| `{arg_name}` | {value} | {help_text} | {required} |")
            continue
        names = ", ".join(f"`{n}`" for n in [*param.opts, *param.secondary_opts])
        default = param.default
        show_default = (
            default not in (None, False, "", ())
            and not callable(default)
            and getattr(param, "show_default", True) is not False
        )
        default_cell = f"`{default}`" if show_default else ""
        options.append(f"| {names} | {value} | {help_text} | {default_cell} |")
    return arguments, options


def _value_type(param: Any, ctx: typer.Context) -> str:
    """Describe the value a parameter takes, e.g. ``<int>`` or ``<watch|poll>``.

    Args:
        param: A Typer option or argument.
        ctx: The context the command would run in.

    Returns:
        A Markdown table cell; empty for boolean flags, which take no value.
    """
    if getattr(param, "is_flag", False):
        return ""
    display = getattr(param, "display_type", None)
    if display is None:
        return ""
    # Pipes inside a code span don't split table cells, so no escaping is needed
    # (and an escaped `\\|` would show its backslash inside the code span).
    return f"`{display(ctx)}`"


def render_command(
    command: Any, name: str, parent: typer.Context | None = None, depth: int = 2
) -> str:
    """Render one command (and, for groups, all of its subcommands) as Markdown.

    Args:
        command: The Click/Typer command or group.
        name: The full invocation, e.g. ``mirrormax layout save``.
        parent: The parent context, for subcommands.
        depth: Heading level for this command.

    Returns:
        A Markdown section.
    """
    ctx = typer.Context(command, info_name=name.rsplit(maxsplit=1)[-1], parent=parent)
    parts: list[str] = [f"{'#' * depth} `{name}`"]
    if command.help:
        parts.append(markup_to_markdown(command.help.strip()))

    # Typer renders argument metavars as `{name}`; show them as NAME like `--help` does.
    usage = _USAGE_ARG.sub(lambda m: m[1].upper(), " ".join(command.collect_usage_pieces(ctx)))
    parts.append(f"```console\n$ {name} {usage}\n```".replace(" \n", "\n"))

    arguments, options = _option_rows(command, ctx)
    if arguments:
        parts.append(
            "**Arguments**\n\n| Argument | Value | Description | Required |\n|---|---|---|---|\n"
            + "\n".join(arguments)
        )
    if options:
        parts.append(
            "**Options**\n\n| Option | Value | Description | Default |\n|---|---|---|---|\n"
            + "\n".join(options)
        )
    if command.epilog:
        parts.append(epilog_to_markdown(command.epilog))

    sections = ["\n\n".join(parts)]
    if isinstance(command, typer.core.TyperGroup):
        # Keep the order users see in `--help` (registration order), not alphabetical.
        for sub_name in command.commands:
            sub = command.commands[sub_name]
            if getattr(sub, "hidden", False):
                continue
            sections.append(render_command(sub, f"{name} {sub_name}", ctx, depth + 1))
    return "\n\n".join(sections)


def render() -> str:
    """Render the complete CLI reference page.

    Returns:
        The page's Markdown, ending in a single newline.
    """
    root = typer.main.get_command(app)
    return f"{_HEADER}\n{render_command(root, 'mirrormax')}\n"


def main(argv: list[str] | None = None) -> int:
    """Write the reference page, or with ``--check`` verify it is up to date.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    args = sys.argv[1:] if argv is None else argv
    content = render()
    if "--check" in args:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print(
                f"{OUTPUT} is out of date; run: uv run python scripts/gen_cli_reference.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} is up to date.")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
