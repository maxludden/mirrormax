---
icon: lucide/life-buoy
---

# Troubleshooting

Start with these two commands. Between them they usually say exactly what's wrong:

```sh
mirrormax status
mirrormax doctor
```

## The displays didn't switch

Run `mirrormax status` and look at the headline:

`◐ Shadow mode`
:   The old vnc-mirror agent is still loaded or enabled, so mirrormax is only observing. Hand over with `mirrormax migrate` (see [Migrating](../getting-started/migrating.md)).

*Display drift*
:   A layout names a display that isn't connected (a different dock, or a monitor that's off). Check with `mirrormax layout list`, then re-save with your current hardware: `mirrormax layout save normal` / `mirrormax layout save mirror`.

*Session unknown*
:   RealVNC hasn't written a start/stop marker since its log rotated. It will after the next connect.

## "Can't read /Library/Logs/rvncserver.log"

This is macOS privacy protection. Open **System Settings → Privacy & Security → Full Disk Access** and add:

- your terminal app, and
- the mirrormax binary (`which mirrormax`, usually `~/.local/bin/mirrormax`).

The log is normally world-readable, so you'll probably never see this.

## `launchctl` errors

mirrormax never uses `launchctl load`/`unload` (they fail on recent macOS with errors like *Load failed: 5: Input/output error*). It uses `bootstrap`, `bootout`, `enable`, `disable` and `kickstart`. If the agent is stuck:

```sh
mirrormax agent status
mirrormax agent install          # rewrites the plist, boots out, re-bootstraps
plutil -lint ~/Library/LaunchAgents/com.maxludden.mirrormax.plist
launchctl print gui/$UID/com.maxludden.mirrormax
```

## Watch mode misbehaving

Switch to polling:

```sh
mirrormax agent install --mode poll
```

or set `mode = "poll"` under `[detection]` in the config and run `mirrormax agent install`.

## After upgrading, the agent runs the old version

The plist points at the installed binary. Re-run:

```sh
mirrormax agent install
```

## More detail

```sh
mirrormax -v <command>     # debug output and full tracebacks
mirrormax logs -f          # follow mirrormax's log
cat ~/Library/Logs/mirrormax/agent.err
```

## Manual test checklist

Use this after installing or upgrading:

- [ ] `mirrormax doctor` shows no `✗` rows.
- [ ] **Shadow run:** with the old agent still running, `mirrormax agent install`; connect from the iPad; `mirrormax logs` shows "Would mirror"; `mirrormax status` shows `◐ Shadow mode`. Disconnect.
- [ ] `mirrormax migrate --dry-run`, then `mirrormax migrate`.
- [ ] Connect from the iPad: the displays mirror within a second or two; `status` shows `● Mirrored`.
- [ ] Rearrange nothing; disconnect: your previous layout is back; `status` shows `○ Normal`.
- [ ] Reboot (or log out and in): `launchctl print gui/$UID/com.maxludden.vnc-mirror` fails (not loaded), and `mirrormax agent status` shows the agent loaded with a pid.
- [ ] `mirrormax status` and `mirrormax doctor` are readable in both a light and a dark terminal theme.
- [ ] When `doctor` says *safe to delete*, remove the legacy script and plist yourself.
