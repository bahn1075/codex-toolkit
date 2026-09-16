#!/usr/bin/env bash
# macOS Bash 3.2+ and Linux. Run as the ordinary login user, outside ~/.codex.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/toolkit.sh"
case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  --resume) RESET=0 ;;
  '') RESET=1 ;;
  *) die "Unknown option: $1" ;;
esac
preflight
acquire_lock
if [ "$RESET" = 1 ]; then
  reset_codex
else
  if [ ! -f "$CT_ROOT/install-state.json" ]; then
    # Bootstrap can fail before state-init (e.g. when downloading tomlkit).
    [ -f "$CT_HOME/previous-home.txt" ] && [ -d "$CT_ROOT/venv" ] \
      || die 'No previous toolkit setup to resume.'
  fi
fi
bootstrap
install_packages
configure_core
install_mem0
configure_headroom
install_plugins
finish
