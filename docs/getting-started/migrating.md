---
icon: lucide/arrow-right-left
---

# Migrating from vnc-mirror

mirrormax replaces the old `vnc-mirror.sh` script and its `com.maxludden.vnc-mirror` launch agent.

!!! danger "Never let both tools switch displays"

    The old script restores a fixed layout and has no lock, so the two would race on every disconnect. mirrormax enforces this for you: **while the old agent is loaded, or enabled at login, mirrormax runs in shadow mode.** It detects sessions and logs what it *would* do, but doesn't touch your displays.

## 1. Shadow run (optional, recommended)

Install mirrormax next to the old agent and watch a few sessions:

```sh
mirrormax agent install
mirrormax status          # ◐ Shadow mode · …
mirrormax logs -f         # "Would mirror (shadow mode: legacy agent loaded)"
```

Connect and disconnect a few times. The log should show a *Would mirror* and a *Would restore* for each session.

## 2. Hand over

Preview the handover, then run it:

```sh
mirrormax migrate --dry-run
mirrormax migrate
```

`migrate`:

1. imports the old layouts (if you haven't saved them yet),
2. adopts the old "mirrored" marker if your displays are mirrored right now,
3. boots out the old agent and runs `launchctl disable` on it, so it stays off at the next login even though its plist is still there,
4. installs and starts `com.maxludden.mirrormax`.

**Nothing is deleted.**

## 3. Changed your mind?

```sh
mirrormax migrate --rollback
```

This re-enables and starts the old agent, disables mirrormax, and hands the "mirrored" state back.

## 4. Deleting the old files

`mirrormax doctor` has a **Delete legacy files?** row. It turns green once:

- you've migrated,
- the old agent is disabled, and
- mirrormax has handled at least one clean connect/disconnect cycle.

Then, and only then, remove the old files yourself:

```sh
rm ~/dev/scripts/vnc-mirror.sh
rm ~/Library/LaunchAgents/com.maxludden.vnc-mirror.plist
```
