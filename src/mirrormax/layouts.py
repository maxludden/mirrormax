"""Display layouts: parsing and describing ``displayplacer`` arguments.

A layout is the list of per-screen argument strings that ``displayplacer``
accepts, for example::

    "id:37D8...B230 res:1280x832 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0"

Mirrored screens share one argument with their IDs joined by ``+``. The raw
strings are kept verbatim so a layout is applied exactly as captured; the
parsed fields exist only for display and validation.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from mirrormax.errors import LayoutError

_ORIGIN_RE = re.compile(r"\((-?\d+),\s*(-?\d+)\)")


@dataclass(frozen=True, slots=True)
class ScreenArg:
    """One ``displayplacer`` screen argument, parsed.

    Attributes:
        raw: The argument exactly as displayplacer prints and accepts it.
        ids: Display IDs in this argument (more than one when mirrored).
        resolution: ``(width, height)`` if given.
        hz: Refresh rate if given.
        scaling: Whether HiDPI scaling is on, if given.
        origin: ``(x, y)`` position in the arrangement, if given.
        degree: Rotation in degrees, if given.
        enabled: Whether the screen is enabled, if given.
    """

    raw: str
    ids: tuple[str, ...]
    resolution: tuple[int, int] | None = None
    hz: int | None = None
    scaling: bool | None = None
    origin: tuple[int, int] | None = None
    degree: int | None = None
    enabled: bool | None = None

    @property
    def mirrored(self) -> bool:
        """Whether this argument mirrors several displays onto one."""
        return len(self.ids) > 1

    @classmethod
    def parse(cls, raw: str) -> ScreenArg:
        """Parse a single screen argument.

        Args:
            raw: e.g. ``"id:A+B res:1470x956 hz:60 scaling:on origin:(0,0) degree:0"``.

        Returns:
            The parsed argument.

        Raises:
            LayoutError: The argument has no ``id:`` field or a malformed value.
        """
        fields: dict[str, str] = {}
        for token in raw.split():
            key, sep, value = token.partition(":")
            if sep:
                fields[key] = value
        if not fields.get("id"):
            raise LayoutError(
                "A display argument has no id",
                cause=f"Got: {raw!r}",
                fix="Copy the arguments from the last line of `displayplacer list`.",
            )
        try:
            res = fields.get("res")
            resolution = tuple(int(n) for n in res.split("x")) if res else None
            origin_match = _ORIGIN_RE.fullmatch(fields["origin"]) if "origin" in fields else None
            return cls(
                raw=raw.strip(),
                ids=tuple(fields["id"].split("+")),
                resolution=(resolution[0], resolution[1]) if resolution else None,
                hz=int(fields["hz"]) if "hz" in fields else None,
                scaling=fields["scaling"] == "on" if "scaling" in fields else None,
                origin=(int(origin_match.group(1)), int(origin_match.group(2)))
                if origin_match
                else None,
                degree=int(fields["degree"]) if "degree" in fields else None,
                enabled=fields["enabled"] == "true" if "enabled" in fields else None,
            )
        except (ValueError, IndexError) as exc:
            raise LayoutError(
                "A display argument has a malformed value",
                cause=f"Got: {raw!r}",
                fix="Copy the arguments from the last line of `displayplacer list`.",
            ) from exc


@dataclass(frozen=True, slots=True)
class Layout:
    """A complete, named display arrangement.

    Attributes:
        name: Layout name, e.g. ``normal`` or ``mirror``.
        screens: The parsed screen arguments, in displayplacer order.
    """

    name: str
    screens: tuple[ScreenArg, ...]

    @classmethod
    def from_args(cls, name: str, args: list[str] | tuple[str, ...]) -> Layout:
        """Build a layout from raw screen argument strings.

        Args:
            name: Layout name.
            args: One string per screen argument.

        Returns:
            The layout.

        Raises:
            LayoutError: ``args`` is empty or an argument is malformed.
        """
        if not args:
            raise LayoutError(
                f"Layout {name!r} has no displays",
                fix=f"Recapture it with `mirrormax layout save {name}`.",
            )
        return cls(name=name, screens=tuple(ScreenArg.parse(a) for a in args))

    @property
    def args(self) -> list[str]:
        """The raw arguments to pass to displayplacer."""
        return [s.raw for s in self.screens]

    @property
    def display_ids(self) -> frozenset[str]:
        """Every display ID the layout refers to."""
        return frozenset(i for s in self.screens for i in s.ids)

    @property
    def is_mirrored(self) -> bool:
        """Whether any screen argument mirrors displays together."""
        return any(s.mirrored for s in self.screens)

    def missing_ids(self, connected: frozenset[str]) -> frozenset[str]:
        """Return the layout's display IDs that aren't currently connected.

        Args:
            connected: IDs of the displays connected right now.

        Returns:
            IDs the layout needs but can't find; empty if it can be applied.
        """
        return self.display_ids - connected

    def renamed(self, name: str) -> Layout:
        """Return a copy of this layout with a different name.

        Args:
            name: The new name.

        Returns:
            The renamed layout.
        """
        return Layout(name=name, screens=self.screens)


def parse_command(text: str, name: str = "imported") -> Layout:
    """Parse a ``displayplacer "..." "..."`` command (or just its arguments).

    Args:
        text: The command line as printed by ``displayplacer list``. The
            leading ``displayplacer`` is optional.
        name: Name for the resulting layout.

    Returns:
        The layout.

    Raises:
        LayoutError: The text can't be split or contains no screen arguments.
    """
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        raise LayoutError("Can't parse the displayplacer command", cause=str(exc)) from exc
    if tokens and tokens[0].endswith("displayplacer"):
        tokens = tokens[1:]
    return Layout.from_args(name, [t for t in tokens if t.startswith("id:")])


@dataclass(frozen=True, slots=True)
class Screen:
    """One physical screen as reported by ``displayplacer list``.

    Attributes:
        id: Persistent screen ID.
        kind: Screen type, e.g. ``MacBook built in screen``.
        resolution: Current resolution text, e.g. ``1280x832``.
        hertz: Refresh rate text.
        origin: Origin text, e.g. ``(0,0) - main display``.
        rotation: Rotation text.
        enabled: Enabled text.
    """

    id: str
    kind: str = ""
    resolution: str = ""
    hertz: str = ""
    origin: str = ""
    rotation: str = ""
    enabled: str = ""


@dataclass(frozen=True, slots=True)
class ListedLayout:
    """The parsed output of ``displayplacer list``.

    Attributes:
        layout: The current arrangement (from the command on the last line).
        screens: Per-screen details.
        connected: Every display ID that is currently connected.
    """

    layout: Layout
    screens: tuple[Screen, ...] = field(default_factory=tuple)
    connected: frozenset[str] = field(default_factory=frozenset)


def parse_list_output(text: str, name: str = "current") -> ListedLayout:
    """Parse the full output of ``displayplacer list``.

    Args:
        text: Everything ``displayplacer list`` printed.
        name: Name to give the captured layout.

    Returns:
        The current layout, screen details and connected display IDs.

    Raises:
        LayoutError: The output has no ``displayplacer`` command line.
    """
    screens: list[Screen] = []
    current: dict[str, str] | None = None
    keys = {
        "Type": "kind",
        "Resolution": "resolution",
        "Hertz": "hertz",
        "Origin": "origin",
        "Rotation": "rotation",
        "Enabled": "enabled",
    }
    command = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("displayplacer ") and "id:" in stripped:
            command = stripped
            continue
        if line.startswith("Persistent screen id:"):
            if current:
                screens.append(Screen(**current))
            current = {"id": line.split(":", 1)[1].strip()}
            continue
        if current is not None and ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            if key.strip() in keys:
                current[keys[key.strip()]] = value.strip()
    if current:
        screens.append(Screen(**current))
    if not command:
        raise LayoutError(
            "displayplacer didn't print a layout command",
            cause="Its output format may have changed, or no displays were reported.",
            fix="Run `displayplacer list` yourself and check the last line.",
        )
    layout = parse_command(command, name=name)
    connected = frozenset(i for s in screens for i in s.id.split("+")) | layout.display_ids
    return ListedLayout(layout=layout, screens=tuple(screens), connected=connected)
