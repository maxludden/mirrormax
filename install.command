#!/bin/zsh
# install.command: installs mirrormax and sets it up next to vnc-mirror (shadow mode).
# Safe to run again. It does NOT run `mirrormax migrate`; vnc-mirror stays in charge.
set -e
trap 'print -P "\n%F{red}✗ Install stopped (see the error above).%f Press any key to close."; read -k1' ERR
cd "${0:A:h}"

print -P "%F{cyan}→ Installing mirrormax with uv…%f"
if ! command -v uv >/dev/null; then
  print -P "%F{red}✗ uv not found.%f Install it with: brew install uv"; read -k1; exit 1
fi
uv tool install . --reinstall
export PATH="$HOME/.local/bin:$PATH"
mirrormax --version

if [[ -f "$HOME/.config/mirrormax/config.toml" ]]; then
  print -P "%F{cyan}→ Config already exists; keeping it.%f"
else
  print -P "%F{cyan}→ Importing your layouts from vnc-mirror.sh…%f"
  mirrormax init --import-legacy --yes
fi

print -P "%F{cyan}→ Installing the launch agent (shadow mode while vnc-mirror is active)…%f"
mirrormax agent install

print -P "%F{cyan}→ Health check…%f"
mirrormax doctor || true
print
mirrormax status || true

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]] || ! grep -qs '.local/bin' "${ZDOTDIR:-$HOME}/.zshrc" "${ZDOTDIR:-$HOME}/.zshenv" 2>/dev/null; then
  print -P "\n%F{yellow}⚠ If 'mirrormax' isn't found in new terminals, run: uv tool update-shell%f"
fi
print -P "\n%F{green}✓ Done.%f Press any key to close."
read -k1
