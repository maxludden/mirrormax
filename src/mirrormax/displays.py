"""Reading and changing the display arrangement.

The controller talks to a :class:`DisplayBackend`, never to ``displayplacer``
directly, so tests can use an in-memory fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mirrormax.errors import CommandError, LayoutError
from mirrormax.layouts import Layout, ListedLayout, parse_list_output
from mirrormax.proc import Runner, run


class DisplayBackend(Protocol):
    """Something that can report and change the display arrangement."""

    def snapshot(self) -> ListedLayout:
        """Capture the current arrangement and connected displays.

        Returns:
            The current layout, screens and connected display IDs.
        """
        ...

    def apply(self, layout: Layout) -> float:
        """Switch to a layout.

        Args:
            layout: The layout to apply.

        Returns:
            Seconds the switch took.
        """
        ...

    def command_for(self, layout: Layout) -> list[str]:
        """Return the command line that would apply a layout.

        Args:
            layout: The layout.

        Returns:
            The argument vector, for dry runs and logs.
        """
        ...


class DisplayPlacer:
    """:class:`DisplayBackend` backed by the ``displayplacer`` command-line tool."""

    def __init__(self, path: Path, runner: Runner = run, timeout: float = 20.0) -> None:
        """Create the backend.

        Args:
            path: Path to the ``displayplacer`` executable.
            runner: Command runner (swap in a fake for tests).
            timeout: Seconds to allow each displayplacer call.
        """
        self.path = path
        self._run = runner
        self._timeout = timeout

    def snapshot(self) -> ListedLayout:
        """Run ``displayplacer list`` and parse it.

        Returns:
            The current layout, screens and connected display IDs.

        Raises:
            LayoutError: The output couldn't be parsed.
            CommandError: displayplacer failed.
        """
        result = self._run([str(self.path), "list"], self._timeout, True)
        return parse_list_output(result.stdout)

    def command_for(self, layout: Layout) -> list[str]:
        """Return the displayplacer command line for a layout.

        Args:
            layout: The layout.

        Returns:
            The argument vector.
        """
        return [str(self.path), *layout.args]

    def apply(self, layout: Layout) -> float:
        """Apply a layout with displayplacer.

        Args:
            layout: The layout to apply.

        Returns:
            Seconds the switch took.

        Raises:
            LayoutError: displayplacer rejected the layout.
        """
        try:
            result = self._run(self.command_for(layout), self._timeout, True)
        except CommandError as exc:
            raise LayoutError(
                f"displayplacer couldn't apply layout {layout.name!r}",
                cause=exc.cause or exc.what,
                fix="Check the displays are connected (`mirrormax layout show "
                f"{layout.name}`), then recapture it with `mirrormax layout save {layout.name}`.",
            ) from exc
        return result.duration
