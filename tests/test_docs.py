"""Tests for the documentation tooling and release metadata.

These keep the generated CLI reference in sync with the Typer app and make sure
the version numbers a release depends on agree with each other.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import mirrormax

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    """Import ``scripts/gen_cli_reference.py`` (it isn't part of the package)."""
    path = ROOT / "scripts" / "gen_cli_reference.py"
    spec = importlib.util.spec_from_file_location("gen_cli_reference", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_reference_is_up_to_date(gen: ModuleType) -> None:
    committed = (ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    assert committed == gen.render(), (
        "docs/reference/cli.md is stale; run: uv run python scripts/gen_cli_reference.py"
    )


def test_cli_reference_covers_every_command(gen: ModuleType) -> None:
    page = gen.render()
    for command in [
        "init",
        "migrate",
        "mirror",
        "restore",
        "check",
        "watch",
        "status",
        "doctor",
        "logs",
        "layout save",
        "layout list",
        "layout show",
        "layout apply",
        "layout delete",
        "agent install",
        "agent uninstall",
        "agent start",
        "agent stop",
        "agent status",
    ]:
        assert f"`mirrormax {command}`" in page, command
    # No raw Rich markup may leak into the Markdown.
    assert "[cyan]" not in page
    assert "[bold" not in page
    assert "[/]" not in page


@pytest.mark.parametrize(
    ("markup", "markdown"),
    [
        ("Start with [cyan]mirrormax init[/].", "Start with `mirrormax init`."),
        ("[bold]0[/] no change", "**0** no change"),
        ("your [green]normal[/] layout", "your normal layout"),
        ("[bold magenta]mirrormax[/] mirrors", "**mirrormax** mirrors"),
        ("plain text", "plain text"),
    ],
)
def test_markup_to_markdown(gen: ModuleType, markup: str, markdown: str) -> None:
    assert gen.markup_to_markdown(markup) == markdown


def test_epilog_examples_become_console_block(gen: ModuleType) -> None:
    epilog = (
        "[bold]Examples[/]\n\n"
        "  [cyan]mirrormax init[/]   guided capture\n\n"
        "  [cyan]mirrormax watch[/]   (runs until Ctrl-C)"
    )
    assert gen.epilog_to_markdown(epilog) == (
        "**Examples**\n\n"
        "```console\n"
        "$ mirrormax init  # guided capture\n"
        "$ mirrormax watch  # runs until Ctrl-C\n"
        "```"
    )


def test_check_mode_reports_stale_page(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cli.md"
    monkeypatch.setattr(gen, "OUTPUT", target)
    assert gen.main(["--check"]) == 1  # missing
    assert gen.main([]) == 0  # write it
    assert gen.main(["--check"]) == 0  # now current
    target.write_text("stale\n", encoding="utf-8")
    assert gen.main(["--check"]) == 1


def test_versions_agree() -> None:
    """The release workflow requires pyproject's version to match ``__version__``."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == mirrormax.__version__


def test_nav_pages_exist() -> None:
    """Every page in the Zensical nav must exist, or the build silently drops it."""
    config = tomllib.loads((ROOT / "zensical.toml").read_text(encoding="utf-8"))

    def pages(entries: list[object]) -> list[str]:
        found: list[str] = []
        for entry in entries:
            assert isinstance(entry, dict)
            for value in entry.values():
                found.extend(pages(value) if isinstance(value, list) else [str(value)])
        return found

    docs = ROOT / config["project"]["docs_dir"]
    missing = [p for p in pages(config["project"]["nav"]) if not (docs / p).is_file()]
    assert not missing
