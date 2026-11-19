"""Loading, validating and saving ``~/.config/mirrormax/config.toml``.

Example::

    [detection]
    mode = "watch"                       # "watch" (default) or "poll"
    log_path = "/Library/Logs/rvncserver.log"
    heartbeat_seconds = 60               # watch mode: full re-check interval
    poll_interval = 5                    # poll mode: launchd StartInterval

    [displayplacer]
    path = "/opt/homebrew/bin/displayplacer"

    [legacy]
    script = "~/dev/scripts/vnc-mirror.sh"
    state_marker = "~/.vnc-mirror-state"

    [layouts.normal]
    args = ["id:... res:1280x832 ...", "id:... res:1920x1080 ..."]

    [layouts.mirror]
    args = ["id:...+... res:1470x956 ..."]

Reading uses the standard library's ``tomllib``. Writing uses a tiny
purpose-built writer, because the file only ever holds strings, integers and
string arrays, which avoids an extra dependency.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from mirrormax.errors import ConfigError, LayoutError
from mirrormax.layouts import Layout

DEFAULT_LOG = Path("/Library/Logs/rvncserver.log")
DEFAULT_DISPLAYPLACER = Path("/opt/homebrew/bin/displayplacer")
NORMAL = "normal"
MIRROR = "mirror"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class DetectionMode(StrEnum):
    """How the launch agent notices session changes."""

    WATCH = "watch"
    """A long-running process woken by kqueue when the log changes (default)."""
    POLL = "poll"
    """launchd runs ``mirrormax check`` every ``poll_interval`` seconds."""


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Settings for session detection.

    Attributes:
        mode: Watch or poll.
        log_path: RealVNC Server's log file.
        heartbeat_seconds: In watch mode, how often to re-check regardless of events.
        poll_interval: In poll mode, launchd's ``StartInterval`` in seconds.
    """

    mode: DetectionMode = DetectionMode.WATCH
    log_path: Path = DEFAULT_LOG
    heartbeat_seconds: int = 60
    poll_interval: int = 5


@dataclass(frozen=True, slots=True)
class LegacyConfig:
    """Where the old vnc-mirror automation lives.

    Attributes:
        script: The old zsh script holding ``MIRROR_ARGS``/``NORMAL_ARGS``.
        state_marker: File the old script creates while mirrored.
    """

    script: Path
    state_marker: Path


@dataclass(frozen=True, slots=True)
class Config:
    """The complete mirrormax configuration.

    Attributes:
        path: The file this config was loaded from (or will be saved to).
        detection: Detection settings.
        displayplacer: Path to the displayplacer executable.
        legacy: Locations of the old automation.
        layouts: Named layouts, keyed by name.
        exists: Whether ``path`` existed when loaded.
    """

    path: Path
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    displayplacer: Path = DEFAULT_DISPLAYPLACER
    legacy: LegacyConfig = field(
        default_factory=lambda: LegacyConfig(
            script=Path.home() / "dev" / "scripts" / "vnc-mirror.sh",
            state_marker=Path.home() / ".vnc-mirror-state",
        )
    )
    layouts: dict[str, Layout] = field(default_factory=dict)
    exists: bool = False

    def layout(self, name: str) -> Layout:
        """Return a named layout.

        Args:
            name: Layout name.

        Returns:
            The layout.

        Raises:
            LayoutError: No layout has that name.
        """
        try:
            return self.layouts[name]
        except KeyError:
            known = ", ".join(sorted(self.layouts)) or "none yet"
            raise LayoutError(
                f"No layout named {name!r}",
                cause=f"Saved layouts: {known}.",
                fix=f"Capture it with `mirrormax layout save {name}` or run `mirrormax init`.",
            ) from None

    def with_layout(self, layout: Layout) -> Config:
        """Return a copy with a layout added or replaced.

        Args:
            layout: The layout to store under its own name.

        Returns:
            The updated config.
        """
        layouts = dict(self.layouts)
        layouts[layout.name] = layout
        return replace(self, layouts=layouts)

    def without_layout(self, name: str) -> Config:
        """Return a copy with a layout removed.

        Args:
            name: Name of the layout to remove.

        Returns:
            The updated config.

        Raises:
            LayoutError: No layout has that name.
        """
        self.layout(name)
        layouts = {k: v for k, v in self.layouts.items() if k != name}
        return replace(self, layouts=layouts)


