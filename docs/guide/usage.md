---
icon: lucide/monitor
---

# Everyday use

Once the agent is installed you shouldn't need to do anything: connect and the displays mirror, disconnect and they come back. This page covers the commands you'll reach for when you want to look or step in.

## Commands at a glance

| Group | Command | What it does |
|---|---|---|
| Setup | `init [--import-legacy]` | Capture the `normal` and `mirror` layouts |
| | `migrate [--dry-run] [--rollback] [--mode watch|poll]` | Take over from (or hand back to) vnc-mirror |
| Switching | `mirror` / `restore` | Switch by hand (`restore` uses the pre-mirror snapshot) |
| | `layout save|list|show|apply|delete NAME` | Manage named layouts |
| Automation | `watch` | Follow the log (what the agent runs); live status line in a terminal |
| | `check` | One detection pass |
| | `agent install|uninstall|start|stop|status` | Manage the launchd agent |
| Diagnostics | `status [--json]` | Session, mode, agent, last switch, warnings |
| | `doctor [--json]` | Check every dependency and print a fix for each problem |
| | `logs [-f] [-n N] [--level LEVEL]` | Show or follow mirrormax's log |

`mirrormax` with no command shows `status`. Full details for every option are in the [CLI reference](../reference/cli.md).

## Checking on things

```sh
mirrormax status        # what's going on right now
mirrormax doctor        # is everything set up correctly?
mirrormax logs -f       # follow the log as sessions come and go
```

## Switching by hand

```sh
mirrormax mirror        # mirror now (saves the current layout first)
mirrormax restore       # put the saved layout back
```

Add `--dry-run` (or `-n`) to see the `displayplacer` command without running it.

## Managing layouts

```sh
mirrormax layout list                   # saved layouts, and whether their displays are connected
mirrormax layout show current           # the live arrangement, with a map
mirrormax layout show normal
mirrormax layout save desk-dock         # save the current arrangement under a new name
mirrormax layout apply desk-dock -n     # preview applying it
mirrormax layout delete desk-dock
```

`layout apply` doesn't change mirrormax's mode, so it won't confuse the agent.

## Controlling the agent

```sh
mirrormax agent status      # what launchd knows about it
mirrormax agent stop        # stop until the next login
mirrormax agent start       # start (or restart)
mirrormax agent install     # rewrite the plist and restart (after upgrades)
mirrormax agent uninstall   # stop and remove the plist
```

## Global options

Global options go **before** the command name:

| Option | Effect |
|---|---|
| `--config PATH`, `-c` | Use a different config file |
| `--dry-run`, `-n` | Change nothing; print commands |
| `--verbose`, `-v` | Debug output and full tracebacks |
| `--no-color` | Disable colored output |
| `--yes`, `-y` | Answer yes to every confirmation |

Many commands also accept `--dry-run`, `--yes` and `--json` after the command name.

## Output and color

Human output uses one consistent theme:

| Symbol | Color | Meaning |
|---|---|---|
| `✓` | green | OK |
| `⚠` | yellow | warning |
| `✗` | red | error |
| `●` | magenta | mirrored |
| `○` | green | normal |
| `◐` | yellow | shadow mode |

Every status carries a symbol **and** a word, so it still reads without color. Color is automatic in a terminal and off when piped. `NO_COLOR=1` or `--no-color` turns it off; `FORCE_COLOR=1` forces it on.

For scripts, use `--json`. See [Exit codes and JSON](../reference/output.md).
