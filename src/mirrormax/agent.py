"""The launchd agent: plist generation and ``launchctl`` operations.

Only the modern ``launchctl`` subcommands are used (``bootstrap``, ``bootout``,
``enable``, ``disable``, ``kickstart``, ``print``). The legacy ``load``/``unload``
pair failed on this Mac with "Load failed: 5: Input/output error", and Apple
has deprecated it anyway.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mirrormax.config import DetectionMode
from mirrormax.errors import AgentError
from mirrormax.paths import AGENT_LABEL, Paths
from mirrormax.proc import Runner, run

LAUNCHCTL = "/bin/launchctl"
AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def domain() -> str:
    """Return the per-user GUI launchd domain, e.g. ``gui/501``.

    Returns:
        The domain target.
    """
    return f"gui/{os.getuid()}"


def find_executable() -> Path:
    """Locate the installed ``mirrormax`` executable for the plist.

    Prefers the script that's running now (so ``uv tool install`` and
    ``uv run`` both work), then ``$PATH``.

    Returns:
        Absolute path to the executable.

    Raises:
        AgentError: No executable could be found.
    """
    argv0 = Path(sys.argv[0])
    if argv0.name == "mirrormax" and argv0.exists():
        return argv0.resolve()
    found = shutil.which("mirrormax")
    if found:
        return Path(found).resolve()
    raise AgentError(
        "Can't find the installed mirrormax command",
        cause="The launch agent needs an absolute path to run.",
        fix="Install it with `uv tool install .` from the project folder, then retry.",
    )


def build_plist(
    *,
    mode: DetectionMode,
    executable: Path,
    paths: Paths,
    poll_interval: int,
    config_file: Path | None = None,
    label: str = AGENT_LABEL,
) -> bytes:
    """Generate the launch agent plist.

    ``watch`` mode runs one long-lived ``mirrormax watch`` process that launchd
    restarts if it exits (``KeepAlive``). ``poll`` mode runs ``mirrormax check``
    every ``poll_interval`` seconds (``StartInterval``), like the old script.

    Args:
        mode: Detection mode.
        executable: Absolute path of the mirrormax executable.
        paths: Standard paths (for log locations).
        poll_interval: Seconds between checks in poll mode.
        config_file: Non-default config file to pass with ``--config``.
        label: launchd label.

    Returns:
        The plist as XML bytes (keys sorted, so output is stable).
    """
    program = [str(executable)]
    if config_file is not None:
        program += ["--config", str(config_file)]
    program.append("watch" if mode is DetectionMode.WATCH else "check")
    data: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(paths.log_dir / "agent.out"),
        "StandardErrorPath": str(paths.log_dir / "agent.err"),
        "EnvironmentVariables": {"PATH": AGENT_PATH, "MIRRORMAX_AGENT": "1"},
    }
    if mode is DetectionMode.WATCH:
        data["KeepAlive"] = True
        data["ThrottleInterval"] = 10
    else:
        data["StartInterval"] = poll_interval
    return plistlib.dumps(data, sort_keys=True)


@dataclass(frozen=True, slots=True)
class AgentStatus:
    """What launchd knows about an agent.

    Attributes:
        label: The launchd label.
        plist: Path of its plist.
        plist_exists: Whether the plist file exists.
        loaded: Whether the job is loaded in the GUI domain.
        disabled: Whether the job is disabled (won't load at login).
        state: launchd's state text, e.g. ``running`` or ``not running``.
        pid: Process ID while running.
        last_exit: Last exit status text, if reported.
        mode: ``watch`` or ``poll`` read from the plist, if it's ours.
        program: The command the plist runs.
    """

    label: str
    plist: Path
    plist_exists: bool
    loaded: bool
    disabled: bool
    state: str | None = None
    pid: int | None = None
    last_exit: str | None = None
    mode: str | None = None
    program: tuple[str, ...] = ()


class Launchctl:
    """Thin, typed wrapper around ``launchctl`` for one user's GUI domain."""

    def __init__(self, runner: Runner = run, target_domain: str | None = None) -> None:
        """Create the wrapper.

        Args:
            runner: Command runner (swap in a fake for tests).
            target_domain: launchd domain; defaults to ``gui/$UID``.
        """
        self._run = runner
        self.domain = target_domain or domain()

    def _lc(self, *args: str, check: bool = False) -> tuple[int, str]:
        """Run ``launchctl`` and return ``(returncode, combined output)``.

        Args:
            *args: launchctl arguments.
            check: Raise on failure.

        Returns:
            Exit status and stdout+stderr.

        Raises:
            AgentError: ``check`` is true and launchctl failed.
        """
        result = self._run([LAUNCHCTL, *args], 15.0, False)
        output = (result.stdout + result.stderr).strip()
        if check and not result.ok:
            raise AgentError(
                f"launchctl {args[0]} failed (status {result.returncode})",
                cause=output.splitlines()[-1] if output else "",
                fix="Run `mirrormax doctor`; check the plist with `plutil -lint`.",
            )
        return result.returncode, output

    def is_loaded(self, label: str) -> bool:
        """Return whether a job is loaded.

        Args:
            label: launchd label.

        Returns:
            True if ``launchctl print`` finds it.
        """
        code, _ = self._lc("print", f"{self.domain}/{label}")
        return code == 0

    def is_disabled(self, label: str) -> bool:
        """Return whether a job is disabled in this domain.

        Args:
            label: launchd label.

        Returns:
            True if ``launchctl print-disabled`` lists it as disabled.
        """
        _, out = self._lc("print-disabled", self.domain)
        match = re.search(rf'"{re.escape(label)}"\s*=>\s*(\w+)', out)
        return bool(match and match.group(1) in {"disabled", "true"})

    def status(self, label: str, plist: Path) -> AgentStatus:
        """Collect everything launchd knows about a job.

        Args:
            label: launchd label.
            plist: Where its plist should be.

        Returns:
            The status.
        """
        code, out = self._lc("print", f"{self.domain}/{label}")
        loaded = code == 0
        state = pid = last_exit = None
        if loaded:
            if m := re.search(r"^\s*state = (.+)$", out, re.M):
                state = m.group(1).strip()
            if m := re.search(r"^\s*pid = (\d+)$", out, re.M):
                pid = int(m.group(1))
            if m := re.search(r"^\s*last exit (?:code|status) = (.+)$", out, re.M):
                last_exit = m.group(1).strip()
        mode = None
        program: tuple[str, ...] = ()
        if plist.exists():
            try:
                data = plistlib.loads(plist.read_bytes())
                program = tuple(str(a) for a in data.get("ProgramArguments", []))
                if data.get("KeepAlive"):
                    mode = DetectionMode.WATCH.value
                elif "StartInterval" in data:
                    mode = DetectionMode.POLL.value
            except (OSError, plistlib.InvalidFileException, ValueError):
                pass
        return AgentStatus(
            label=label,
            plist=plist,
            plist_exists=plist.exists(),
            loaded=loaded,
            disabled=self.is_disabled(label),
            state=state,
            pid=pid,
            last_exit=last_exit,
            mode=mode,
            program=program,
        )

    def bootstrap(self, plist: Path) -> None:
        """Load a plist into the domain.

        Args:
            plist: The plist file.

        Raises:
            AgentError: launchctl refused it.
        """
        self._lc("bootstrap", self.domain, str(plist), check=True)

    def bootout(self, label: str) -> bool:
        """Unload a job if it's loaded.

        Args:
            label: launchd label.

        Returns:
            True if a loaded job was unloaded.

        Raises:
            AgentError: The job is loaded but couldn't be unloaded.
        """
        if not self.is_loaded(label):
            return False
        self._lc("bootout", f"{self.domain}/{label}", check=True)
        return True

    def enable(self, label: str) -> None:
        """Mark a job enabled so it loads at login.

        Args:
            label: launchd label.
        """
        self._lc("enable", f"{self.domain}/{label}", check=True)

    def disable(self, label: str) -> None:
        """Mark a job disabled so it won't load at login, even if its plist exists.

        Args:
            label: launchd label.
        """
        self._lc("disable", f"{self.domain}/{label}", check=True)

    def kickstart(self, label: str) -> None:
        """Restart a loaded job (``kickstart -k``).

        Args:
            label: launchd label.
        """
        self._lc("kickstart", "-k", f"{self.domain}/{label}", check=True)


def install(
    lc: Launchctl,
    plist_bytes: bytes,
    plist_path: Path,
    label: str = AGENT_LABEL,
) -> None:
    """Write the plist and (re)load the agent.

    Args:
        lc: launchctl wrapper.
        plist_bytes: Plist content from :func:`build_plist`.
        plist_path: Where to write it.
        label: launchd label.

    Raises:
        AgentError: Writing or loading failed.
    """
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = plist_path.with_suffix(".plist.tmp")
        tmp.write_bytes(plist_bytes)
        os.replace(tmp, plist_path)
    except OSError as exc:
        raise AgentError(f"Can't write {plist_path}", cause=str(exc)) from exc
    lc.bootout(label)
    lc.enable(label)
    lc.bootstrap(plist_path)


def uninstall(lc: Launchctl, plist_path: Path, label: str = AGENT_LABEL) -> bool:
    """Unload the agent and remove its plist.

    Args:
        lc: launchctl wrapper.
        plist_path: The plist file.
        label: launchd label.

    Returns:
        True if anything was removed or unloaded.

    Raises:
        AgentError: The plist couldn't be removed.
    """
    changed = lc.bootout(label)
    if plist_path.exists():
        try:
            plist_path.unlink()
        except OSError as exc:
            raise AgentError(f"Can't remove {plist_path}", cause=str(exc)) from exc
        changed = True
    return changed
