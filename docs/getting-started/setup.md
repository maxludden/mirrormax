---
icon: lucide/settings-2
---

# First-time setup

mirrormax needs two saved layouts:

- **`normal`**: your everyday arrangement. It's used when the pre-mirror snapshot is missing or refers to displays that are no longer connected.
- **`mirror`**: the arrangement to use during a VNC session, with the external display mirrored onto the built-in one.

## Capture the layouts

=== "Guided capture"

    ```sh
    mirrormax init
    ```

    `init`:

    1. captures your current arrangement as `normal`,
    2. offers to press ⌘F1 to turn on mirroring (this needs Accessibility permission for your terminal; you can also turn on mirroring yourself),
    3. captures the mirrored arrangement as `mirror`,
    4. restores `normal`,
    5. writes `~/.config/mirrormax/config.toml`.

=== "Import from vnc-mirror.sh"

    If you used the old `vnc-mirror.sh` script, reuse its `MIRROR_ARGS` and `NORMAL_ARGS`:

    ```sh
    mirrormax init --import-legacy
    ```

You can re-capture either layout at any time, for example after buying a new monitor:

```sh
mirrormax layout save normal
mirrormax layout save mirror
```

## Check everything

```sh
mirrormax doctor
```

`doctor` checks displayplacer, the RealVNC log, your config and layouts, the launch agent, and the legacy setup. Every problem comes with the command that fixes it. Aim for no `✗` rows.

## Start the agent

```sh
mirrormax agent install
```

This writes `~/Library/LaunchAgents/com.maxludden.mirrormax.plist` and starts the agent in **watch** mode. It starts again automatically at every login.

```sh
mirrormax status
```

should now show `○ Normal` and the agent as `✓ watch mode · pid …`.

## Try it

1. Connect to your Mac from the iPad. Within a second or two the displays mirror and `mirrormax status` shows `● Mirrored`.
2. Disconnect. Your previous layout comes back and `status` shows `○ Normal`.

If nothing happens, see [Troubleshooting](../guide/troubleshooting.md).
