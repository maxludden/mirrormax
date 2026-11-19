"""Default filesystem locations used by mirrormax.

Config and state follow the XDG layout (``~/.config``, ``~/.local/state``) so
they're easy to find and back up; logs go where macOS users expect them,
``~/Library/Logs``, so they show up in Console.app.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP = "mirrormax"
AGENT_LABEL = "com.maxludden.mirrormax"
LEGACY_LABEL = "com.maxludden.vnc-mirror"


def _xdg(var: str, default: Path) -> Path:
    """Return an XDG base directory, honouring the environment variable.

    Args:
        var: Environment variable name, e.g. ``XDG_CONFIG_HOME``.
        default: Directory to use when the variable is unset or empty.

    Returns:
        The resolved base directory.
    """
    value = os.environ.get(var, "").strip()
    return Path(value) if value else default


@dataclass(frozen=True, slots=True)
class Paths:
    """Every path mirrormax reads or writes.

    Attributes:
        home: The user's home directory.
        config_file: The TOML configuration file.
        state_dir: Directory holding state and the lock file.
        state_file: The JSON state file.
        lock_file: The ``flock`` lock file.
        log_dir: Directory for mirrormax's own logs.
        log_file: The main rotating log file.
        launch_agents_dir: The user's LaunchAgents directory.
        agent_plist: The mirrormax launch agent plist.
        legacy_plist: The old vnc-mirror launch agent plist.
        legacy_script: The old vnc-mirror shell script.
        legacy_state_marker: File the old script creates while mirrored.
    """

    home: Path
    config_file: Path
    state_dir: Path
    state_file: Path
    lock_file: Path
    log_dir: Path
    log_file: Path
    launch_agents_dir: Path
    agent_plist: Path
    legacy_plist: Path
    legacy_script: Path
    legacy_state_marker: Path

    @classmethod
    def default(cls, home: Path | None = None, config_file: Path | None = None) -> Paths:
        """Build the standard set of paths.

        Args:
            home: Home directory to use; defaults to ``Path.home()``.
            config_file: Override for the config file (``--config``).

        Returns:
            The resolved paths.
        """
        home = home or Path.home()
        config_home = _xdg("XDG_CONFIG_HOME", home / ".config")
        state_home = _xdg("XDG_STATE_HOME", home / ".local" / "state")
        state_dir = state_home / APP
        log_dir = home / "Library" / "Logs" / APP
        agents = home / "Library" / "LaunchAgents"
        return cls(
            home=home,
            config_file=config_file or config_home / APP / "config.toml",
            state_dir=state_dir,
            state_file=state_dir / "state.json",
            lock_file=state_dir / f"{APP}.lock",
            log_dir=log_dir,
            log_file=log_dir / f"{APP}.log",
            launch_agents_dir=agents,
            agent_plist=agents / f"{AGENT_LABEL}.plist",
            legacy_plist=agents / f"{LEGACY_LABEL}.plist",
            legacy_script=home / "dev" / "scripts" / "vnc-mirror.sh",
            legacy_state_marker=home / ".vnc-mirror-state",
        )
