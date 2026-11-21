---
icon: lucide/download
---

# Installation

## Requirements

| Requirement | Why | Install |
|---|---|---|
| macOS | mirrormax drives macOS displays and uses launchd and kqueue | n/a |
| Python 3.12 or newer | runtime (uv installs it for you) | n/a |
| [`uv`](https://docs.astral.sh/uv/) | installs mirrormax as an isolated tool | `brew install uv` |
| [`displayplacer`](https://github.com/jakehilborn/displayplacer) | reads and applies display layouts | `brew install displayplacer` |
| RealVNC Server | the VNC server whose sessions mirrormax follows | [realvnc.com](https://www.realvnc.com/) |

## Install mirrormax

=== "From PyPI (recommended)"

    ```sh
    uv tool install mirrormax
    mirrormax --version
    ```

    Upgrade later with:

    ```sh
    uv tool upgrade mirrormax
    mirrormax agent install   # point the agent at the new build
    ```

=== "From a clone"

    ```sh
    git clone https://github.com/maxludden/mirrormax.git
    cd mirrormax
    uv tool install .
    mirrormax --version
    ```

    After pulling changes:

    ```sh
    uv tool install . --reinstall
    mirrormax agent install
    ```

=== "One-click script"

    From a clone, double-click `install.command` in Finder (or run it in a terminal). It:

    1. installs mirrormax with `uv tool install . --reinstall`,
    2. imports your layouts from `vnc-mirror.sh` if you don't have a config yet,
    3. installs the launch agent (in shadow mode while vnc-mirror is active),
    4. runs `mirrormax doctor` and `mirrormax status`.

    It never runs `mirrormax migrate`, so vnc-mirror stays in charge until you decide otherwise. It's safe to run again.

`uv tool install` puts the `mirrormax` command in `~/.local/bin`. If your shell can't find it, run:

```sh
uv tool update-shell
```

and open a new terminal.

!!! note "Reinstall the agent after upgrading"

    The launch agent's plist points at the installed `mirrormax` binary. After every upgrade or reinstall, run `mirrormax agent install` so launchd picks up the new build.

## Shell completion

mirrormax can install tab completion for your shell:

```sh
mirrormax --install-completion
```

## Next step

[First-time setup](setup.md): capture your layouts and check everything with `doctor`.
