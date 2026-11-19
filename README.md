# mirrormax

Mirror your external display onto the MacBook screen while a **RealVNC** session is active, and put your previous layout back the moment it ends.

```
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

It replaces the old `vnc-mirror.sh` + launch agent with a proper CLI: it restores *whatever layout you had* (not a fixed one), switches at most once per connect/disconnect, refuses to touch displays when it can't tell what's going on, and explains itself in color.

## How it works

- **Detection reads RealVNC's log, not a port.** iPad connections go through RealVNC's cloud relay (UDP), so a TCP 5900 check never sees them. RealVNC Server writes `SServerAgent: Starting desktop` / `Stopping desktop` to `/Library/Logs/rvncserver.log`; whichever came last is the current state. No marker (e.g. right after a log rotation) means *unknown*, and nothing changes.
- **On connect:** snapshot the current layout (`displayplacer list`), then apply your `mirror` layout.
- **On disconnect:** apply the snapshot, or your saved `normal` layout if the snapshot is missing or refers to displays that are gone.
- **Drift check:** a layout is only applied if every display it names is connected. Otherwise the switch is skipped and `status` tells you why.
- **Watch mode (default):** a background process sleeps in kqueue on the log file and wakes the instant RealVNC writes to it, at zero CPU while idle. It also re-checks every 60 s (the heartbeat), so a missed event or a sleep/wake can't leave you stuck.

### Why `watch` is the default (and not polling)

The old agent spawned a tiny zsh script every 5 s. Spawning a Python CLI (Typer, Rich and loguru imports) every 5 s costs much more CPU per run, so a long-running watcher is cheaper. To see the difference on your Mac:

```sh
brew install hyperfine
hyperfine 'mirrormax check'
```

kqueue (`EVFILT_VNODE`, Python's stdlib `select`) is Apple's recommended way to watch a *single file*; FSEvents-based libraries (`watchdog`, `watchfiles`) are built for directory trees and add latency and dependencies. launchd's `WatchPaths` was rejected because `launchd.plist(5)` calls it race-prone and it has a history of silently stopping. If `watch` ever misbehaves, `mirrormax agent install --mode poll` gives you the old every-5-seconds behaviour.

## Install

Requires macOS, [`uv`](https://docs.astral.sh/uv/) and `displayplacer`.

```sh
brew install displayplacer
cd ~/dev/py/mirrormax
uv tool install .          # puts `mirrormax` on your PATH (~/.local/bin)
mirrormax --version
```

After pulling changes: `uv tool install . --reinstall`, then `mirrormax agent install` so the agent points at the new build.

## Set up

**Option A: reuse the layouts from `vnc-mirror.sh`**

```sh
mirrormax init --import-legacy
```

**Option B: capture fresh layouts (guided)**

```sh
mirrormax init
```

`init` captures your current arrangement as `normal`, offers to press ⌘F1 for you to turn on mirroring (needs Accessibility permission for your terminal), captures that as `mirror`, restores `normal`, and writes `~/.config/mirrormax/config.toml`.

Then check everything:

```sh
mirrormax doctor
```

## Migrating from vnc-mirror

The two tools must never both switch displays: the old script restores a fixed layout and has no lock, so they would race on every disconnect. mirrormax enforces that: **while the old agent is loaded (or enabled at login), mirrormax runs in shadow mode**, detecting and logging what it *would* do without touching anything.

1. **Shadow run (optional, recommended).** Install mirrormax next to the old agent and watch a few sessions:

   ```sh
   mirrormax agent install
   mirrormax status          # ◐ Shadow mode · …
   mirrormax logs -f         # "Would mirror (shadow mode: legacy agent loaded)"
   ```

2. **Hand over.** Preview, then migrate:

   ```sh
   mirrormax migrate --dry-run
   mirrormax migrate
   ```

   This imports the old layouts (if not already saved), adopts the old "mirrored" marker if your displays are mirrored right now, boots out the old agent and runs `launchctl disable` on it (so it stays off at the next login even though its plist is still there), then installs and starts `com.maxludden.mirrormax`. **Nothing is deleted.**

3. **Changed your mind?** `mirrormax migrate --rollback` re-enables and starts the old agent, disables mirrormax, and hands the "mirrored" state back.

4. **Deleting the old files.** `mirrormax doctor` has a *Delete legacy files?* row. It turns green once you've migrated, the old agent is disabled, and mirrormax has handled at least one clean connect/disconnect cycle. Then (and only then) you can remove `~/dev/scripts/vnc-mirror.sh` and `~/Library/LaunchAgents/com.maxludden.vnc-mirror.plist`.

## Commands

| Group | Command | What it does |
|---|---|---|
| Setup | `init [--import-legacy]` | Capture the `normal` and `mirror` layouts |
| | `migrate [--dry-run] [--rollback] [--mode watch\|poll]` | Take over from (or hand back to) vnc-mirror |
| Switching | `mirror` / `restore` | Switch by hand (`restore` uses the pre-mirror snapshot) |
| | `layout save\|list\|show\|apply\|delete NAME` | Manage named layouts; `layout show current` shows the live arrangement with a map |
| Automation | `watch` | Follow the log (what the agent runs); live status line in a terminal |
| | `check` | One detection pass. Exit codes: 0 no change, 1 error, 10 mirrored, 11 restored |
| | `agent install [--mode watch\|poll]` / `uninstall` / `start` / `stop` / `status` | Manage the launchd agent |
| Diagnostics | `status [--json]` | Session, mode, agent, last switch, warnings |
| | `doctor [--json]` | Check every dependency and print a fix for each problem |
| | `logs [-f] [-n N] [--level LEVEL]` | Show or follow mirrormax's log |

Global flags (before the command): `--config PATH`, `--dry-run`/`-n`, `--verbose`/`-v`, `--no-color`, `--yes`/`-y`. Many commands also accept `--dry-run`, `--yes` and `--json` after the command name. `mirrormax` with no command shows `status`.

### Output and color

Human output uses one consistent theme: `✓` green for OK, `⚠` yellow for warnings, `✗` red for errors, `●` magenta for mirrored, `○` green for normal, `◐` yellow for shadow mode. Every status carries a symbol and a word, so it still reads without color. Color is automatic in a terminal and off when piped; `NO_COLOR=1` or `--no-color` turns it off and `FORCE_COLOR=1` forces it on. `--json` prints only JSON with a `"schema"` key (e.g. `mirrormax.status/1`), so it's safe to pipe into `jq`.

## Files

| Path | What |
|---|---|
| `~/.config/mirrormax/config.toml` | Layouts, log path, detection mode, heartbeat, displayplacer path |
| `~/.local/state/mirrormax/state.json` | Mode, pre-mirror snapshot, log cursor, last switch, migration record (written atomically) |
| `~/.local/state/mirrormax/mirrormax.lock` | Stops two mirrormax processes from switching at once |
| `~/Library/Logs/mirrormax/mirrormax.log` | mirrormax's log (rotated at 1 MB, 5 kept; visible in Console.app) |
| `~/Library/Logs/mirrormax/agent.out` / `agent.err` | The agent's stdout/stderr |
| `~/Library/LaunchAgents/com.maxludden.mirrormax.plist` | The launch agent |

Example config:

```toml
[detection]
mode = "watch"  # "watch" (default) or "poll"
log_path = "/Library/Logs/rvncserver.log"
heartbeat_seconds = 60
poll_interval = 5

