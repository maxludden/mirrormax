---
icon: lucide/house
---

# mirrormax

**Mirror your external display onto the MacBook screen while a RealVNC session is active, and put your previous layout back the moment it ends.**

```text
╭─ mirrormax ──────────────────────────────────────────╮
│ ● Mirrored · VNC session active since 14:02 (23 min) │
╰──────────────────────────────────────────────────────╯
Session      active
Displays     ● mirrored · previous layout saved
Agent        ✓ watch mode · pid 812
Last switch  mirrored at 14:02 · 23 min ago
Last check   14:24 · just now
Legacy       inactive
Config       /Users/max/.config/mirrormax/config.toml
RealVNC log  /Library/Logs/rvncserver.log
```

When you connect to your Mac from an iPad (or anything else) with RealVNC, the remote screen shows the MacBook's built-in display. If your work is on an external monitor, you can't see it. mirrormax watches for VNC sessions, mirrors your displays while one is active, and restores **whatever layout you had before** once it ends.

<div class="grid cards" markdown>

-   :lucide-zap: **Instant and idle-cheap**

    ---

    A background watcher sleeps in kqueue on RealVNC's log and wakes the moment a session starts or stops. It uses no CPU while idle.

-   :lucide-undo-2: **Restores *your* layout**

    ---

    It saves a snapshot right before mirroring and puts it back afterwards, rather than switching to a fixed layout.

-   :lucide-shield-check: **Refuses to guess**

    ---

    It switches at most once per connect or disconnect. If it can't tell what's going on, or a display is missing, it changes nothing and tells you why.

-   :lucide-stethoscope: **Explains itself**

    ---

    `status` and `doctor` show what's happening, and `doctor` prints a fix for every problem it finds. Every command has `--json` output.

</div>

## Quick start

=== "From PyPI"

    ```sh
    brew install displayplacer
    uv tool install mirrormax
    mirrormax init          # capture your normal and mirror layouts
    mirrormax doctor        # check everything is in place
    mirrormax agent install # start switching automatically
    ```

=== "From a clone"

    ```sh
    brew install displayplacer
    git clone https://github.com/maxludden/mirrormax.git
    cd mirrormax
    uv tool install .
    mirrormax init && mirrormax doctor && mirrormax agent install
    ```

Then connect from your iPad: the displays mirror within a second or two, and your old layout comes back when you disconnect.

!!! tip "Coming from `vnc-mirror.sh`?"

    Don't run both tools at once. Read [Migrating from vnc-mirror](getting-started/migrating.md) first: mirrormax runs in a safe *shadow mode* until you hand control over.

## Where next

- [Installation](getting-started/installation.md): requirements and install options.
- [How it works](guide/how-it-works.md): detection, switching and the safety checks.
- [CLI reference](reference/cli.md): every command and option.
- [Troubleshooting](guide/troubleshooting.md): when the displays don't switch.