def _expand(value: str) -> Path:
    """Expand ``~`` and environment variables in a configured path.

    Args:
        value: Path text from the config file.

    Returns:
        The expanded path.
    """
    return Path(os.path.expandvars(value)).expanduser()


def _line_of(text: str, key: str) -> int | None:
    """Find the line number where a key appears in the TOML source.

    Args:
        text: The TOML source.
        key: A bare key or table name to look for.

    Returns:
        The 1-based line number, or ``None`` if not found.
    """
    pattern = re.compile(rf"^\s*(\[[^\]]*{re.escape(key)}[^\]]*\]|{re.escape(key)}\s*=)")
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return number
    return None


def _invalid(path: Path, text: str, key: str, problem: str, expected: str) -> ConfigError:
    """Build a validation error that points at the offending key.

    Args:
        path: The config file.
        text: Its source text.
        key: Dotted key that is wrong, e.g. ``detection.mode``.
        problem: What is wrong with it.
        expected: What a valid value looks like.

    Returns:
        A ready-to-raise error.
    """
    line = _line_of(text, key.rsplit(".", 1)[-1])
    where = f"{path}:{line}" if line else str(path)
    return ConfigError(
        f"Invalid config: {key} {problem}",
        cause=f"In {where}",
        fix=f"Expected {expected}. Edit the file, then run `mirrormax doctor`.",
    )


def default_config(path: Path) -> Config:
    """Return a config with defaults and no layouts.

    Args:
        path: Where the config would be saved.

    Returns:
        The default config.
    """
    return Config(path=path)