[displayplacer]
path = "/opt/homebrew/bin/displayplacer"

[legacy]
script = "~/dev/scripts/vnc-mirror.sh"
state_marker = "~/.vnc-mirror-state"

[layouts.mirror]
args = [
    "id:37D8832A-…+6B26DAE3-… res:1470x956 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0",
]

[layouts.normal]
args = [
    "id:37D8832A-… res:1280x832 hz:60 color_depth:8 enabled:true scaling:on origin:(0,0) degree:0",
    "id:6B26DAE3-… res:1920x1080 hz:60 color_depth:8 enabled:true scaling:off origin:(-359,-1080) degree:0",
]
```

## Troubleshooting

**"Can't read /Library/Logs/rvncserver.log"**: macOS privacy protection. System Settings → Privacy & Security → Full Disk Access → add your terminal app and the mirrormax binary (`which mirrormax`, usually `~/.local/bin/mirrormax`). The log has been world-readable on this Mac so far, so you'll probably never see this.

**`launchctl` errors.** mirrormax never uses `load`/`unload` (they failed here with "Load failed: 5: Input/output error"). It uses `bootstrap`/`bootout`/`enable`/`disable`/`kickstart`. If the agent is stuck:

```sh
mirrormax agent status
mirrormax agent install          # rewrites the plist, boots out, re-bootstraps
plutil -lint ~/Library/LaunchAgents/com.maxludden.mirrormax.plist
launchctl print gui/$UID/com.maxludden.mirrormax
```

**Displays didn't switch.** Run `mirrormax status`:
- *Shadow mode*: the old agent is still active → `mirrormax migrate`.
- *Display drift*: a layout names a display that isn't connected (different dock, monitor off) → `mirrormax layout list`, then `mirrormax layout save normal` / `mirror` with the current hardware.
- *Session unknown*: RealVNC hasn't written a marker since its log rotated; it will after the next connect.

**Watch mode misbehaving?** Switch to polling: `mirrormax agent install --mode poll` (or set `mode = "poll"` in the config and reinstall).

**More detail:** `mirrormax -v <command>` prints debug output and full tracebacks; `mirrormax logs -f` follows the log.

## Manual test checklist

1. ☐ `mirrormax doctor`: no ✗ rows.
2. ☐ Shadow run: with the old agent still running, `mirrormax agent install`; connect from the iPad; `mirrormax logs` shows "Would mirror"; `mirrormax status` shows ◐ Shadow mode. Disconnect.
3. ☐ `mirrormax migrate --dry-run`, then `mirrormax migrate`.
4. ☐ Connect from the iPad: displays mirror within a second or two; `status` shows ● Mirrored.
5. ☐ Rearrange nothing; disconnect: your previous layout is back; `status` shows ○ Normal.
6. ☐ Reboot (or log out and in): `launchctl print gui/$UID/com.maxludden.vnc-mirror` fails (not loaded), `mirrormax agent status` shows loaded with a pid.
7. ☐ Look at `mirrormax status` and `mirrormax doctor` in both a light and a dark terminal theme; everything should be readable.
8. ☐ When `doctor` says *safe to delete*, remove the legacy script and plist yourself.

## Development

```sh
uv sync
uv run pytest                 # 70+ tests, no real displays or launchd needed
uv run pytest -m macos        # also exercise the real kqueue watcher (macOS only)
uv run mypy                   # strict, src and tests
uv run ruff check && uv run ruff format --check
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_ui_cli.py   # accept intentional UI changes
```

Layout: `detector` (log → session state), `layouts` (displayplacer args), `displays` (backend protocol + displayplacer), `state` (atomic JSON + lock), `controller` (state machine), `watcher` (kqueue), `agent` (launchd), `legacy` (shadow mode, migrate, rollback), `doctor`, `status`, `ui` (Rich theme and renderers), `jsonout`, `cli` (Typer, thin).
