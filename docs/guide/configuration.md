---
icon: lucide/file-cog
---

# Configuration and files

## Config file

mirrormax reads `~/.config/mirrormax/config.toml`. `mirrormax init` writes it for you; edit it by hand to change detection settings. Use `--config PATH` to point at a different file.

```toml title="~/.config/mirrormax/config.toml"
[detection]
mode = "watch"                          # "watch" (default) or "poll"
log_path = "/Library/Logs/rvncserver.log"
heartbeat_seconds = 60                  # watch mode: re-check this often regardless
poll_interval = 5                       # poll mode: seconds between checks

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

### `[detection]`

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"watch"` | How the agent runs: `watch` (kqueue, instant) or `poll` (launchd runs `check` on a timer) |
| `log_path` | `/Library/Logs/rvncserver.log` | RealVNC Server's log |
| `heartbeat_seconds` | `60` | In watch mode, re-check at least this often |
| `poll_interval` | `5` | In poll mode, seconds between checks |

After changing `mode`, run `mirrormax agent install` so the plist matches.

### `[displayplacer]`

| Key | Meaning |
|---|---|
| `path` | Full path to the `displayplacer` binary |

### `[legacy]`

Where the old vnc-mirror files live. Used by `init --import-legacy`, shadow mode, `migrate` and `doctor`.

### `[layouts.NAME]`

Each layout is a list of `displayplacer` screen arguments, exactly as `displayplacer list` prints them. Manage them with `mirrormax layout …` rather than by hand. `normal` and `mirror` are required; you can add as many others as you like.

## Files mirrormax uses

| Path | What |
|---|---|
| `~/.config/mirrormax/config.toml` | Layouts, log path, detection mode, heartbeat, displayplacer path |
| `~/.local/state/mirrormax/state.json` | Mode, pre-mirror snapshot, log cursor, last switch, migration record (written atomically) |
| `~/.local/state/mirrormax/mirrormax.lock` | Stops two mirrormax processes from switching at once |
| `~/Library/Logs/mirrormax/mirrormax.log` | mirrormax's log (rotated at 1 MB, 5 kept; visible in Console.app) |
| `~/Library/Logs/mirrormax/agent.out` / `agent.err` | The agent's stdout and stderr |
| `~/Library/LaunchAgents/com.maxludden.mirrormax.plist` | The launch agent |

## Environment variables

| Variable | Effect |
|---|---|
| `NO_COLOR=1` | Disable colored output |
| `FORCE_COLOR=1` | Force colored output, even when piped |
