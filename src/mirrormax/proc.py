"""The single place where mirrormax runs external commands.

All subprocess calls go through :func:`run` so they share timeouts, logging
and typed errors, and so tests can swap in a fake runner. ``shell=True`` is
never used: arguments are always passed as a list.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from loguru import logger

from mirrormax.errors import CommandError, CommandNotFoundError, CommandTimeoutError

DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a finished external command.

    Attributes:
        args: The argument vector that was executed.
        returncode: The process exit status.
        stdout: Captured standard output, decoded as UTF-8.
        stderr: Captured standard error, decoded as UTF-8.
        duration: Wall-clock seconds the command took.
    """

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def ok(self) -> bool:
        """Whether the command exited with status 0."""
        return self.returncode == 0


Runner = Callable[[Sequence[str], float, bool], CommandResult]
"""Signature of a command runner: ``(args, timeout, check) -> CommandResult``."""


def run(args: Sequence[str], timeout: float = DEFAULT_TIMEOUT, check: bool = True) -> CommandResult:
    """Run an external command and capture its output.

    Args:
        args: Program and arguments. The first item must be the program.
        timeout: Seconds to wait before giving up.
        check: Raise :class:`CommandError` on a non-zero exit status.

    Returns:
        The captured result.

    Raises:
        CommandNotFoundError: The program does not exist or isn't executable.
        CommandTimeoutError: The command ran longer than ``timeout``.
        CommandError: ``check`` is true and the command failed.
    """
    argv = tuple(args)
    logger.debug("run: {}", " ".join(argv))
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandNotFoundError(
            f"Can't run {argv[0]}",
            cause="The program isn't installed or isn't at that path.",
            fix="Check the path in your config, or run `mirrormax doctor`.",
        ) from exc
    except PermissionError as exc:
        raise CommandNotFoundError(
            f"Not allowed to run {argv[0]}",
            cause="The file exists but isn't executable.",
            fix=f"chmod +x {argv[0]}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeoutError(
            f"{argv[0]} didn't finish within {timeout:g} s",
            cause="The command hung, often because the display system was busy.",
            fix="Try again; if it keeps happening, run `mirrormax doctor`.",
        ) from exc
    result = CommandResult(
        args=argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration=time.monotonic() - start,
    )
    if check and not result.ok:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise CommandError(
            f"{argv[0]} failed with exit status {result.returncode}",
            cause=detail[-1] if detail else "",
            fix="Run the command with --verbose for the full output.",
        )
    return result
