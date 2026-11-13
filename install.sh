#!/usr/bin/env bash
# UniVault installer — copies CLIs and the shared lib into ~/.local.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
case "$PREFIX" in
  /*) ;;
  *) say "error: PREFIX must be an absolute path" >&2; exit 1 ;;
esac
if [[ "$PREFIX" == "/" ]]; then
  say "error: refusing to install into /" >&2
  exit 1
fi
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/univault"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

say() { printf '%s\n' "$*"; }

say "UniVault installer"
say "  source: $ROOT"
say "  prefix: $PREFIX"
[[ "$DRY_RUN" == "1" ]] && say "  mode:   dry-run (nothing will be written)"

if [[ ! -d "$ROOT/scripts/lib" ]]; then
  say "error: scripts/lib not found under $ROOT" >&2
  exit 1
fi

if [[ "$DRY_RUN" == "0" ]]; then
  mkdir -p "$BIN_DIR" "$LIB_DIR"
fi

for name in ed-archive ed-lessons moodle-setup moodle-archive univault-normalize univault; do
  src="$ROOT/scripts/$name"
  if [[ ! -f "$src" ]]; then
    say "  skip (missing): $name"
    continue
  fi
  say "  install: $BIN_DIR/$name"
  if [[ "$DRY_RUN" == "0" ]]; then
    install -m 755 "$src" "$BIN_DIR/$name"
  fi
done

say "  install: $LIB_DIR/lib/"
if [[ "$DRY_RUN" == "0" ]]; then
  rm -rf -- "$LIB_DIR/lib"
  mkdir -p "$LIB_DIR"
  cp -R "$ROOT/scripts/lib" "$LIB_DIR/lib"
  chmod -R u+rwX,go+rX "$LIB_DIR/lib"
fi

say ""
say "Done. Ensure $BIN_DIR is on PATH, e.g. add to your shell profile:"
say "  export PATH=\"$BIN_DIR:\$PATH\""
say ""
say "Next steps:"
say "  univault --help"
say "  univault login          # one-time Moodle/Okta login (optional: use moodle-setup import-cookie)"
say "  univault sync --dry-run # plan; then remove --dry-run to archive"
say ""
say "ZCode plugin: add this repository as a local marketplace, then install UniVault."
