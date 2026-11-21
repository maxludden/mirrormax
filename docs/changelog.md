---
icon: lucide/history
---

# Changelog

All notable changes to mirrormax are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and mirrormax uses [semantic versioning](https://semver.org/).

## Unreleased

### Added

- Documentation site built with Zensical and published to GitHub Pages.
- Generated CLI reference (`scripts/gen_cli_reference.py`), checked in CI.
- GitHub Actions workflows for CI, docs deployment and PyPI releases (trusted publishing).

## 0.1.0

### Added

- First release: the `mirrormax` CLI, replacing `vnc-mirror.sh`.
- Session detection from RealVNC Server's log, with a kqueue watcher and a 60-second heartbeat.
- Snapshot-and-restore of the previous layout, with drift checks.
- Named layouts (`layout save|list|show|apply|delete`).
- launchd agent management in watch or poll mode.
- Shadow mode, `migrate` and `migrate --rollback` for a safe handover from vnc-mirror.
- `status`, `doctor` and `logs` diagnostics, with `--json` output for scripting.
