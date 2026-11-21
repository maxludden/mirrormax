---
icon: lucide/braces
---

# Exit codes and JSON

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (for `check`: no change was needed, or a switch was skipped) |
| `1` | Error: mirrormax printed an explanation and a suggested fix |
| `10` | `check` only: displays were mirrored |
| `11` | `check` only: the previous layout was restored |
| `130` | Interrupted with Ctrl-C |

The `10`/`11` codes let a wrapper script react to a switch:

```sh
mirrormax check
case $? in
  10) echo "now mirrored" ;;
  11) echo "back to normal" ;;
esac
```

## JSON output

Commands that accept `--json` print **only** JSON on stdout, so it's safe to pipe into `jq`:

```sh
mirrormax status --json | jq .mode
mirrormax layout list --json | jq '.layouts[].name'
```

Every document has a `"schema"` key of the form `mirrormax.<kind>/1`. Within a schema version, fields are only ever **added**, never renamed or removed.

| Schema | Produced by |
|---|---|
| `mirrormax.status/1` | `status --json` |
| `mirrormax.doctor/1` | `doctor --json` (`ok`, `checks`) |
| `mirrormax.outcome/1` | `mirror`, `restore`, `check`, `layout apply` with `--json` |
| `mirrormax.layouts/1` | `layout list --json` (`layouts`, `connected`) |
| `mirrormax.layout/1` | `layout show NAME --json` (`layout`, `connected`, `missing`) |
| `mirrormax.agent/1` | `agent status --json` |
| `mirrormax.error/1` | any `--json` command that fails (`error`, `cause`, `fix`) |

### Outcome documents

`mirrormax.outcome/1` describes what a switching command did:

| Field | Type | Meaning |
|---|---|---|
| `action` | string | `none`, `mirrored`, `restored`, `skipped` (drift or missing layout), `shadow` (legacy agent owns the displays) or `applied` (a named layout applied by hand) |
| `message` | string | One-line human summary |
| `mode` | string | `normal` or `mirrored`, after the operation |
| `session` | string or null | `active`, `ended` or `unknown`; `null` for manual operations |
| `layout` | string or null | The layout applied, or that would be applied |
| `command` | list or null | The `displayplacer` command (always set for dry runs) |
| `duration` | number or null | Seconds the switch took |
| `warning` | string or null | A warning, if any |
| `dry_run` | boolean | Whether this was a dry run |
| `detection_reason` | string | How the session state was determined (empty for manual operations) |
| `marker_time` | string or null | ISO 8601 timestamp of the log marker behind the session state |

An illustrative example (values shortened):

```json
{
  "schema": "mirrormax.outcome/1",
  "action": "mirrored",
  "message": "…",
  "mode": "mirrored",
  "session": "active",
  "layout": "mirror",
  "command": ["displayplacer", "id:… res:1470x956 …"],
  "duration": 1.42,
  "warning": null,
  "dry_run": false,
  "detection_reason": "…",
  "marker_time": "2026-11-19T14:02:11"
}
```

### Error documents

Errors carry the same three parts as the human error panel: what went wrong, why, and how to fix it. For example:

```json
{
  "schema": "mirrormax.error/1",
  "error": "Can't read /Library/Logs/rvncserver.log",
  "cause": "…",
  "fix": "…"
}
```