def load(path: Path, *, required: bool = True) -> Config:
    """Load and validate the config file.

    Args:
        path: The TOML file.
        required: If false, return defaults when the file doesn't exist.

    Returns:
        The validated config.

    Raises:
        ConfigError: The file is missing (and required), unreadable, not
            valid TOML, or has invalid values.
    """
    if not path.exists():
        if not required:
            return default_config(path)
        raise ConfigError(
            "No mirrormax config yet",
            cause=f"{path} doesn't exist.",
            fix="Run `mirrormax init` (or `mirrormax init --import-legacy`).",
        )
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except OSError as exc:
        raise ConfigError(f"Can't read {path}", cause=str(exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            "The config file isn't valid TOML",
            cause=f"{path}: {exc}",
            fix="Fix the syntax at that line, then run `mirrormax doctor`.",
        ) from exc
    return _validate(path, text, data)


def _section(data: dict[str, Any], name: str, path: Path, text: str) -> dict[str, Any]:
    """Return a top-level table, or an empty dict if absent.

    Args:
        data: Parsed TOML.
        name: Table name.
        path: Config file (for errors).
        text: Source text (for errors).

    Returns:
        The table.

    Raises:
        ConfigError: The key exists but isn't a table.
    """
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise _invalid(path, text, name, "is not a table", f"a [{name}] section")
    return value


def _int(table: dict[str, Any], key: str, default: int, minimum: int, ctx: tuple[Path, str]) -> int:
    """Read a bounded integer from a table.

    Args:
        table: The TOML table.
        key: Dotted key name (last part is looked up in ``table``).
        default: Value when absent.
        minimum: Smallest allowed value.
        ctx: ``(path, text)`` for error messages.

    Returns:
        The integer.

    Raises:
        ConfigError: The value isn't an integer or is too small.
    """
    value = table.get(key.rsplit(".", 1)[-1], default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(ctx[0], ctx[1], key, f"is {value!r}", f"a whole number ≥ {minimum}")
    return value


def _str(table: dict[str, Any], key: str, default: str, ctx: tuple[Path, str]) -> str:
    """Read a string from a table.

    Args:
        table: The TOML table.
        key: Dotted key name (last part is looked up in ``table``).
        default: Value when absent.
        ctx: ``(path, text)`` for error messages.

    Returns:
        The string.

    Raises:
        ConfigError: The value isn't a non-empty string.
    """
    value = table.get(key.rsplit(".", 1)[-1], default)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(ctx[0], ctx[1], key, f"is {value!r}", "a non-empty string")
    return value


def _validate(path: Path, text: str, data: dict[str, Any]) -> Config:
    """Turn parsed TOML into a :class:`Config`, checking every value.

    Args:
        path: The config file.
        text: Its source text.
        data: The parsed TOML.

    Returns:
        The config.

    Raises:
        ConfigError: Any value is invalid.
    """
    ctx = (path, text)
    det = _section(data, "detection", path, text)
    mode_text = _str(det, "detection.mode", DetectionMode.WATCH.value, ctx)
    try:
        mode = DetectionMode(mode_text)
    except ValueError:
        raise _invalid(
            path, text, "detection.mode", f"is {mode_text!r}", '"watch" or "poll"'
        ) from None
    detection = DetectionConfig(
        mode=mode,
        log_path=_expand(_str(det, "detection.log_path", str(DEFAULT_LOG), ctx)),
        heartbeat_seconds=_int(det, "detection.heartbeat_seconds", 60, 5, ctx),
        poll_interval=_int(det, "detection.poll_interval", 5, 1, ctx),
    )
    dp = _section(data, "displayplacer", path, text)
    displayplacer = _expand(_str(dp, "displayplacer.path", str(DEFAULT_DISPLAYPLACER), ctx))

    leg = _section(data, "legacy", path, text)
    legacy = LegacyConfig(
        script=_expand(_str(leg, "legacy.script", "~/dev/scripts/vnc-mirror.sh", ctx)),
        state_marker=_expand(_str(leg, "legacy.state_marker", "~/.vnc-mirror-state", ctx)),
    )

    layouts: dict[str, Layout] = {}
    for name, table in _section(data, "layouts", path, text).items():
        key = f"layouts.{name}"
        if not _NAME_RE.match(name):
            raise _invalid(path, text, key, "has an invalid name", "letters, digits, - or _")
        args = table.get("args") if isinstance(table, dict) else None
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            raise _invalid(path, text, key, "has no valid `args`", 'args = ["id:... res:...", ...]')
        try:
            layouts[name] = Layout.from_args(name, args)
        except LayoutError as exc:
            raise _invalid(
                path, text, key, f"is malformed ({exc.cause})", "displayplacer args"
            ) from exc

    return Config(
        path=path,
        detection=detection,
        displayplacer=displayplacer,
        legacy=legacy,
        layouts=layouts,
        exists=True,
    )


def _tilde(path: Path) -> str:
    """Render a path with the home directory shortened to ``~``.

    Args:
        path: The path.

    Returns:
        The display string.
    """
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text == home or text.startswith(home + "/") else text


def dumps(config: Config) -> str:
    """Serialise a config to TOML text.

    Args:
        config: The config.

    Returns:
        TOML source.
    """

    def q(value: str) -> str:
        # JSON string escaping is a valid subset of TOML basic strings.
        return json.dumps(value, ensure_ascii=False)

    det = config.detection
    lines = [
        "# mirrormax configuration. Edit freely; run `mirrormax doctor` to check it.",
        "",
        "[detection]",
        f'mode = {q(det.mode.value)}  # "watch" (default) or "poll"',
        f"log_path = {q(str(det.log_path))}",
        f"heartbeat_seconds = {det.heartbeat_seconds}",
        f"poll_interval = {det.poll_interval}",
        "",
        "[displayplacer]",
        f"path = {q(str(config.displayplacer))}",
        "",
        "[legacy]",
        f"script = {q(_tilde(config.legacy.script))}",
        f"state_marker = {q(_tilde(config.legacy.state_marker))}",
    ]
    for name in sorted(config.layouts):
        lines += ["", f"[layouts.{name}]", "args = ["]
        lines += [f"    {q(arg)}," for arg in config.layouts[name].args]
        lines.append("]")
    return "\n".join(lines) + "\n"


def save(config: Config) -> None:
    """Write the config atomically (temp file in the same folder, then rename).

    Args:
        config: The config to save; written to ``config.path``.

    Raises:
        ConfigError: The file couldn't be written.
    """
    path = config.path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dumps(config))
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"Can't write {path}", cause=str(exc)) from exc
