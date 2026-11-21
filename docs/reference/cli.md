---
icon: lucide/terminal
---

# CLI reference

<!--
  GENERATED FILE - do not edit by hand.
  Regenerate with: uv run python scripts/gen_cli_reference.py
-->

Every command, argument and option, generated from `mirrormax --help`.
Global options go **before** the command name (`mirrormax -n mirror`); command
options go after it (`mirrormax mirror --dry-run`).

## `mirrormax`

**mirrormax** mirrors your external display onto the MacBook screen while a RealVNC session is active, and puts your previous layout back when it ends.

```console
$ mirrormax [OPTIONS] COMMAND [ARGS]...
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--config`, `-c` | `<path>` | Use this config file. |  |
| `--dry-run`, `-n` |  | Change nothing; print commands. |  |
| `--verbose`, `-v` |  | Debug output and full tracebacks. |  |
| `--no-color` |  | Disable colored output. |  |
| `--yes`, `-y` |  | Answer yes to every confirmation. |  |
| `--version` |  | Show the version and exit. |  |
| `--install-completion` |  | Install completion for the current shell. |  |
| `--show-completion` |  | Show completion for the current shell, to copy it or customize the installation. |  |
| `--help` |  | Show this message and exit. |  |

Start with `mirrormax init`, then `mirrormax doctor`. Docs: https://maxludden.github.io/mirrormax/

### `mirrormax init`

Guided setup: capture your normal and mirror layouts.

```console
$ mirrormax init [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--import-legacy` |  | Read MIRROR_ARGS/NORMAL_ARGS from the old script. |  |
| `--yes`, `-y` |  | Don't ask for confirmation. |  |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax init  # guided capture of both layouts
$ mirrormax init --import-legacy  # reuse the layouts from vnc-mirror.sh
```

### `mirrormax migrate`

Take over from the old **vnc-mirror** automation (safely, reversibly).

```console
$ mirrormax migrate [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--rollback` |  | Undo: give control back to vnc-mirror. |  |
| `--mode` | `<watch|poll>` | Agent mode (default: from config). |  |
| `--dry-run`, `-n` |  | Show what would happen; change nothing. |  |
| `--yes`, `-y` |  | Don't ask for confirmation. |  |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax migrate --dry-run  # preview the handover
$ mirrormax migrate  # take over from vnc-mirror
$ mirrormax migrate --rollback  # give control back to vnc-mirror
```

### `mirrormax mirror`

Mirror displays now (saves the current layout to restore later).

```console
$ mirrormax mirror [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--dry-run`, `-n` |  | Show what would happen; change nothing. |  |
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax mirror --dry-run
```

### `mirrormax restore`

Restore the layout from before mirroring (or your normal layout).

```console
$ mirrormax restore [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--dry-run`, `-n` |  | Show what would happen; change nothing. |  |
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax restore
```

### `mirrormax check`

Run one detection pass (what the agent runs in **poll** mode).

```console
$ mirrormax check [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--dry-run`, `-n` |  | Show what would happen; change nothing. |  |
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

Exit codes: **0** no change · **1** error · **10** mirrored · **11** restored

**Example**

```console
$ mirrormax check -n
```

### `mirrormax watch`

Follow RealVNC's log and switch instantly (the default agent mode).

```console
$ mirrormax watch [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax watch  # runs until Ctrl-C; the agent runs this in the background
```

### `mirrormax status`

Show the session, display mode, agent and any warnings.

```console
$ mirrormax status [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax status
$ mirrormax status --json | jq .mode
```

### `mirrormax doctor`

Check everything mirrormax depends on and explain how to fix problems.

```console
$ mirrormax doctor [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax doctor
```

### `mirrormax logs`

Show (or follow) mirrormax's own log.

```console
$ mirrormax logs [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--follow`, `-f` |  | Keep printing new lines. |  |
| `--lines`, `-n` | `<int>` | How many recent lines. | `40` |
| `--level` | `<str>` | Minimum level, e.g. info, warning. | `debug` |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax logs -f
$ mirrormax logs --level warning -n 200
```

### `mirrormax layout`

Save, inspect and apply named display layouts.

```console
$ mirrormax layout [OPTIONS] COMMAND [ARGS]...
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--help` |  | Show this message and exit. |  |

#### `mirrormax layout save`

Save the current display arrangement under NAME.

```console
$ mirrormax layout save [OPTIONS] NAME
```

**Arguments**

| Argument | Value | Description | Required |
|---|---|---|---|
| `NAME` | `<str>` | Layout name, e.g. normal or mirror. | yes |

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--yes`, `-y` |  | Don't ask for confirmation. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax layout save normal
```

#### `mirrormax layout list`

List saved layouts and whether their displays are connected.

```console
$ mirrormax layout list [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax layout list --json
```

#### `mirrormax layout show`

Show a layout's screens and a map of the arrangement.

```console
$ mirrormax layout show [OPTIONS] NAME
```

**Arguments**

| Argument | Value | Description | Required |
|---|---|---|---|
| `NAME` | `<str>` | Layout name, or `current` for the live arrangement. | yes |

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax layout show normal
$ mirrormax layout show current  # what's applied right now
```

#### `mirrormax layout apply`

Apply a saved layout now (doesn't change mirrormax's mode).

```console
$ mirrormax layout apply [OPTIONS] NAME
```

**Arguments**

| Argument | Value | Description | Required |
|---|---|---|---|
| `NAME` | `<str>` | Layout to apply. | yes |

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--force` |  | Skip the connected-displays check. |  |
| `--dry-run`, `-n` |  | Show what would happen; change nothing. |  |
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax layout apply normal --dry-run
```

#### `mirrormax layout delete`

Delete a saved layout.

```console
$ mirrormax layout delete [OPTIONS] NAME
```

**Arguments**

| Argument | Value | Description | Required |
|---|---|---|---|
| `NAME` | `<str>` | Layout to delete. | yes |

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--yes`, `-y` |  | Don't ask for confirmation. |  |
| `--help` |  | Show this message and exit. |  |

**Example**

```console
$ mirrormax layout delete old-dock
```

### `mirrormax agent`

Install and control the launchd agent that runs mirrormax in the background.

```console
$ mirrormax agent [OPTIONS] COMMAND [ARGS]...
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--help` |  | Show this message and exit. |  |

#### `mirrormax agent install`

Write the launch agent plist and start it.

```console
$ mirrormax agent install [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--mode` | `<watch|poll>` | watch (default) or poll. |  |
| `--help` |  | Show this message and exit. |  |

**Examples**

```console
$ mirrormax agent install
$ mirrormax agent install --mode poll  # fallback: check every few seconds
```

#### `mirrormax agent uninstall`

Stop the agent and remove its plist.

```console
$ mirrormax agent uninstall [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--yes`, `-y` |  | Don't ask for confirmation. |  |
| `--help` |  | Show this message and exit. |  |

#### `mirrormax agent start`

Start (or restart) the agent.

```console
$ mirrormax agent start [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--help` |  | Show this message and exit. |  |

#### `mirrormax agent stop`

Stop the agent until the next login (use uninstall to remove it).

```console
$ mirrormax agent stop [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--help` |  | Show this message and exit. |  |

#### `mirrormax agent status`

Show what launchd knows about the agent.

```console
$ mirrormax agent status [OPTIONS]
```

**Options**

| Option | Value | Description | Default |
|---|---|---|---|
| `--json` |  | Print plain JSON (stable schema) instead of tables. |  |
| `--help` |  | Show this message and exit. |  |
