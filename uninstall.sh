#!/usr/bin/env bash
# UniVault uninstaller — removes installed CLIs and libs. User config is kept.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
case "$PREFIX" in
  /*) ;;
  *) say "error: PREFIX must be an absolute path" >&2; exit 1 ;;
esac
if [[ "$PREFIX" == "/" ]]; then
  say "error: refusing to operate on /" >&2
  exit 1
fi
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/univault"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

say() { printf '%s\n' "$*"; }

say "UniVault uninstaller"
say "  prefix: $PREFIX"
[[ "$DRY_RUN" == "1" ]] && say "  mode:   dry-run (nothing will be removed)"

for name in ed-archive ed-lessons moodle-setup moodle-archive univault-normalize univault; do
  target="$BIN_DIR/$name"
  if [[ -e "$target" ]]; then
    say "  remove: $target"
    [[ "$DRY_RUN" == "0" ]] && rm -f "$target"
  fi
done

if [[ -d "$LIB_DIR" ]]; then
  say "  remove: $LIB_DIR"
  [[ "$DRY_RUN" == "0" ]] && rm -rf "$LIB_DIR"
fi

say ""
say "Kept: ~/.config/univault/ (session cookie + config)."
say "Remove it manually if you no longer want UniVault on this machine."
