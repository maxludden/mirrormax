"""Logging setup (loguru) and the ``mirrormax logs`` viewer.

* Always: a rotating plain-text file at ``~/Library/Logs/mirrormax/mirrormax.log``
  (visible in Console.app).
* Under launchd (``MIRRORMAX_AGENT=1``): warnings and errors also go to
  stderr as plain timestamped lines, which launchd saves to ``agent.err``.
* In a terminal: ``--verbose`` adds colored debug output on stderr.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from rich.text import Text

if TYPE_CHECKING:
    from loguru import Message
    from rich.console import Console

FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}"
LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \| (?P<level>\w+)\s*\| (?P<msg>.*)$"
)
LEVEL_STYLE = {
    "TRACE": "muted",
    "DEBUG": "muted",
    "INFO": "info",
    "SUCCESS": "ok",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}
LEVELS = ["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]


def is_agent() -> bool:
    """Return whether this process was started by the launch agent.

    Returns:
        True when ``MIRRORMAX_AGENT=1`` (set in the plist).
    """
    return os.environ.get("MIRRORMAX_AGENT") == "1"


def setup(log_file: Path, *, verbose: bool, err_console: Console | None) -> None:
    """Configure loguru sinks for this process.

    Args:
        log_file: The rotating log file.
        verbose: Log debug messages, and echo them to the terminal.
        err_console: Console for terminal echo (ignored under launchd).
    """
    logger.remove()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level="DEBUG" if verbose else "INFO",
            format=FILE_FORMAT,
            rotation="1 MB",
            retention=5,
            encoding="utf-8",
            enqueue=False,
        )
    except OSError:
        pass  # Logging must never stop the tool from working.
    if is_agent():
        logger.add(sys.stderr, level="WARNING", format=FILE_FORMAT, colorize=False)
    elif verbose and err_console is not None:
        logger.add(_console_sink(err_console), level="DEBUG", format="{message}")


def _console_sink(console: Console) -> Callable[[Message], None]:
    """Build a loguru sink that prints styled lines through a Rich console.

    Printing through the console keeps output tidy alongside Live displays.

    Args:
        console: Target console.

    Returns:
        The sink function.
    """

    def sink(message: Message) -> None:
        record = message.record
        level = record["level"].name
        console.print(
            Text.assemble(
                (record["time"].strftime("%H:%M:%S "), "muted"),
                (f"{level:<8} ", LEVEL_STYLE.get(level, "")),
                record["message"],
            )
        )

    return sink


def style_line(line: str) -> Text:
    """Color one log file line by level.

    Args:
        line: A line in :data:`FILE_FORMAT`.

    Returns:
        Styled text (unchanged text if the line doesn't match the format).
    """
    match = LINE_RE.match(line.rstrip("\n"))
    if not match:
        return Text(line.rstrip("\n"))
    level = match.group("level")
    return Text.assemble(
        (match.group("time"), "muted"),
        " ",
        (f"{level:<8}", LEVEL_STYLE.get(level, "")),
        " ",
        match.group("msg"),
    )


def level_of(line: str) -> str | None:
    """Return the level name of a log line.

    Args:
        line: A log line.

    Returns:
        The level, or ``None`` for continuation lines.
    """
    match = LINE_RE.match(line)
    return match.group("level") if match else None


def tail(path: Path, lines: int) -> list[str]:
    """Return the last ``lines`` lines of a file.

    Args:
        path: The file.
        lines: How many lines.

    Returns:
        The lines (without newlines); empty if the file doesn't exist.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, max(4096, lines * 200))
            fh.seek(size - block)
            data = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return data.splitlines()[-lines:]


def follow(path: Path, interval: float = 0.5) -> Iterator[str]:
    """Yield lines appended to a file, surviving rotation.

    Args:
        path: The file to follow.
        interval: Seconds between checks.

    Yields:
        New lines, without newlines.
    """
    fh = None
    inode = None
    while True:
        try:
            st = path.stat()
            if fh is None or st.st_ino != inode:
                if fh is not None:
                    fh.close()
                fh = path.open("r", encoding="utf-8", errors="replace")
                if inode is None:
                    fh.seek(0, os.SEEK_END)
                inode = st.st_ino
        except FileNotFoundError:
            time.sleep(interval)
            continue
        line = fh.readline()
        if line:
            yield line.rstrip("\n")
        else:
            time.sleep(interval)
