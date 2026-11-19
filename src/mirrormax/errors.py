"""Typed errors for mirrormax.

Every error carries three pieces of human-facing text, *what* happened, the
likely *cause*, and the *fix*, so the UI layer can render a helpful panel
instead of a traceback.
"""

from __future__ import annotations


class MirrormaxError(Exception):
    """Base class for all expected, user-facing mirrormax errors.

    Attributes:
        what: One-line description of what went wrong.
        cause: The most likely reason, or an empty string if unknown.
        fix: What the user can do next, ideally a command to run.
    """

    def __init__(self, what: str, *, cause: str = "", fix: str = "") -> None:
        """Create the error.

        Args:
            what: One-line description of what went wrong.
            cause: The most likely reason, if known.
            fix: What the user can do next.
        """
        super().__init__(what)
        self.what = what
        self.cause = cause
        self.fix = fix


class CommandError(MirrormaxError):
    """An external command exited with a non-zero status."""


class CommandNotFoundError(MirrormaxError):
    """An external command could not be found or executed."""


class CommandTimeoutError(MirrormaxError):
    """An external command did not finish within its timeout."""


class ConfigError(MirrormaxError):
    """The configuration file is missing, unreadable or invalid."""


class StateError(MirrormaxError):
    """The state file is unreadable or corrupt."""


class LockHeldError(MirrormaxError):
    """Another mirrormax process holds the lock."""


class LayoutError(MirrormaxError):
    """A display layout could not be parsed, found or applied."""


class LogUnreadableError(MirrormaxError):
    """RealVNC's log file exists but cannot be read."""


class AgentError(MirrormaxError):
    """A launchd operation failed."""


class LegacyError(MirrormaxError):
    """Something about the legacy vnc-mirror setup prevents the operation."""
